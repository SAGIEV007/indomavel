"use strict";

// Indomável — editor: escolhe o bloco, ajusta o corte na trilha, vê o vídeo pronto em tempo real e exporta.

const FORMATOS = {
  "9:16": [1080, 1920],
  "3:4": [1080, 1440],
  "4:5": [1080, 1350],
  "1:1": [1080, 1080],
  "16:9": [1920, 1080],
};
const NOMES_ESTADO = { na_fila: "Na fila", baixando: "Em andamento", pronta: "Pronto", falhou: "Falhou" };
const NOMES_ORIGEM = { bloco_pronto: "Bloco pronto do Chub", dentro_de_bloco: "Trecho escolhido dentro de um bloco longo" };
const ESTADOS_LOCAIS = {
  na_fila: "Na fila", legenda: "Buscando legenda", transcrevendo: "Transcrevendo", blocos: "Dividindo em blocos",
  pronto: "Pronto", falhou: "Falhou", interrompido: "Interrompido",
  aguardando_youtube: "Processando no YouTube", sem_audio: "Sem falas/áudio",
  aguardando_retentativa: "Aguardando retentativa",
};
const ETIQUETAS = ["EM ALTA!", "VIRALIZOU!!", "MANDOU A REAL!!", "VERGONHOSO!", "URGENTE!", "ASSISTA ATÉ O FIM!"];
const CORES_LEGENDA = ["#FFFFFF", "#FFD60A", "#111111"];
const CORES_DESTAQUE = ["#FFD60A", "#FF3B30", "#2EC4B6", "#FFFFFF"];
const FONTES_CSS = { bebas: '"Bebas Neue", sans-serif', montserrat: '"Montserrat", sans-serif' };
const PESOS_CSS = { bebas: 400, montserrat: 900 };
// Campos que mudam a imagem do card/rodapé e os que mudam posições no quadro.
const CAMPOS_MOLDURA = ["card", "tag", "headline", "tamanho_tag", "tamanho_headline", "rodape", "texto_rodape"];
const CAMPOS_LAYOUT = CAMPOS_MOLDURA.concat(["tamanho_legenda", "contorno", "posicao_legenda"]);
const MARGEM_IMA_PX = 10;
const CORTE_MINIMO_S = 1;
const INTERVALO_BUSCA_MS = 120;

const estado = {
  origemLista: "chub", filtroTatico: "todos", pagina: 0, fonte: "", busca: "", tokenVideos: 0, videos: [], locais: [], playlist: [], statusPlaylist: null, modoAutoCortes: false,
  video: null, blocos: [], frases: [], ordem: "potencial", bloco: null,
  inicio: null, fim: null,
  player: null, playerPronto: false, pendente: null, duracao: 0, tocandoCorte: false,
  formato: "9:16", estilo: {}, estiloPadrao: {}, layout: null, layoutChave: "", escala: 1,
  trechos: [], trechosChave: "", ultimoTrecho: undefined,
  vista: { inicio: 0, fim: 60 }, arrasto: null, ultimaBusca: 0, buscaPendente: null, tempoVisual: null,
  headlinesPorBloco: {}, sugestoesChave: "", ultimasSugestoes: [], tarefas: [], painel: "blocos",
  automacao: null, filtroAutomatico: "para_revisar", assinaturaAutomatico: "", corteAoAbrir: null,
  versaoServidor: "", semConexao: false, servidorAntigo: false, headlineEditada: {},
  massaSelecionados: new Set(), massa: [],
};

const $ = (id) => document.getElementById(id);

function el(tag, classe, texto) {
  const elemento = document.createElement(tag);
  if (classe) elemento.className = classe;
  if (texto != null) elemento.textContent = texto;
  return elemento;
}

function adiar(funcao, ms) {
  let espera = null;
  return (...argumentos) => {
    clearTimeout(espera);
    espera = setTimeout(() => funcao(...argumentos), ms);
  };
}

function limitar(valor, minimo, maximo) {
  return Math.max(minimo, Math.min(maximo, valor));
}

function lerLocal(chave, padrao) {
  try {
    const valor = localStorage.getItem(chave);
    return valor ? JSON.parse(valor) : padrao;
  } catch (erro) {
    return padrao;
  }
}

function guardarLocal(chave, valor) {
  try {
    localStorage.setItem(chave, JSON.stringify(valor));
  } catch (erro) {
    // Armazenamento do navegador indisponível: o estilo só não fica lembrado para a próxima vez.
  }
}

// ---------- formatação ----------

function fmtTempo(segundos, casas) {
  if (segundos == null || isNaN(segundos)) return "—";
  casas = casas || 0;
  const fator = Math.pow(10, casas);
  const total = Math.round(Math.max(0, segundos) * fator) / fator;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = (total - h * 3600 - m * 60).toFixed(casas).padStart(casas ? 3 + casas : 2, "0");
  return ((h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + s).replace(".", ",");
}

function lerTempo(texto) {
  const limpo = String(texto).trim().replace(",", ".");
  if (!limpo) return NaN;
  const partes = limpo.split(":").map(Number);
  if (partes.length > 3 || partes.some((parte) => isNaN(parte) || parte < 0)) return NaN;
  return partes.reduce((total, parte) => total * 60 + parte, 0);
}

function fmtDuracao(segundos, decimal) {
  if (segundos == null || isNaN(segundos)) return "—";
  if (segundos < 60) return (decimal ? segundos.toFixed(1).replace(".", ",") : Math.round(segundos)) + "s";
  const minutos = Math.floor(segundos / 60);
  const resto = Math.floor(segundos % 60);
  return minutos + "min" + (resto ? " " + String(resto).padStart(2, "0") + "s" : "");
}

function fmtData(iso) {
  if (!iso) return "";
  const data = new Date(iso);
  return data.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit" }) +
    " " + data.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function nomeArquivo(caminho) {
  return caminho ? caminho.split(/[\\/]/).pop() : "";
}

function limparPalavra(palavra) {
  return palavra.replace(/^["'“”(\[]+|["'“”)\].,;:!?…]+$/g, "");
}

// ---------- rede e avisos ----------

async function api(caminho, opcoes) {
  let resposta;
  try {
    resposta = await fetch(caminho, opcoes);
  } catch (erro) {
    estado.semConexao = true;
    verificarServidor();
    throw new Error("sem conexão com o servidor do Indomável");
  }
  let dados = null;
  try { dados = await resposta.json(); } catch (erro) { dados = null; }
  if (!resposta.ok) throw new Error((dados && dados.erro) || "erro " + resposta.status);
  return dados;
}

function postar(caminho, corpo) {
  return api(caminho, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo) });
}

function avisar(mensagem, tipo) {
  // Sem servidor ou com servidor antigo, a faixa vermelha do topo já explica; não empilha avisos.
  if (tipo === "erro" && (estado.semConexao || estado.servidorAntigo)) return;
  const caixa = $("aviso");
  caixa.textContent = mensagem;
  caixa.className = "aviso " + (tipo || "");
  caixa.hidden = false;
  clearTimeout(avisar.espera);
  avisar.espera = setTimeout(() => { caixa.hidden = true; }, 6000);
}

// ---------- Chub: status, fontes e vídeos ----------

async function verificarChub() {
  const chip = $("status-chub");
  try {
    const dados = await api("/api/saude");
    chip.textContent = "Chub conectado · " + Number(dados.chub.blocos_ativos).toLocaleString("pt-BR") + " blocos";
    chip.className = "chip chip-ok";
    chip.title = "Hora do servidor do Chub: " + dados.chub.agora;
  } catch (erro) {
    chip.textContent = "Chub com problema: " + erro.message;
    chip.className = "chip chip-erro";
    chip.title = erro.message;
  }
}

async function carregarFontes() {
  const dados = await api("/api/fontes");
  dados.fontes.forEach((fonte) => {
    const opcao = el("option", null, fonte);
    opcao.value = fonte;
    $("fonte").appendChild(opcao);
  });
}

async function carregarVideos(reiniciar) {
  const token = ++estado.tokenVideos;
  const lista = $("lista-videos");
  if (reiniciar) {
    estado.pagina = 0;
    estado.videos = [];
    lista.innerHTML = "";
  }
  $("mais-videos").hidden = true;
  const carregando = el("li", "carregando", "Carregando vídeos do Chub…");
  const parametros = new URLSearchParams({ pagina: String(estado.pagina), fonte: estado.fonte, q: estado.busca, so_com_blocos: "1" });
  if (estado.filtroTatico && estado.filtroTatico !== "todos") {
    parametros.set("filtro", estado.filtroTatico);
  }
  try {
    const dados = await api("/api/videos?" + parametros.toString());
    if (token !== estado.tokenVideos || estado.origemLista !== "chub") return;
    carregando.remove();
    dados.videos.forEach((video) => {
      estado.videos.push(video);
      lista.appendChild(itemVideo(video, true));
    });
    if (!estado.videos.length) lista.appendChild(el("li", "vazio-lista", "Nenhum vídeo encontrado."));
    $("mais-videos").hidden = dados.videos.length < dados.tamanho;
  } catch (erro) {
    if (token !== estado.tokenVideos) return;
    carregando.className = "erro-lista";
    carregando.textContent = "Não carregou: " + erro.message;
  }
}

function itemVideo(video, clicavel) {
  const ativo = estado.video && estado.video.youtube_id === video.youtube_id;
  const item = el("li", "video" + (ativo ? " ativo" : "") + (clicavel ? "" : " indisponivel"));
  item.dataset.id = video.youtube_id;
  const imagem = el("img");
  imagem.loading = "lazy";
  imagem.alt = "";
  imagem.src = "https://i.ytimg.com/vi/" + video.youtube_id + "/mqdefault.jpg";
  const corpo = el("div", "video-corpo");
  let situacao = "";
  if (video.blocos) {
    situacao = video.blocos + " blocos";
    if (video.blocos_qa != null && video.blocos_qa > 0) {
      situacao += " (" + video.blocos_qa + " prontos)";
    }
  } else if (video.tem_transcricao) {
    situacao = "legenda pronta, blocos a caminho";
  } else {
    situacao = "aguardando legenda";
  }
  corpo.append(
    el("div", "video-titulo", video.titulo),
    el("div", "video-meta", [fmtData(video.publicado_em), video.duracao_s ? fmtDuracao(video.duracao_s) : "", situacao].filter(Boolean).join(" · ")),
    el("div", "video-fonte", video.fontes),
  );
  item.append(imagem, corpo);
  if (clicavel) item.addEventListener("click", () => abrirVideo(video));
  return item;
}

// ---------- links de fora do Chub ----------

function trocarOrigem(origem) {
  estado.origemLista = origem;
  document.querySelectorAll(".aba[data-origem]").forEach((aba) => aba.classList.toggle("ativa", aba.dataset.origem === origem));
  $("filtros-chub").hidden = origem !== "chub";
  if ($("barra-playlist")) $("barra-playlist").hidden = origem !== "playlist";
  $("mais-videos").hidden = true;
  $("lista-videos").innerHTML = "";
  if (origem === "chub") {
    carregarVideos(true);
  } else if (origem === "playlist") {
    carregarPlaylist();
  } else {
    carregarLocais();
  }
}

function fmtTempoRelativo(timestampSegundos) {
  if (!timestampSegundos) return "recentemente";
  const decorrido = Math.max(0, Math.floor(Date.now() / 1000 - timestampSegundos));
  if (decorrido < 10) return "agora mesmo";
  if (decorrido < 60) return `há ${decorrido}s`;
  const m = Math.floor(decorrido / 60);
  if (m < 60) return `há ${m} min`;
  const h = Math.floor(m / 60);
  return `há ${h}h`;
}

async function carregarPlaylist() {
  clearTimeout(carregarPlaylist.espera);
  try {
    const dados = await api("/api/playlist");
    estado.playlist = dados.videos || [];
    estado.statusPlaylist = dados.status || {};
  } catch (erro) {
    if (estado.origemLista === "playlist") {
      $("lista-videos").innerHTML = "";
      $("lista-videos").appendChild(el("li", "erro-lista", "Não carregou a playlist: " + erro.message));
    }
    return;
  }
  if ($("contador-playlist")) {
    $("contador-playlist").textContent = estado.playlist.length ? "(" + estado.playlist.length + ")" : "";
  }
  const st = estado.statusPlaylist;
  if ($("playlist-status-texto")) {
    if (st && st.sincronizando) {
      $("playlist-status-texto").textContent = "Sincronizando YouTube…";
      if ($("playlist-ponto-sync")) $("playlist-ponto-sync").className = "ponto-sync sincronizando";
    } else {
      const checado = st && st.ultima_checagem ? fmtTempoRelativo(st.ultima_checagem) : "Ao vivo";
      $("playlist-status-texto").textContent = `Atualizado ${checado} · ${estado.playlist.length} vídeo(s)`;
      if ($("playlist-ponto-sync")) $("playlist-ponto-sync").className = "ponto-sync";
    }
  }
  if (estado.origemLista === "playlist") desenharPlaylist();
  const andando = (st && st.sincronizando) || estado.playlist.some((v) => !["pronto", "falhou", "interrompido"].includes(v.estado));
  carregarPlaylist.espera = setTimeout(carregarPlaylist, andando ? 2500 : 15000);
}

function desenharPlaylist() {
  const lista = $("lista-videos");
  lista.innerHTML = "";
  if (!estado.playlist.length) {
    const li = el("li", "vazio-lista", "Ainda não há vídeos catalogados na aba Indomável. O sistema está monitorando a playlist em tempo real.");
    lista.appendChild(li);
    return;
  }
  estado.playlist.forEach((video) => {
    const pronto = video.estado === "pronto" || (video.blocos && video.blocos > 0);
    const item = itemVideo(video, pronto);
    const corpo = item.querySelector(".video-corpo");
    corpo.querySelector(".video-meta").textContent = [
      fmtData(video.publicado_em),
      video.duracao_s ? fmtDuracao(video.duracao_s) : "",
      pronto ? video.blocos + " blocos" + (video.blocos_qa ? ` (${video.blocos_qa} prontos)` : "") : ""
    ].filter(Boolean).join(" · ");
    
    const nome = ESTADOS_LOCAIS[video.estado] || video.estado || (pronto ? "Pronto" : "Aguardando");
    corpo.appendChild(el("div", "video-estado estado-" + (pronto ? "pronto" : video.estado), pronto ? "✅ " + nome : "⏳ " + nome + (video.mensagem ? ": " + video.mensagem : "")));
    
    if (!pronto && video.estado !== "falhou" && video.estado !== "interrompido" && video.progresso != null) {
      const barra = el("div", "progresso");
      const cheio = el("div");
      cheio.style.width = Math.round(100 * video.progresso) + "%";
      barra.appendChild(cheio);
      corpo.appendChild(barra);
    }
    if (["falhou", "interrompido", "aguardando_retentativa", "aguardando_youtube"].includes(video.estado)) {
      const botao = el("button", "botao-sec pequeno", "Processar de novo");
      botao.type = "button";
      botao.addEventListener("click", (evento) => {
        evento.stopPropagation();
        enviarLink(video.youtube_id, true);
      });
      corpo.appendChild(botao);
    }
    if (pronto) {
      corpo.appendChild(criarPainelCortes(video, carregarPlaylist));
    }
    lista.appendChild(item);
  });
}

function criarPainelCortes(video, callbackRecarregar) {
  const c = video.cortes;
  const painelCortes = el("div", "video-cortes-painel");
  painelCortes.style.marginTop = "6px";
  painelCortes.style.display = "flex";
  painelCortes.style.alignItems = "center";
  painelCortes.style.gap = "6px";
  painelCortes.style.flexWrap = "wrap";

  if (c && c.estado === "concluido") {
    const qtd = c.feitos || (c.pacotes ? c.pacotes.length : 0) || c.total_blocos || 0;
    const tag = el("span", "tag-corte-concluido", `🎬 ${qtd} cortes gerados (Google Drive)`);
    tag.style.fontSize = "11px";
    tag.style.fontWeight = "bold";
    tag.style.color = "#2ed573";
    tag.style.background = "rgba(46, 213, 115, 0.12)";
    tag.style.padding = "2px 6px";
    tag.style.borderRadius = "4px";
    painelCortes.appendChild(tag);

    const btnRefazer = el("button", "botao-sec pequeno", "🔄 Recortar do zero");
    btnRefazer.type = "button";
    btnRefazer.title = "Apaga os cortes anteriores deste vídeo e gera novamente os pacotes FernandoXX";
    btnRefazer.addEventListener("click", async (evento) => {
      evento.stopPropagation();
      if (!confirm("Deseja apagar os cortes anteriores deste vídeo e regerar pacotes FernandoXX do zero?")) return;
      btnRefazer.disabled = true;
      try {
        await postar(`/api/cortes/${video.youtube_id}/refazer`, {});
        avisar("Regerando cortes FernandoXX do zero...", "ok");
        setTimeout(callbackRecarregar, 1000);
      } catch (e) {
        avisar("Erro ao refazer cortes: " + e.message, "erro");
        btnRefazer.disabled = false;
      }
    });
    painelCortes.appendChild(btnRefazer);
  } else if (c && (c.estado === "processando" || c.estado === "em_fila")) {
    const txt = c.estado === "em_fila"
      ? "⏳ Na fila de cortes sequenciais…"
      : `⚡ Gerando pacotes FernandoXX (${c.feitos || 0}/${c.total_blocos || 0})…`;
    const tag = el("span", "tag-corte-processando", txt);
    tag.style.fontSize = "11px";
    tag.style.color = "#ffa502";
    tag.style.fontWeight = "bold";
    painelCortes.appendChild(tag);
  } else {
    const btnCortes = el("button", "botao-pri pequeno", "⚡ Gerar Cortes (FernandoXX)");
    btnCortes.type = "button";
    btnCortes.title = "Gera o pacote quádruplo FernandoXX para Google Drive (com legenda, sem legenda, cru + srt e headlines)";
    btnCortes.addEventListener("click", async (evento) => {
      evento.stopPropagation();
      btnCortes.disabled = true;
      try {
        await postar(`/api/cortes/${video.youtube_id}/disparar`, {});
        avisar("Cortes iniciados! Pacotes FernandoXX sendo gerados em segundo plano.", "ok");
        setTimeout(callbackRecarregar, 1000);
      } catch (e) {
        avisar("Erro ao disparar cortes: " + e.message, "erro");
        btnCortes.disabled = false;
      }
    });
    painelCortes.appendChild(btnCortes);

    const btnPonto = el("button", "botao-sec pequeno", "🚩 Iniciar cortes daqui");
    btnPonto.type = "button";
    btnPonto.title = "Define este vídeo como o ponto de partida para os cortes automáticos";
    btnPonto.addEventListener("click", async (evento) => {
      evento.stopPropagation();
      try {
        await postar("/api/automacao/config-cortes", { video_inicial_id: video.youtube_id });
        if (estado.configCortes) estado.configCortes.video_inicial_id = video.youtube_id;
        avisar(`🚩 Ponto de partida definido: ${video.titulo || video.youtube_id}`, "ok");
      } catch (e) {
        avisar("Erro ao definir ponto de partida: " + e.message, "erro");
      }
    });
    painelCortes.appendChild(btnPonto);
  }
  return painelCortes;
}

async function carregarLocais() {
  clearTimeout(carregarLocais.espera);
  try {
    estado.locais = (await api("/api/locais")).videos;
  } catch (erro) {
    if (estado.origemLista === "local") {
      $("lista-videos").innerHTML = "";
      $("lista-videos").appendChild(el("li", "erro-lista", "Não carregou: " + erro.message));
    }
    return;
  }
  $("contador-locais").textContent = estado.locais.length ? "(" + estado.locais.length + ")" : "";
  if (estado.origemLista === "local") desenharLocais();
  const andando = estado.locais.some((video) => !["pronto", "falhou", "interrompido"].includes(video.estado));
  if (andando) carregarLocais.espera = setTimeout(carregarLocais, 2500);
}

function desenharLocais() {
  const lista = $("lista-videos");
  lista.innerHTML = "";
  if (!estado.locais.length) {
    lista.appendChild(el("li", "vazio-lista", "Cole acima o link de um vídeo que o Chub não tem. O Indomável busca a legenda e divide em blocos com o Gemini."));
    return;
  }
  estado.locais.forEach((video) => {
    const pronto = video.estado === "pronto";
    const item = itemVideo(video, pronto);
    const corpo = item.querySelector(".video-corpo");
    corpo.querySelector(".video-meta").textContent = [fmtData(video.publicado_em), video.duracao_s ? fmtDuracao(video.duracao_s) : "",
      pronto ? video.blocos + " blocos" : ""].filter(Boolean).join(" · ");
    const nome = ESTADOS_LOCAIS[video.estado] || video.estado || "";
    corpo.appendChild(el("div", "video-estado estado-" + video.estado, pronto ? nome : nome + (video.mensagem ? ": " + video.mensagem : "")));
    if (!pronto && video.estado !== "falhou" && video.estado !== "interrompido" && video.progresso != null) {
      const barra = el("div", "progresso");
      const cheio = el("div");
      cheio.style.width = Math.round(100 * video.progresso) + "%";
      barra.appendChild(cheio);
      corpo.appendChild(barra);
    }
    if (video.estado === "falhou" || video.estado === "interrompido") {
      const botao = el("button", "botao-sec pequeno", "Processar de novo");
      botao.type = "button";
      botao.addEventListener("click", (evento) => {
        evento.stopPropagation();
        enviarLink(video.youtube_id, true);
      });
      corpo.appendChild(botao);
    }
    if (pronto) {
      corpo.appendChild(criarPainelCortes(video, carregarLocais));
    }
    lista.appendChild(item);
  });
}

async function enviarLink(link, refazer) {
  try {
    const dados = await postar("/api/links", { link: link, refazer: Boolean(refazer) });
    if (dados.situacao === "no_chub") {
      avisar("Esse vídeo já está no Chub: abrindo os blocos do Chub.", "ok");
      trocarOrigem("chub");
      abrirVideo(dados.video);
    } else if (dados.situacao === "local_pronto") {
      trocarOrigem("local");
      avisar("Esse vídeo já foi processado aqui.", "ok");
    } else {
      avisar(dados.video_chub ? "O Chub ainda não dividiu esse vídeo: processando aqui." : "Processando: legenda do YouTube e blocos pelo Gemini.", "ok");
      trocarOrigem("local");
    }
    $("link").value = "";
  } catch (erro) {
    avisar("Não deu para abrir o link: " + erro.message, "erro");
  }
}

// ---------- abrir vídeo e selecionar bloco ----------

async function abrirVideo(video) {
  estado.video = video;
  estado.bloco = null;
  estado.blocos = [];
  estado.frases = [];
  estado.trechos = [];
  estado.trechosChave = "";
  estado.tocandoCorte = false;
  document.querySelectorAll(".video").forEach((item) => item.classList.toggle("ativo", item.dataset.id === video.youtube_id));
  $("vazio").hidden = true;
  $("editor").hidden = false;
  $("titulo-video").textContent = video.titulo;
  $("link-youtube").href = "https://www.youtube.com/watch?v=" + video.youtube_id;
  $("detalhe-bloco").hidden = true;
  $("card-frases").hidden = true;
  $("lista-blocos").innerHTML = "";
  $("lista-blocos").appendChild(el("li", "carregando", "Carregando blocos…"));
  estado.duracao = video.duracao_s || 0;
  definirCorte(null, null);
  aplicarLayout();
  carregarPlayer(video.youtube_id);

  const base = (video.origem === "local" ? "/api/locais/" : "/api/videos/") + video.youtube_id;
  const [resultadoBlocos, resultadoFrases] = await Promise.allSettled([api(base + "/blocos"), api(base + "/transcricao")]);
  if (estado.video !== video) return;
  if (resultadoFrases.status === "fulfilled") {
    estado.frases = resultadoFrases.value.frases;
    if (!estado.duracao && resultadoFrases.value.video) estado.duracao = resultadoFrases.value.video.duracao_s || 0;
    if (!estado.duracao && estado.frases.length) estado.duracao = estado.frases[estado.frases.length - 1].fim;
  } else {
    avisar("Sem a transcrição deste vídeo: " + resultadoFrases.reason.message, "erro");
  }
  if (resultadoBlocos.status === "fulfilled") {
    estado.blocos = resultadoBlocos.value.blocos;
    desenharBlocos();
    atualizarResumoExportacaoLote();
    const pedido = estado.corteAoAbrir && estado.corteAoAbrir.youtube_id === video.youtube_id ? estado.corteAoAbrir : null;
    estado.corteAoAbrir = null;
    const alvo = pedido
      ? (estado.blocos.find((bloco) => bloco.id === pedido.bloco_id) ||
         estado.blocos.find((bloco) => bloco.inicio <= pedido.inicio && bloco.fim >= pedido.inicio))
      : ordenados()[0];
    if (alvo) selecionarBloco(alvo, false);
    if (pedido) abrirCorteAutomatico(pedido);
  } else {
    $("lista-blocos").innerHTML = "";
    $("lista-blocos").appendChild(el("li", "erro-lista", "Blocos não carregaram: " + resultadoBlocos.reason.message));
  }
  desenharVisaoGeral();
  desenharTrilha();
}

function selecionarBloco(bloco, tocar) {
  estado.bloco = bloco;
  definirCorte(bloco.inicio, bloco.fim);
  ajustarVista();
  desenharBlocos();
  desenharDetalheBloco();
  desenharFrases();
  aplicarHeadlineDoBloco(bloco);
  irPara(bloco.inicio, Boolean(tocar));
  estado.tocandoCorte = Boolean(tocar);
  atualizarTransporte();
  estado.sugestoesChave = "";
  sugerirHeadlinesAdiado();
}

function aplicarHeadlineDoBloco(bloco) {
  const guardada = estado.headlinesPorBloco[bloco.id];
  estado.estilo.tag = guardada ? guardada.tag : (estado.estilo.tag || "EM ALTA!");
  estado.estilo.headline = guardada ? guardada.headline : bloco.titulo;
  preencherControlesEstilo();
  pedirLayoutAdiado();
}

// ---------- player do YouTube ----------

let promessaYouTube = null;

function carregarAPIYouTube() {
  if (!promessaYouTube) {
    promessaYouTube = new Promise((resolver, rejeitar) => {
      if (window.YT && window.YT.Player) { resolver(); return; }
      window.onYouTubeIframeAPIReady = () => resolver();
      const script = document.createElement("script");
      script.src = "https://www.youtube.com/iframe_api";
      script.onerror = () => { promessaYouTube = null; rejeitar(new Error("o player do YouTube não carregou (sem internet?)")); };
      document.head.appendChild(script);
    });
  }
  return promessaYouTube;
}

async function carregarPlayer(youtubeId) {
  try {
    await carregarAPIYouTube();
  } catch (erro) {
    avisar(erro.message, "erro");
    return;
  }
  if (estado.player) {
    if (estado.playerPronto) estado.player.cueVideoById({ videoId: youtubeId, startSeconds: 0 });
    else estado.pendente = { t: 0, tocar: false };
    return;
  }
  estado.player = new YT.Player("player", {
    videoId: youtubeId,
    width: "100%",
    height: "100%",
    playerVars: {
      controls: 0, disablekb: 1, fs: 0, rel: 0, playsinline: 1, iv_load_policy: 3, cc_load_policy: 0,
      modestbranding: 1, origin: window.location.origin,
    },
    events: {
      onReady: () => {
        estado.playerPronto = true;
        desligarLegendasDoYouTube();
        if (estado.pendente) {
          const pendente = estado.pendente;
          estado.pendente = null;
          irPara(pendente.t, pendente.tocar);
        }
      },
      onStateChange: (evento) => {
        // O YouTube religa a própria legenda a cada mudança de estado; ela ficaria por baixo da nossa.
        desligarLegendasDoYouTube();
        if (evento.data === 1 && estado.pausarQuandoTocar) {
          // Toque rápido só para o vídeo mostrar o quadro pedido pela trilha antes do primeiro play.
          estado.pausarQuandoTocar = false;
          estado.player.pauseVideo();
          if (estado.tempoVisual != null) estado.player.seekTo(estado.tempoVisual, true);
          if (!estado.estavaMudo) estado.player.unMute();
          setTimeout(atualizarTransporte, 250);
        }
        if (evento.data === 0) estado.tocandoCorte = false;
        atualizarTransporte();
      },
      onError: (evento) => {
        const mensagem = evento.data === 101 || evento.data === 150
          ? "O dono do vídeo não deixa tocar fora do YouTube. A exportação continua funcionando."
          : "O player do YouTube deu erro " + evento.data + ".";
        avisar(mensagem, "erro");
      },
    },
  });
}

function desligarLegendasDoYouTube() {
  try {
    estado.player.setOption("captions", "track", {});
    estado.player.unloadModule("captions");
    estado.player.unloadModule("cc");
  } catch (erro) {
    // O módulo de legenda do YouTube ainda não existe neste estado do player; nada a desligar.
  }
}

function estadoPlayer() {
  try {
    return estado.player && estado.playerPronto ? estado.player.getPlayerState() : -1;
  } catch (erro) {
    return -1;
  }
}

function irPara(segundos, tocar) {
  const player = estado.player;
  if (!player || !estado.playerPronto || !estado.video) {
    estado.pendente = { t: segundos, tocar: tocar };
    return;
  }
  segundos = Math.max(0, segundos);
  estado.tempoVisual = segundos;
  estado.ultimaBusca = performance.now();
  const situacao = player.getPlayerState();
  if (tocar) {
    player.seekTo(segundos, true);
    player.playVideo();
  } else if (situacao === 1 || situacao === 2 || situacao === 3) {
    player.seekTo(segundos, true);
  } else {
    player.cueVideoById({ videoId: estado.video.youtube_id, startSeconds: segundos });
  }
}

// Busca rápida para arrastar: no máximo uma a cada INTERVALO_BUSCA_MS; a última sempre é feita.
function buscar(segundos, final) {
  estado.tempoVisual = segundos;
  const player = estado.player;
  if (!player || !estado.playerPronto) return;
  const agora = performance.now();
  if (!final && agora - estado.ultimaBusca < INTERVALO_BUSCA_MS) {
    estado.buscaPendente = segundos;
    return;
  }
  estado.buscaPendente = null;
  estado.ultimaBusca = agora;
  const situacao = player.getPlayerState();
  if (situacao === -1 || situacao === 5) {
    // Vídeo que nunca tocou não mostra o quadro de uma busca: toca mudo por um instante e pausa.
    if (!estado.pausarQuandoTocar) estado.estavaMudo = player.isMuted();
    estado.pausarQuandoTocar = true;
    player.mute();
    player.seekTo(Math.max(0, segundos), true);
    player.playVideo();
    return;
  }
  player.seekTo(Math.max(0, segundos), Boolean(final));
  if (situacao !== 1 && situacao !== 3) player.pauseVideo();
}

function tempoAtual() {
  if (estado.arrasto || (estado.tempoVisual != null && performance.now() - estado.ultimaBusca < 450)) {
    return estado.tempoVisual || 0;
  }
  try {
    return estado.player && estado.playerPronto ? (estado.player.getCurrentTime() || 0) : 0;
  } catch (erro) {
    return 0;
  }
}

function alternarPlay() {
  if (!estado.player || !estado.playerPronto) return;
  if (estadoPlayer() === 1) {
    estado.player.pauseVideo();
    estado.tocandoCorte = false;
  } else {
    const agora = tempoAtual();
    if (estado.inicio != null && (agora < estado.inicio - 0.05 || agora >= estado.fim - 0.05)) irPara(estado.inicio, true);
    else estado.player.playVideo();
    estado.tocandoCorte = estado.inicio != null;
  }
  atualizarTransporte();
}

function tocarCorte() {
  if (estado.inicio == null) return;
  irPara(estado.inicio, true);
  estado.tocandoCorte = true;
  atualizarTransporte();
}

function atualizarTransporte() {
  const tocando = estadoPlayer() === 1 || estadoPlayer() === 3;
  $("play").textContent = tocando ? "❚❚" : "▶";
  $("tocar-corte").classList.toggle("ativo", estado.tocandoCorte);
  if (estado.player && estado.playerPronto) {
    try { $("mudo").textContent = estado.player.isMuted() ? "Mudo" : "Som ligado"; } catch (erro) { /* player recriando */ }
  }
}

// ---------- preview: layout, moldura e legenda ----------

function subconjunto(campos) {
  const saida = {};
  campos.forEach((campo) => { saida[campo] = estado.estilo[campo]; });
  return saida;
}

async function pedirLayout() {
  const pedido = { formato: estado.formato, estilo: subconjunto(CAMPOS_LAYOUT) };
  const chave = JSON.stringify(pedido);
  if (chave === estado.layoutChave) return;
  estado.layoutChave = chave;
  try {
    const dados = await postar("/api/layout", pedido);
    if (chave !== estado.layoutChave) return;
    estado.layout = dados.layout;
    aplicarLayout();
    atualizarResumoExportacao();
  } catch (erro) {
    estado.layoutChave = "";
    avisar("Não deu para montar o preview: " + erro.message, "erro");
    setTimeout(pedirLayoutAdiado, 4000);
  }
}

const pedirLayoutAdiado = adiar(pedirLayout, 160);

function aplicarLayout() {
  const [largura, altura] = FORMATOS[estado.formato];
  const externo = $("palco-externo");
  const livreL = externo.clientWidth - 20;
  const livreA = externo.clientHeight - 20;
  if (livreL <= 0 || livreA <= 0) return;
  const quadroL = Math.floor(Math.min(livreL, livreA * largura / altura));
  const quadroA = Math.floor(quadroL * altura / largura);
  const quadro = $("quadro");
  quadro.style.width = quadroL + "px";
  quadro.style.height = quadroA + "px";
  estado.escala = quadroL / largura;
  const layout = estado.layout && estado.layout.formato === estado.formato ? estado.layout : null;
  const area = $("area-video");
  area.style.top = (layout ? layout.video.y * estado.escala : 0) + "px";
  area.style.height = (layout ? layout.video.h * estado.escala : quadroA) + "px";
  posicionarPlayer();
  const endereco = "/api/moldura.png?d=" + encodeURIComponent(JSON.stringify({ formato: estado.formato, estilo: subconjunto(CAMPOS_MOLDURA) }));
  if ($("moldura").getAttribute("src") !== endereco) $("moldura").setAttribute("src", endereco);
  aplicarEstiloLegenda();
}

function posicionarPlayer() {
  const area = $("area-video");
  const larguraArea = area.clientWidth;
  const alturaArea = area.clientHeight;
  if (!larguraArea || !alturaArea) return;
  // Mesma conta do ffmpeg na exportação: cobre a área, aplica o zoom e desloca pelo enquadramento e corte de topo (anti-GC).
  const cortarTopo = ((estado.estilo.cortar_topo || 0) * (estado.escala || 1));
  const escala = Math.max(larguraArea / 1920, (alturaArea + cortarTopo) / 1080) * (estado.estilo.zoom || 1);
  const larguraPlayer = 1920 * escala;
  const alturaPlayer = 1080 * escala;
  const folgaX = Math.max(0, larguraPlayer - larguraArea);
  const folgaY = Math.max(0, alturaPlayer - alturaArea - cortarTopo);
  const caixa = $("player-caixa");
  caixa.style.width = larguraPlayer + "px";
  caixa.style.height = alturaPlayer + "px";
  caixa.style.left = (-folgaX / 2 * (1 + (estado.estilo.enquadramento_x || 0))) + "px";
  caixa.style.top = (-cortarTopo - (folgaY / 2 * (1 + (estado.estilo.enquadramento_y || 0)))) + "px";
}

function aplicarEstiloLegenda() {
  const caixa = $("legenda");
  const estilo = estado.estilo;
  const escala = estado.escala || 1;
  caixa.hidden = !estilo.legenda;
  // Cor, fonte e caixa alta valem na hora, mesmo antes de o layout chegar do servidor.
  caixa.style.fontFamily = FONTES_CSS[estilo.fonte_legenda] || FONTES_CSS.bebas;
  caixa.style.fontWeight = PESOS_CSS[estilo.fonte_legenda] || 400;
  caixa.style.color = estilo.cor_legenda || "#FFFFFF";
  caixa.style.textTransform = estilo.maiusculas ? "uppercase" : "none";
  const layout = estado.layout && estado.layout.formato === estado.formato ? estado.layout : null;
  const [largura, altura] = FORMATOS[estado.formato];
  const unidade = Math.min(largura, altura) / 1080;
  const tamanho = layout ? layout.legenda.tamanho : (estilo.tamanho_legenda || 104) * unidade;
  const contorno = layout ? layout.legenda.contorno : (estilo.contorno || 0) * unidade;
  caixa.style.top = layout ? layout.legenda.y * escala + "px" : ((estilo.posicao_legenda || 0.72) * 100) + "%";
  caixa.style.fontSize = tamanho * escala + "px";
  // No ASS o contorno fica todo por fora da letra; no CSS metade fica por dentro, por isso o dobro.
  caixa.style.setProperty("--contorno", (2 * contorno * escala) + "px");
  estado.ultimoTrecho = undefined;
}

async function pedirTrechos() {
  if (!estado.video || estado.inicio == null) return;
  const pedido = {
    youtube_id: estado.video.youtube_id, inicio: estado.inicio, fim: estado.fim,
    estilo: { max_palavras: estado.estilo.max_palavras },
  };
  const chave = JSON.stringify(pedido);
  if (chave === estado.trechosChave) return;
  estado.trechosChave = chave;
  $("info-legenda").textContent = "Carregando a legenda palavra por palavra…";
  try {
    const dados = await postar("/api/legenda", pedido);
    if (chave !== estado.trechosChave) return;
    estado.trechos = dados.trechos;
    $("info-legenda").textContent = dados.trechos.length + " trechos, com o tempo de cada palavra da legenda automática. Confira nomes e números no áudio.";
  } catch (erro) {
    if (chave !== estado.trechosChave) return;
    estado.trechos = trechosPelasFrases();
    $("info-legenda").textContent = "Legenda aproximada pelas frases (sem o tempo de cada palavra): " + erro.message;
  }
  estado.ultimoTrecho = undefined;
  desenharTrilha();
}

const pedirTrechosAdiado = adiar(pedirTrechos, 250);

function trechosPelasFrases() {
  const saida = [];
  estado.frases.filter((frase) => frase.fim > estado.inicio && frase.inicio < estado.fim).forEach((frase) => {
    const palavras = frase.texto.split(/\s+/).filter(Boolean);
    const porVez = estado.estilo.max_palavras || 3;
    const grupos = [];
    for (let i = 0; i < palavras.length; i += porVez) grupos.push(palavras.slice(i, i + porVez));
    const passo = (frase.fim - frase.inicio) / (grupos.length || 1);
    grupos.forEach((grupo, i) => {
      const inicio = Math.max(frase.inicio + i * passo, estado.inicio);
      const fim = Math.min(frase.inicio + (i + 1) * passo, estado.fim);
      if (fim > inicio) saida.push({ inicio, fim, palavras: grupo, destaque: null });
    });
  });
  return saida;
}

function trechoNoTempo(segundos) {
  let baixo = 0;
  let alto = estado.trechos.length - 1;
  let achado = -1;
  while (baixo <= alto) {
    const meio = (baixo + alto) >> 1;
    if (estado.trechos[meio].inicio <= segundos) { achado = meio; baixo = meio + 1; } else { alto = meio - 1; }
  }
  if (achado < 0) return null;
  return segundos < estado.trechos[achado].fim ? estado.trechos[achado] : null;
}

function atualizarLegenda(segundos) {
  const dentro = estado.inicio != null && segundos >= estado.inicio - 0.02 && segundos <= estado.fim + 0.02;
  let trecho = dentro && estado.estilo.legenda ? trechoNoTempo(segundos) : null;
  const parado = !estado.arrasto && estadoPlayer() !== 1 && estadoPlayer() !== 3;
  if (!trecho && parado && estado.estilo.legenda && estado.trechos.length) {
    // Vídeo parado: mostra o trecho mais próximo, para dar para ver o efeito de cor, fonte e tamanho.
    trecho = estado.trechos.find((candidato) => candidato.fim > segundos) || estado.trechos[0];
  }
  if (trecho === estado.ultimoTrecho) return;
  estado.ultimoTrecho = trecho;
  const alvo = $("legenda-texto");
  alvo.innerHTML = "";
  $("legenda").classList.toggle("vazia", !trecho);
  if (!trecho) return;
  trecho.palavras.forEach((palavra, indice) => {
    const texto = limparPalavra(palavra);
    if (!texto) return;
    if (alvo.childNodes.length) alvo.appendChild(document.createTextNode(" "));
    const parte = el("span", null, texto);
    if (estado.estilo.destacar_palavra && indice === trecho.destaque) parte.style.color = estado.estilo.cor_destaque;
    alvo.appendChild(parte);
  });
}

// ---------- trilha profissional ----------

function larguraTrilha() {
  return $("trilha").clientWidth || 1;
}

function tempoParaX(segundos) {
  return (segundos - estado.vista.inicio) / (estado.vista.fim - estado.vista.inicio) * larguraTrilha();
}

function xParaTempo(x) {
  return estado.vista.inicio + x / larguraTrilha() * (estado.vista.fim - estado.vista.inicio);
}

function duracaoTotal() {
  return estado.duracao || (estado.frases.length ? estado.frases[estado.frases.length - 1].fim : 0);
}

function definirVista(inicio, fim) {
  const total = duracaoTotal() || Math.max(fim, 60);
  const largura = limitar(fim - inicio, 3, Math.max(3, total));
  const comeco = limitar(inicio, 0, Math.max(0, total - largura));
  estado.vista = { inicio: comeco, fim: comeco + largura };
  desenharTrilha();
  desenharVisaoGeral();
}

function ajustarVista() {
  if (estado.inicio == null) return;
  const folga = Math.max(4, (estado.fim - estado.inicio) * 0.18);
  definirVista(estado.inicio - folga, estado.fim + folga);
}

function aproximar(fator, centro) {
  const { inicio, fim } = estado.vista;
  const meio = centro != null ? centro : (inicio + fim) / 2;
  const nova = (fim - inicio) * fator;
  const proporcao = (meio - inicio) / (fim - inicio);
  definirVista(meio - nova * proporcao, meio - nova * proporcao + nova);
}

function desenharTrilha() {
  const trilha = $("trilha");
  const largura = trilha.clientWidth;
  if (!largura) return;
  const { inicio, fim } = estado.vista;
  const intervalo = fim - inicio;

  const regua = $("regua");
  regua.innerHTML = "";
  const passos = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200];
  const passo = passos.find((candidato) => candidato / intervalo * largura >= 70) || 1200;
  for (let t = Math.ceil(inicio / passo) * passo; t <= fim; t += passo) {
    const marca = el("div", "marca");
    marca.style.left = tempoParaX(t) + "px";
    marca.appendChild(el("span", null, fmtTempo(t, passo < 1 ? 1 : 0)));
    regua.appendChild(marca);
  }

  const faixa = $("faixa-frases");
  faixa.innerHTML = "";
  const destaques = new Set(estado.bloco ? estado.bloco.destaques.map((destaque) => destaque.frase) : []);
  estado.frases.forEach((frase) => {
    if (frase.fim < inicio || frase.inicio > fim) return;
    const x = tempoParaX(frase.inicio);
    const w = tempoParaX(frase.fim) - x;
    const segmento = el("div", "seg-frase" + (destaques.has(frase.i) ? " destaque" : ""));
    segmento.style.left = x + "px";
    segmento.style.width = Math.max(1, w - 1) + "px";
    if (w > 34) segmento.textContent = frase.texto;
    faixa.appendChild(segmento);
  });

  const faixaLegenda = $("faixa-legenda");
  faixaLegenda.innerHTML = "";
  estado.trechos.forEach((trecho) => {
    if (trecho.fim < inicio || trecho.inicio > fim) return;
    const x = tempoParaX(trecho.inicio);
    const w = tempoParaX(trecho.fim) - x;
    const segmento = el("div", "seg-legenda");
    segmento.style.left = x + "px";
    segmento.style.width = Math.max(1, w) + "px";
    if (w > 26) segmento.textContent = trecho.palavras.map(limparPalavra).join(" ");
    faixaLegenda.appendChild(segmento);
  });
  atualizarSelecao();
}

function atualizarSelecao() {
  const selecao = $("selecao");
  if (estado.inicio == null) {
    selecao.hidden = true;
    return;
  }
  selecao.hidden = false;
  const x1 = tempoParaX(estado.inicio);
  const x2 = tempoParaX(estado.fim);
  selecao.style.left = x1 + "px";
  selecao.style.width = Math.max(2, x2 - x1) + "px";
}

function desenharVisaoGeral() {
  const caixa = $("visao-geral");
  caixa.querySelectorAll(".seg-bloco").forEach((segmento) => segmento.remove());
  const total = duracaoTotal();
  if (!total) return;
  estado.blocos.forEach((bloco) => {
    const segmento = el("div", "seg-bloco" + (bloco.pronto ? " pronto" : "") + (estado.bloco === bloco ? " ativo" : ""));
    segmento.style.left = (100 * bloco.inicio / total) + "%";
    segmento.style.width = Math.max(0.2, 100 * (bloco.fim - bloco.inicio) / total) + "%";
    segmento.title = fmtTempo(bloco.inicio) + " · " + bloco.titulo;
    caixa.appendChild(segmento);
  });
  const janela = $("janela-visivel");
  janela.style.left = (100 * estado.vista.inicio / total) + "%";
  janela.style.width = Math.max(0.4, 100 * (estado.vista.fim - estado.vista.inicio) / total) + "%";
}

function imantar(segundos, borda) {
  const limiar = MARGEM_IMA_PX / larguraTrilha() * (estado.vista.fim - estado.vista.inicio);
  let melhor = segundos;
  let distancia = limiar;
  estado.frases.forEach((frase) => {
    const alvo = borda === "inicio" ? frase.inicio : frase.fim;
    if (Math.abs(alvo - segundos) < distancia) {
      distancia = Math.abs(alvo - segundos);
      melhor = alvo;
    }
  });
  return melhor;
}

function tempoDoPonteiro(evento) {
  const caixa = $("trilha").getBoundingClientRect();
  return limitar(xParaTempo(evento.clientX - caixa.left), 0, duracaoTotal() || Number.MAX_VALUE);
}

function iniciarArrasto(evento, tipo) {
  if (!estado.video || evento.button !== 0) return;
  evento.preventDefault();
  evento.stopPropagation();
  const tocando = estadoPlayer() === 1;
  if (tocando && tipo !== "cabeca") estado.player.pauseVideo();
  estado.arrasto = {
    tipo, pointerId: evento.pointerId, alvo: evento.currentTarget,
    tInicial: tempoDoPonteiro(evento), inicio: estado.inicio, fim: estado.fim,
  };
  try { evento.currentTarget.setPointerCapture(evento.pointerId); } catch (erro) { /* navegador sem captura: segue sem */ }
  moverArrasto(evento);
}

function moverArrasto(evento) {
  const arrasto = estado.arrasto;
  if (!arrasto || evento.pointerId !== arrasto.pointerId) return;
  let t = tempoDoPonteiro(evento);
  const livre = evento.altKey || !$("imantar").checked;
  if (arrasto.tipo === "inicio") {
    if (!livre) t = imantar(t, "inicio");
    t = Math.min(t, estado.fim - CORTE_MINIMO_S);
    definirCorte(t, estado.fim, true);
    buscar(t);
  } else if (arrasto.tipo === "fim") {
    if (!livre) t = imantar(t, "fim");
    t = Math.max(t, estado.inicio + CORTE_MINIMO_S);
    definirCorte(estado.inicio, t, true);
    buscar(t);
  } else if (arrasto.tipo === "mover") {
    const duracao = arrasto.fim - arrasto.inicio;
    let novoInicio = limitar(arrasto.inicio + t - arrasto.tInicial, 0, Math.max(0, (duracaoTotal() || 1e9) - duracao));
    if (!livre) novoInicio = imantar(novoInicio, "inicio");
    definirCorte(novoInicio, novoInicio + duracao, true);
    buscar(novoInicio);
  } else {
    buscar(t);
  }
}

function terminarArrasto(evento) {
  const arrasto = estado.arrasto;
  if (!arrasto || evento.pointerId !== arrasto.pointerId) return;
  estado.arrasto = null;
  try { arrasto.alvo.releasePointerCapture(evento.pointerId); } catch (erro) { /* já liberado */ }
  buscar(estado.buscaPendente != null ? estado.buscaPendente : estado.tempoVisual, true);
  if (arrasto.tipo !== "cabeca") {
    definirCorte(estado.inicio, estado.fim);
    marcarFrasesNoCorte();
  }
}

// ---------- corte ----------

function definirCorte(inicio, fim, arrastando) {
  estado.inicio = inicio;
  estado.fim = fim;
  const valido = inicio != null && fim != null && fim > inicio;
  if (document.activeElement !== $("in-inicio")) $("in-inicio").value = inicio == null ? "" : fmtTempo(inicio, 2);
  if (document.activeElement !== $("in-fim")) $("in-fim").value = fim == null ? "" : fmtTempo(fim, 2);
  $("duracao-corte").textContent = valido ? fmtDuracao(fim - inicio, true) : "—";
  $("btn-exportar").disabled = !valido;
  if ($("btn-quick-exportar")) $("btn-quick-exportar").disabled = !valido;
  $("btn-baixar-cru").disabled = !valido;
  atualizarSelecao();
  atualizarResumoExportacao();
  if (!arrastando) {
    marcarFrasesNoCorte();
    if (valido) pedirTrechosAdiado();
  }
}

function ajustar(borda, delta) {
  if (estado.inicio == null) return;
  if (borda === "inicio") {
    const t = limitar(estado.inicio + delta, 0, estado.fim - CORTE_MINIMO_S);
    definirCorte(t, estado.fim);
    buscar(t, true);
  } else {
    const t = limitar(estado.fim + delta, estado.inicio + CORTE_MINIMO_S, duracaoTotal() || Number.MAX_VALUE);
    definirCorte(estado.inicio, t);
    buscar(t, true);
  }
}

function lerCampoTempo(borda) {
  const valor = lerTempo((borda === "inicio" ? $("in-inicio") : $("in-fim")).value);
  const inicio = borda === "inicio" ? valor : estado.inicio;
  const fim = borda === "fim" ? valor : estado.fim;
  if (isNaN(valor) || inicio == null || fim == null || fim - inicio < CORTE_MINIMO_S) {
    avisar("Tempo inválido. Use o formato 1:01:43,50 e deixe o fim depois do começo.", "erro");
    $("in-inicio").blur();
    $("in-fim").blur();
    definirCorte(estado.inicio, estado.fim);
    return;
  }
  definirCorte(inicio, fim);
  buscar(valor, true);
}

function marcarAgora(borda) {
  if (estado.inicio == null) return;
  const agora = Math.round(tempoAtual() * 100) / 100;
  if (borda === "inicio") definirCorte(Math.min(agora, estado.fim - CORTE_MINIMO_S), estado.fim);
  else definirCorte(estado.inicio, Math.max(agora, estado.inicio + CORTE_MINIMO_S));
}

async function copiarTranscricao() {
  if (estado.inicio == null) return;
  const texto = estado.frases
    .filter((frase) => frase.fim > estado.inicio + 0.01 && frase.inicio < estado.fim - 0.01)
    .map((frase) => frase.texto)
    .join(" ");
  try {
    await navigator.clipboard.writeText(texto);
    avisar("Texto do corte copiado.", "ok");
  } catch (erro) {
    avisar("Não deu para copiar: " + erro.message, "erro");
  }
}

// ---------- painel: blocos ----------

function ordenados() {
  const copia = estado.blocos.slice();
  if (estado.ordem === "potencial") return copia.sort((a, b) => b.potencial - a.potencial);
  return copia.sort((a, b) => a.inicio - b.inicio);
}

function desenharBlocos() {
  atualizarBlocosCrus();
  const lista = $("lista-blocos");
  lista.innerHTML = "";
  if (!estado.blocos.length) {
    lista.appendChild(el("li", "vazio-lista", "Este vídeo ainda não foi dividido em blocos."));
    return;
  }
  ordenados().forEach((bloco, posicao) => {
    const item = el("li", "bloco" + (estado.bloco === bloco ? " ativo" : ""));
    const corpo = el("div", "bloco-corpo");
    corpo.append(
      el("div", "bloco-titulo", bloco.titulo),
      el("div", "bloco-meta", [bloco.categoria, fmtDuracao(bloco.duracao), bloco.destaques.length + " momentos fortes"].filter(Boolean).join(" · ")),
    );
    if (bloco.pronto) corpo.appendChild(el("span", "selo selo-pronto", "Pronto para short"));
    item.append(
      el("span", "bloco-pos", estado.ordem === "potencial" ? (posicao + 1) + "º" : String(posicao + 1)),
      corpo,
      el("span", "bloco-quando", fmtTempo(bloco.inicio)),
    );
    item.addEventListener("click", () => selecionarBloco(bloco, true));
    lista.appendChild(item);
  });
  desenharVisaoGeral();
}

function desenharDetalheBloco() {
  const bloco = estado.bloco;
  $("detalhe-bloco").hidden = !bloco;
  if (!bloco) return;
  $("bloco-titulo").textContent = bloco.titulo;
  $("bloco-resumo").textContent = bloco.resumo;
  const selos = $("bloco-selos");
  selos.innerHTML = "";
  const selo = (texto, classe) => selos.appendChild(el("span", "selo " + (classe || ""), texto));
  selo(fmtDuracao(bloco.duracao) + " · começa em " + fmtTempo(bloco.inicio));
  selo(bloco.renan_falando ? "Renan falando" : "Locutor não confirmado", bloco.renan_falando ? "selo-ok" : "selo-atencao");
  selo(bloco.precisa_contexto ? "Precisa de contexto" : "Se sustenta sozinho", bloco.precisa_contexto ? "selo-atencao" : "selo-ok");
  selo(bloco.cortes_possiveis + (bloco.cortes_possiveis === 1 ? " corte possível" : " cortes possíveis"));
  if (bloco.pronto) selo("Pronto para short", "selo-pronto");
  if (bloco.origem === "gemini") selo("Bloco do Gemini (fora do Chub)", "selo-gemini");
  const destaques = $("bloco-destaques");
  destaques.innerHTML = "";
  if (!bloco.destaques.length) destaques.appendChild(el("li", "vazio-lista", "Sem momentos fortes marcados."));
  bloco.destaques.forEach((destaque) => {
    const item = el("li");
    const texto = el("div");
    texto.append(el("div", "destaque-texto", "“" + destaque.texto + "”"), el("div", "destaque-motivo", destaque.motivo));
    item.append(el("span", "tempo-mini", fmtTempo(destaque.inicio)), texto);
    item.addEventListener("click", () => irPara(destaque.inicio, true));
    destaques.appendChild(item);
  });
}

function desenharFrases() {
  const bloco = estado.bloco;
  const lista = $("bloco-frases");
  lista.innerHTML = "";
  $("card-frases").hidden = !bloco;
  if (!bloco) return;
  const dentro = estado.frases.map((frase, indice) => ({ frase, indice }))
    .filter(({ frase }) => frase.fim > bloco.inicio + 0.01 && frase.inicio < bloco.fim - 0.01);
  if (!dentro.length) {
    lista.appendChild(el("li", "vazio-lista", "Sem transcrição para este bloco."));
    return;
  }
  const primeira = Math.max(0, dentro[0].indice - 3);
  const ultima = Math.min(estado.frases.length - 1, dentro[dentro.length - 1].indice + 3);
  const destaques = new Set(bloco.destaques.map((destaque) => destaque.frase));

  for (let indice = primeira; indice <= ultima; indice += 1) {
    const frase = estado.frases[indice];
    const item = el("li", "frase-item" + (destaques.has(frase.i) ? " destaque" : ""));
    item.dataset.inicio = frase.inicio;
    item.dataset.fim = frase.fim;

    const btnQuando = el("button", "quando", fmtTempo(frase.inicio));
    btnQuando.type = "button";
    btnQuando.title = "Ouvir a partir desta frase";
    btnQuando.addEventListener("click", (evento) => {
      evento.stopPropagation();
      irPara(frase.inicio, true);
    });

    const colunaTexto = el("div", "frase-conteudo");

    // Palavras clicáveis para micro-ajuste de corte
    const palavrasWrap = el("div", "frase-palavras");
    const palavrasArr = (frase.texto || "").split(/\s+/).filter(Boolean);
    const durFrase = Math.max(0.1, frase.fim - frase.inicio);
    palavrasArr.forEach((palavra, pIdx) => {
      const pInicio = frase.inicio + (pIdx / Math.max(1, palavrasArr.length)) * durFrase;
      const pFim = frase.inicio + ((pIdx + 1) / Math.max(1, palavrasArr.length)) * durFrase;
      const spanP = el("span", "palavra-clicavel", palavra + " ");
      spanP.title = `Palavra ~${pInicio.toFixed(1)}s: Clique para Início | Shift+Clique para Fim`;
      spanP.addEventListener("click", (evento) => {
        evento.stopPropagation();
        if (evento.shiftKey) {
          const novoInicio = estado.inicio != null ? Math.min(estado.inicio, pFim - CORTE_MINIMO_S) : Math.max(0, pFim - 15.0);
          definirCorte(novoInicio, pFim);
          buscar(pFim, true);
        } else {
          const novoFim = estado.fim != null ? Math.max(estado.fim, pInicio + CORTE_MINIMO_S) : pInicio + 30.0;
          definirCorte(pInicio, novoFim);
          buscar(pInicio, true);
        }
      });
      palavrasWrap.appendChild(spanP);
    });

    // Edição ao vivo da legenda
    const textoEditavel = el("div", "frase-texto-editavel", frase.texto);
    textoEditavel.contentEditable = "true";
    textoEditavel.spellcheck = false;
    textoEditavel.title = "Clique para editar o texto da legenda ao vivo";
    textoEditavel.addEventListener("input", () => {
      frase.texto = textoEditavel.textContent.trim();
      atualizarTrechosPorFrase(frase);
    });
    textoEditavel.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); textoEditavel.blur(); }
    });

    colunaTexto.append(palavrasWrap, textoEditavel);
    if (frase.conferir) colunaTexto.appendChild(el("span", "conferir", "conferir no áudio"));

    const botoes = el("span", "frase-botoes");
    const botaoInicio = el("button", "btn-corte-borda", "« Início");
    botaoInicio.type = "button";
    botaoInicio.title = "O corte começa nesta frase";
    botaoInicio.addEventListener("click", (evento) => {
      evento.stopPropagation();
      const novoFim = estado.fim != null ? Math.max(estado.fim, frase.inicio + CORTE_MINIMO_S) : frase.inicio + 30.0;
      definirCorte(frase.inicio, novoFim);
      buscar(frase.inicio, true);
    });
    const botaoFim = el("button", "btn-corte-borda", "Fim »");
    botaoFim.type = "button";
    botaoFim.title = "O corte termina nesta frase";
    botaoFim.addEventListener("click", (evento) => {
      evento.stopPropagation();
      const novoInicio = estado.inicio != null ? Math.min(estado.inicio, frase.fim - CORTE_MINIMO_S) : Math.max(0, frase.fim - 15.0);
      definirCorte(novoInicio, frase.fim);
      buscar(frase.fim, true);
    });
    botoes.append(botaoInicio, botaoFim);

    item.append(btnQuando, colunaTexto, botoes);
    lista.appendChild(item);
  }
  marcarFrasesNoCorte();
}

function atualizarTrechosPorFrase(fraseModificada) {
  if (!fraseModificada) return;
  const fIni = fraseModificada.inicio;
  const fFim = fraseModificada.fim;
  const palavras = (fraseModificada.texto || "").split(/\s+/).filter(Boolean);
  const porVez = estado.estilo.max_palavras || 3;
  const grupos = [];
  for (let i = 0; i < palavras.length; i += porVez) {
    grupos.push(palavras.slice(i, i + porVez));
  }
  const dur = Math.max(0.1, fFim - fIni);
  const passo = dur / Math.max(1, grupos.length);
  const novosTrechos = grupos.map((grupo, idx) => ({
    inicio: Math.max(fIni + idx * passo, estado.inicio != null ? estado.inicio : fIni),
    fim: Math.min(fIni + (idx + 1) * passo, estado.fim != null ? estado.fim : fFim),
    palavras: grupo,
    destaque: grupo.length > 1 ? 1 : 0,
  })).filter((t) => t.fim > t.inicio);

  const outros = (estado.trechos || []).filter((t) => t.fim <= fIni + 0.01 || t.inicio >= fFim - 0.01);
  estado.trechos = outros.concat(novosTrechos).sort((a, b) => a.inicio - b.inicio);
  estado.ultimoTrecho = undefined;
  atualizarLegenda(tempoAtual());
  desenharTrilha();
}

function marcarFrasesNoCorte() {
  document.querySelectorAll("#bloco-frases li[data-inicio]").forEach((item) => {
    const inicio = Number(item.dataset.inicio);
    const fim = Number(item.dataset.fim);
    item.classList.toggle("fora", estado.inicio == null || fim <= estado.inicio + 0.01 || inicio >= estado.fim - 0.01);
  });
}

// ---------- painel: card e legenda ----------

function montarCores(idCaixa, chave, cores) {
  const caixa = $(idCaixa);
  cores.forEach((cor) => {
    const botao = el("button", "cor");
    botao.type = "button";
    botao.style.background = cor;
    botao.dataset.cor = cor;
    botao.dataset.chave = chave;
    botao.title = cor;
    botao.addEventListener("click", () => mudarEstilo(chave, cor));
    caixa.appendChild(botao);
  });
  const entrada = el("input");
  entrada.type = "color";
  entrada.dataset.estilo = chave;
  entrada.title = "Outra cor";
  caixa.appendChild(entrada);
}

function preencherControlesEstilo() {
  document.querySelectorAll("[data-estilo]").forEach((controle) => {
    if (controle === document.activeElement && controle.type !== "checkbox") return;
    const valor = estado.estilo[controle.dataset.estilo];
    if (controle.type === "checkbox") controle.checked = Boolean(valor);
    else if (valor != null) controle.value = valor;
  });
  document.querySelectorAll("[data-valor-de]").forEach((rotulo) => {
    const chave = rotulo.dataset.valorDe;
    const valor = estado.estilo[chave];
    if (chave === "cortar_topo") {
      rotulo.textContent = (valor || 0) + "px";
    } else if (chave && chave.startsWith("enquadramento_")) {
      rotulo.textContent = (typeof valor === "number" ? valor : 0).toFixed(2);
    } else {
      rotulo.textContent = typeof valor === "number" && !Number.isInteger(valor) ? valor.toFixed(2) : (valor != null ? valor : "");
    }
  });
  document.querySelectorAll(".cor").forEach((botao) => {
    botao.classList.toggle("ativa", String(estado.estilo[botao.dataset.chave]).toUpperCase() === botao.dataset.cor.toUpperCase());
  });
}

function mudarEstilo(chave, valor) {
  estado.estilo[chave] = valor;
  if (chave === "tag" || chave === "headline") {
    if (estado.bloco) estado.headlinesPorBloco[estado.bloco.id] = { tag: estado.estilo.tag, headline: estado.estilo.headline };
  } else {
    const paraGuardar = Object.assign({}, estado.estilo);
    delete paraGuardar.headline;
    guardarLocal("indomavel.estilo", paraGuardar);
  }
  if (CAMPOS_LAYOUT.includes(chave)) pedirLayoutAdiado();
  if (chave === "zoom" || chave.startsWith("enquadramento") || chave === "cortar_topo") posicionarPlayer();
  if (["cor_legenda", "cor_destaque", "fonte_legenda", "maiusculas", "destacar_palavra", "legenda"].includes(chave)) aplicarEstiloLegenda();
  if (chave === "max_palavras") {
    estado.trechosChave = "";
    pedirTrechosAdiado();
  }
  preencherControlesEstilo();
  atualizarResumoExportacao();
}

function vincularEstilo() {
  const etiquetas = $("etiquetas");
  ETIQUETAS.forEach((etiqueta) => {
    const opcao = el("option");
    opcao.value = etiqueta;
    etiquetas.appendChild(opcao);
  });
  montarCores("cores-legenda", "cor_legenda", CORES_LEGENDA);
  montarCores("cores-destaque", "cor_destaque", CORES_DESTAQUE);
  document.querySelectorAll("[data-estilo]").forEach((controle) => {
    const evento = controle.type === "checkbox" || controle.tagName === "SELECT" ? "change" : "input";
    controle.addEventListener(evento, () => {
      let valor = controle.type === "checkbox" ? controle.checked : controle.value;
      if (controle.type === "range") valor = Number(valor);
      if ((controle.dataset.estilo === "tag" || controle.dataset.estilo === "headline") && estado.bloco) {
        estado.headlineEditada[estado.bloco.id] = true;
      }
      mudarEstilo(controle.dataset.estilo, valor);
    });
  });
  $("restaurar-estilo").addEventListener("click", () => {
    const { tag, headline } = estado.estilo;
    estado.estilo = Object.assign({}, estado.estiloPadrao, { tag, headline });
    guardarLocal("indomavel.estilo", {});
    estado.trechosChave = "";
    preencherControlesEstilo();
    pedirLayoutAdiado();
    posicionarPlayer();
    aplicarEstiloLegenda();
    pedirTrechosAdiado();
  });
  $("btn-sugerir").addEventListener("click", () => sugerirHeadlines(true));
}

async function sugerirHeadlines(forcar) {
  const bloco = estado.bloco;
  if (!bloco || !estado.video || estado.inicio == null) return;
  const chave = estado.video.youtube_id + ":" + estado.inicio.toFixed(1) + "-" + estado.fim.toFixed(1);
  if (!forcar && chave === estado.sugestoesChave) return;
  estado.sugestoesChave = chave;
  const lista = $("sugestoes");
  lista.innerHTML = "";
  lista.appendChild(el("li", "carregando", "Pedindo sugestões ao Gemini…"));
  try {
    const dados = await postar("/api/headlines", {
      youtube_id: estado.video.youtube_id, inicio: estado.inicio, fim: estado.fim, titulo: bloco.titulo, resumo: bloco.resumo,
      categoria: bloco.categoria || "", temas: bloco.temas || [], destaques: bloco.destaques.map((destaque) => destaque.texto),
    });
    if (chave !== estado.sugestoesChave) return;
    lista.innerHTML = "";
    estado.ultimasSugestoes = dados.opcoes || [];
    if (!dados.opcoes.length) lista.appendChild(el("li", "vazio-lista", "O Gemini não devolveu sugestões."));
    dados.opcoes.forEach((opcao) => {
      const item = el("li");
      const botao = el("button", "sugestao");
      botao.type = "button";
      botao.append(el("span", "sugestao-tag", opcao.tag), el("span", "sugestao-texto", opcao.headline), el("span", "sugestao-angulo", opcao.angulo));
      botao.addEventListener("click", () => {
        if (estado.bloco) estado.headlineEditada[estado.bloco.id] = true;
        mudarEstilo("tag", opcao.tag);
        mudarEstilo("headline", opcao.headline);
        marcarAnguloAtivo(opcao.angulo);
        if (estado.video && estado.bloco) {
          postar("/api/headlines/feedback", {
            youtube_id: estado.video.youtube_id,
            bloco_id: estado.bloco.id,
            tag: opcao.tag,
            headline: opcao.headline,
            angulo: opcao.angulo,
            acao: "escolhido_pelo_editor",
          }).catch(() => {});
        }
      });
      item.appendChild(botao);
      lista.appendChild(item);
    });
    const temas = [bloco.categoria].concat(bloco.temas || []).filter(Boolean);
    $("base-sugestoes").textContent = temas.length ? "Baseadas no tema do bloco: " + temas.join(" · ") : "";
    // Enquanto você não escreveu nem escolheu uma headline para este bloco, o card já usa a primeira sugestão.
    if (dados.opcoes.length && estado.bloco === bloco && !estado.headlineEditada[bloco.id]) {
      mudarEstilo("tag", dados.opcoes[0].tag);
      mudarEstilo("headline", dados.opcoes[0].headline);
      marcarAnguloAtivo(dados.opcoes[0].angulo);
    }
  } catch (erro) {
    if (chave !== estado.sugestoesChave) return;
    lista.innerHTML = "";
    lista.appendChild(el("li", "erro-lista", "Sem sugestões agora: " + erro.message));
  }
}

function marcarAnguloAtivo(angulo) {
  document.querySelectorAll(".btn-angulo").forEach((btn) => {
    btn.classList.toggle("ativo", btn.dataset.angulo === angulo);
  });
}

function aplicarAnguloHeadline(angulo) {
  const bloco = estado.bloco;
  if (!bloco) return;
  marcarAnguloAtivo(angulo);
  // Se houver sugestão com esse ângulo, aplica
  const opcao = (estado.ultimasSugestoes || []).find((o) => o.angulo === angulo);
  if (opcao) {
    if (estado.bloco) estado.headlineEditada[estado.bloco.id] = true;
    mudarEstilo("tag", opcao.tag);
    mudarEstilo("headline", opcao.headline);
    return;
  }
  // Fallback instantâneo calibrado para o ângulo
  const destaque = (bloco.destaques && bloco.destaques[0] && bloco.destaques[0].texto) || "";
  const alvo = (destaque || bloco.titulo || "Renan Santos comenta a política").replace(/["\n]/g, " ").trim();
  const alvoLimpo = alvo.length > 80 ? alvo.slice(0, 77) + "…" : alvo;

  let tag = "EM ALTA!";
  let headline = "";
  if (angulo === "noticioso") {
    tag = "EM ALTA!";
    headline = "Em análise, Renan Santos expõe: " + alvoLimpo;
  } else if (angulo === "confronto") {
    tag = "CONFRONTO!";
    headline = "Renan Santos confronta e dispara: “" + alvoLimpo + "”";
  } else if (angulo === "citacao") {
    tag = "MANDOU A REAL!!";
    headline = "“" + alvoLimpo + "” — Renan Santos";
  }
  if (headline.length > 160) headline = headline.slice(0, 157) + "…";
  if (estado.bloco) estado.headlineEditada[estado.bloco.id] = true;
  mudarEstilo("tag", tag);
  mudarEstilo("headline", headline);
}

function vincularAngulosHeadline() {
  document.querySelectorAll(".btn-angulo").forEach((btn) => {
    btn.addEventListener("click", () => aplicarAnguloHeadline(btn.dataset.angulo));
  });
}

const sugerirHeadlinesAdiado = adiar(() => sugerirHeadlines(false), 1200);

// ---------- painel: exportar ----------

function atualizarBlocosCrus() {
  const botao = $("btn-blocos-crus");
  if (!estado.video) {
    $("resumo-blocos-crus").textContent = "Abra um vídeo à esquerda.";
    botao.disabled = true;
    return;
  }
  if (!estado.blocos.length) {
    $("resumo-blocos-crus").textContent = "Este vídeo ainda não tem blocos no Chub: o YouTube ainda não gerou a legenda automática.";
    botao.disabled = true;
    return;
  }
  const soma = estado.blocos.reduce((total, bloco) => total + (bloco.fim - bloco.inicio), 0);
  $("resumo-blocos-crus").textContent = estado.blocos.length + " blocos · " + fmtDuracao(soma, true) + " de vídeo no total";
  botao.disabled = false;
}

function atualizarResumoExportacaoLote() {
  const resumo = $("resumo-exportar-lote");
  const botao = $("btn-exportar-lote");
  if (!resumo || !botao) return;
  if (!estado.video) {
    resumo.textContent = "Abra um vídeo com blocos à esquerda.";
    botao.disabled = true;
    return;
  }
  if (!estado.blocos.length) {
    resumo.textContent = "Este vídeo ainda não tem blocos disponíveis no Chub.";
    botao.disabled = true;
    return;
  }
  const validos = estado.blocos.filter((b) => {
    const d = b.duracao || (b.fim - b.inicio);
    return d >= 20 && d <= 90;
  });
  const contagem = validos.length || estado.blocos.length;
  resumo.textContent = `${contagem} cortes válidos identificados (duração ideal < 90s, preferencialmente 45-75s com +200ms VAD).`;
  botao.disabled = false;
}

async function baixarBlocosCrus() {
  if (!estado.video || !estado.blocos.length) return;
  const botao = $("btn-blocos-crus");
  botao.disabled = true;
  try {
    await postar("/api/videos/" + estado.video.youtube_id + "/blocos-crus", {});
    avisar("Todos os blocos na fila. Primeiro o vídeo inteiro é baixado na maior qualidade.", "ok");
    await atualizarTarefas();
  } catch (erro) {
    avisar("Não deu para baixar os blocos: " + erro.message, "erro");
  } finally {
    atualizarBlocosCrus();
  }
}

function atualizarResumoExportacao() {
  atualizarBlocosCrus();
  atualizarResumoExportacaoLote();
  if (estado.inicio == null) {
    $("resumo-exportacao").textContent = "Escolha um bloco e ajuste o corte.";
    return;
  }
  const [largura, altura] = FORMATOS[estado.formato];
  const comCard = $("exp-headline") ? $("exp-headline").checked : estado.estilo.card;
  const comLeg = $("exp-legenda") ? $("exp-legenda").checked : estado.estilo.legenda;
  const card = comCard && (estado.estilo.tag || estado.estilo.headline)
    ? "card “" + [estado.estilo.tag, estado.estilo.headline].filter(Boolean).join(" ") + "”"
    : "sem card";
  $("resumo-exportacao").textContent = estado.formato + " (" + largura + "×" + altura + ") · " + fmtDuracao(estado.fim - estado.inicio, true) +
    " · " + card + " · " + (comLeg ? "com legenda" : "sem legenda") + " · " + (estado.estilo.rodape ? "com marca d'água" : "sem marca d'água");
}

async function exportar() {
  if (!estado.video || estado.inicio == null) return;
  const botao = $("btn-exportar");
  const quickBotao = $("btn-quick-exportar");
  if (botao) botao.disabled = true;
  if (quickBotao) {
    quickBotao.disabled = true;
    quickBotao.textContent = "Adicionando…";
  }
  try {
    const comLegenda = $("exp-legenda") ? $("exp-legenda").checked : estado.estilo.legenda;
    const comHeadline = $("exp-headline") ? $("exp-headline").checked : estado.estilo.card;
    const estiloFinal = Object.assign({}, estado.estilo, { legenda: comLegenda, card: comHeadline });
    await postar("/api/exportar", {
      youtube_id: estado.video.youtube_id,
      inicio: estado.inicio,
      fim: estado.fim,
      formato: estado.formato,
      estilo: estiloFinal,
      com_legenda: comLegenda,
      com_headline: comHeadline,
      titulo: estado.estilo.headline || (estado.bloco && estado.bloco.titulo) || estado.video.titulo,
      trechos: (estado.trechos && estado.trechos.length) ? estado.trechos : null,
    });
    avisar("Exportação adicionada à fila! Processando em segundo plano.", "ok");
    abrirDockExportacao();
    await atualizarTarefas();
  } catch (erro) {
    avisar("Não deu para exportar: " + erro.message, "erro");
  } finally {
    const valido = estado.inicio != null && estado.fim != null && estado.fim > estado.inicio;
    if (botao) botao.disabled = !valido;
    if (quickBotao) {
      quickBotao.disabled = !valido;
      quickBotao.textContent = "⚡ Exportar";
    }
  }
}

async function exportarLote() {
  if (!estado.video || !estado.blocos.length) return;
  const botao = $("btn-exportar-lote");
  botao.disabled = true;
  botao.textContent = "Adicionando à fila…";
  try {
    const formato = $("lote-formato") ? $("lote-formato").value : estado.formato;
    const comLegenda = $("lote-legenda") ? $("lote-legenda").checked : true;
    const comHeadline = $("lote-headline") ? $("lote-headline").checked : true;
    const manterBruto = $("lote-manter-bruto") ? $("lote-manter-bruto").checked : false;
    const comMarca = $("lote-marca") ? $("lote-marca").checked : true;
    await postar(`/api/videos/${estado.video.youtube_id}/exportar-lote`, {
      formato: formato,
      com_legenda: comLegenda,
      com_headline: comHeadline,
      com_marca: comMarca,
      manter_bruto: manterBruto,
      estilo: Object.assign({}, estado.estilo, { legenda: comLegenda, card: comHeadline, rodape: comMarca }),
    });
    avisar("Exportação em lote adicionada à fila (" + (comMarca ? "com" : "sem") + " marca d'água). Processando em segundo plano.", "ok");
    abrirDockExportacao();
    await atualizarTarefas();
  } catch (erro) {
    avisar("Erro na exportação em lote: " + erro.message, "erro");
  } finally {
    botao.disabled = false;
    botao.textContent = "Exportar Todos os Cortes Válidos";
  }
}

async function abrirPastaCortes() {
  try {
    await postar("/api/cortes/pasta");
  } catch (erro) {
    avisar("Não foi possível abrir a pasta: " + erro.message, "erro");
  }
}

async function baixarCru() {
  if (!estado.video || estado.inicio == null) return;
  try {
    await postar("/api/trechos", {
      youtube_id: estado.video.youtube_id, inicio: estado.inicio, fim: estado.fim, legenda: true,
      titulo: (estado.bloco && estado.bloco.titulo) || estado.video.titulo,
    });
    avisar("Trecho na fila de download.", "ok");
    abrirDockExportacao();
    await atualizarTarefas();
  } catch (erro) {
    avisar("Não deu para baixar: " + erro.message, "erro");
  }
}

function abrirDockExportacao() {
  const dock = $("dock-exportacao");
  if (dock) {
    dock.hidden = false;
    dock.classList.remove("minimizado");
  }
}

function toggleDockExportacao() {
  const dock = $("dock-exportacao");
  if (dock) {
    if (dock.hidden) {
      dock.hidden = false;
      dock.classList.remove("minimizado");
    } else {
      dock.classList.toggle("minimizado");
    }
  }
}

async function atualizarTarefas() {
  clearTimeout(atualizarTarefas.espera);
  try {
    estado.tarefas = (await api("/api/trechos")).tarefas;
  } catch (erro) {
    $("contador-tarefas").textContent = "(erro)";
    return;
  }
  const lista = $("lista-tarefas");
  if (lista) {
    lista.innerHTML = "";
    if (!estado.tarefas.length) lista.appendChild(el("li", "vazio-lista", "Nada exportado ainda."));
    estado.tarefas.forEach((tarefa) => lista.appendChild(itemTarefa(tarefa)));
  }
  desenharDockExportacao();
  const ativas = estado.tarefas.filter((tarefa) => tarefa.estado === "na_fila" || tarefa.estado === "baixando").length;
  const texto = ativas ? "(" + ativas + " em andamento)" : (estado.tarefas.length ? "(" + estado.tarefas.length + ")" : "");
  $("contador-tarefas").textContent = texto;
  $("contador-painel").textContent = ativas ? "(" + ativas + ")" : "";
  if ($("dock-contador")) $("dock-contador").textContent = ativas ? ativas + " ativas" : "Concluído";
  if (ativas) atualizarTarefas.espera = setTimeout(atualizarTarefas, 1000);
}

function desenharDockExportacao() {
  const dock = $("dock-exportacao");
  const lista = $("dock-lista");
  if (!dock || !lista) return;
  const tarefasRecentes = (estado.tarefas || []).slice(0, 10);
  const ativas = tarefasRecentes.filter((t) => t.estado === "na_fila" || t.estado === "baixando").length;
  if (ativas > 0 && dock.hidden) {
    dock.hidden = false;
  }
  lista.innerHTML = "";
  if (!tarefasRecentes.length) {
    lista.appendChild(el("li", "dica", "Nenhuma exportação na fila."));
    return;
  }
  tarefasRecentes.forEach((tarefa) => {
    lista.appendChild(criarCardTelemetria(tarefa));
  });
}

function criarCardTelemetria(tarefa) {
  const item = el("li", "dock-item " + tarefa.estado);
  const topo = el("div", "dock-item-topo");
  const titulo = el("div", "dock-item-titulo", tarefa.titulo || tarefa.youtube_id);
  let rotuloFormato = tarefa.formato || (tarefa.tipo === "blocos_crus" ? "RAW" : "9:16");
  if (tarefa.tipo === "exportacao_lote") {
    rotuloFormato += " · " + (tarefa.feitos || 0) + "/" + (tarefa.blocos ? tarefa.blocos.length : 0) + (tarefa.com_marca === false ? " · sem marca" : " · com marca");
  }
  const formato = el("span", "dock-item-formato", rotuloFormato);
  topo.append(titulo, formato);

  const progresso = typeof tarefa.progresso === "number" ? tarefa.progresso : (tarefa.estado === "pronta" ? 100 : 0);
  const emAndamento = tarefa.estado !== "pronta" && tarefa.estado !== "falhou" && tarefa.estado !== "na_fila";
  const barra = el("div", "telemetria-barra");
  const preenchimento = el("div", "telemetria-progresso" + (emAndamento ? " animada" : ""));
  preenchimento.style.width = Math.max(0, Math.min(100, progresso)) + "%";
  barra.appendChild(preenchimento);

  const status = el("div", "telemetria-status");
  const etapaClasse = tarefa.etapa || (tarefa.estado === "pronta" ? "pronta" : tarefa.estado);
  const badge = el("span", "badge-etapa " + etapaClasse, tarefa.etapa_nome || NOMES_ESTADO[tarefa.estado] || tarefa.estado);
  const info = el("span", "telemetria-eta");
  let textoInfo = Math.round(progresso) + "%";
  if (tarefa.eta_s != null && tarefa.estado !== "pronta" && tarefa.estado !== "falhou") {
    textoInfo += " · faltam ~" + fmtDuracao(tarefa.eta_s);
  }
  info.textContent = textoInfo;
  status.append(badge, info);

  item.append(topo, barra, status);

  if (tarefa.mensagem && tarefa.mensagem !== (tarefa.etapa_nome || "")) {
    item.appendChild(el("div", "tarefa-msg", tarefa.mensagem));
  }

  if (tarefa.estado === "falhou") {
    item.appendChild(el("div", "tarefa-erro-msg", "❌ " + (tarefa.mensagem || "Falha na exportação")));
  }

  if (tarefa.estado === "pronta") {
    const acoes = el("div", "dock-item-acoes");
    const nomeArq = nomeArquivo(tarefa.arquivo_cortes || tarefa.arquivo);
    const rotulo = el("span", "dica", "✅ " + nomeArq);

    const btnAbrir = el("button", "botao-pri pequeno btn-abrir-video", "▶ Abrir Vídeo");
    btnAbrir.type = "button";
    btnAbrir.title = "Abrir vídeo exportado no player padrão do Windows";
    btnAbrir.addEventListener("click", async () => {
      try {
        await postar("/api/trechos/" + tarefa.id + "/abrir");
      } catch (erro) {
        avisar("Não deu para abrir o vídeo: " + erro.message, "erro");
      }
    });

    const btnMostrar = el("button", "botao-sec pequeno", "📂 Pasta");
    btnMostrar.type = "button";
    btnMostrar.title = "Localizar arquivo na pasta do sistema";
    btnMostrar.addEventListener("click", async () => {
      try {
        await postar("/api/trechos/" + tarefa.id + "/mostrar");
      } catch (erro) {
        avisar("Não deu para abrir a pasta: " + erro.message, "erro");
      }
    });

    acoes.append(btnAbrir, btnMostrar);
    item.append(rotulo, acoes);
  }
  return item;
}

function itemTarefa(tarefa) {
  const item = el("li", "tarefa " + tarefa.estado);
  let tipo = tarefa.tipo === "exportacao" ? "Vídeo pronto " + (tarefa.formato || "9:16") : "Trecho sem edição";
  let alcance = fmtTempo(tarefa.inicio, 1) + " a " + fmtTempo(tarefa.fim, 1);
  if (tarefa.tipo === "blocos_crus") {
    tipo = "Todos os blocos, sem edição";
    alcance = (tarefa.feitos || 0) + " de " + (tarefa.blocos ? tarefa.blocos.length : 0) + " blocos";
  } else if (tarefa.tipo === "exportacao_lote") {
    tipo = "Exportação em lote (" + (tarefa.formato || "9:16") + ", " + (tarefa.com_marca === false ? "sem" : "com") + " marca d'água)";
    alcance = (tarefa.feitos || 0) + " de " + (tarefa.blocos ? tarefa.blocos.length : 0) + " cortes";
  }

  const progresso = typeof tarefa.progresso === "number" ? tarefa.progresso : (tarefa.estado === "pronta" ? 100 : 0);
  const emAndamento = tarefa.estado !== "pronta" && tarefa.estado !== "falhou" && tarefa.estado !== "na_fila";
  const barra = el("div", "telemetria-barra");
  const preenchimento = el("div", "telemetria-progresso" + (emAndamento ? " animada" : ""));
  preenchimento.style.width = Math.max(0, Math.min(100, progresso)) + "%";
  barra.appendChild(preenchimento);

  const status = el("div", "telemetria-status");
  const etapaClasse = tarefa.etapa || (tarefa.estado === "pronta" ? "pronta" : tarefa.estado);
  const badge = el("span", "badge-etapa " + etapaClasse, tarefa.etapa_nome || NOMES_ESTADO[tarefa.estado] || tarefa.estado);
  const info = el("span", "telemetria-eta");
  let textoInfo = Math.round(progresso) + "%";
  if (tarefa.eta_s != null && tarefa.estado !== "pronta" && tarefa.estado !== "falhou") {
    textoInfo += " · faltam ~" + fmtDuracao(tarefa.eta_s);
  }
  info.textContent = textoInfo;
  status.append(badge, info);

  item.append(
    el("div", "tarefa-titulo", tarefa.titulo || tarefa.youtube_id),
    el("div", "tarefa-msg", tipo + " · " + alcance),
    barra,
    status,
  );

  if (tarefa.mensagem && tarefa.mensagem !== (tarefa.etapa_nome || "")) {
    item.appendChild(el("div", "tarefa-msg", tarefa.mensagem));
  }

  if (tarefa.estado === "falhou") {
    item.appendChild(el("div", "tarefa-erro-msg", "❌ " + (tarefa.mensagem || "Falha na exportação")));
  }

  if (tarefa.estado === "pronta") {
    const acoes = el("div", "dock-item-acoes");
    const nomeArq = nomeArquivo(tarefa.arquivo_cortes || tarefa.arquivo);
    const rotulo = el("span", "dica", "✅ " + nomeArq + (tarefa.legenda ? " + .srt" : ""));

    const btnAbrir = el("button", "botao-pri pequeno btn-abrir-video", "▶ Abrir Vídeo");
    btnAbrir.type = "button";
    btnAbrir.title = "Abrir vídeo exportado no player padrão do Windows";
    btnAbrir.addEventListener("click", async () => {
      try {
        await postar("/api/trechos/" + tarefa.id + "/abrir");
      } catch (erro) {
        avisar("Não deu para abrir o vídeo: " + erro.message, "erro");
      }
    });

    const botao = el("button", "botao-sec pequeno", "📂 Mostrar na pasta");
    botao.type = "button";
    botao.title = "Localizar arquivo na pasta do sistema";
    botao.addEventListener("click", async () => {
      try {
        await postar("/api/trechos/" + tarefa.id + "/mostrar");
      } catch (erro) {
        avisar("Não deu para abrir a pasta: " + erro.message, "erro");
      }
    });

    acoes.append(btnAbrir, botao);
    item.append(rotulo, acoes);
  }
  return item;
}

function trocarPainel(nome) {
  estado.painel = nome;
  document.querySelectorAll(".aba[data-painel]").forEach((aba) => aba.classList.toggle("ativa", aba.dataset.painel === nome));
  $("painel-blocos").hidden = nome !== "blocos";
  $("painel-estilo").hidden = nome !== "estilo";
  $("painel-exportar").hidden = nome !== "exportar";
  $("painel-automatico").hidden = nome !== "automatico";
  if ($("painel-live")) $("painel-live").hidden = nome !== "live";
  if (nome === "exportar") atualizarTarefas();
  if (nome === "live") carregarLiveStatus();
  if (nome === "automatico") {
    estado.assinaturaAutomatico = "";
    carregarAutomacao();
    desenharVideosMassa();
    carregarMassa();
  }
}

function trocarFormato(formato) {
  estado.formato = formato;
  guardarLocal("indomavel.formato", formato);
  document.querySelectorAll("[data-formato]").forEach((botao) => botao.classList.toggle("ativo", botao.dataset.formato === formato));
  aplicarLayout();
  pedirLayout();
  atualizarResumoExportacao();
}

// ---------- painel: automático ----------

async function carregarAutomacao() {
  clearTimeout(carregarAutomacao.espera);
  try {
    estado.automacao = await api("/api/automacao");
  } catch (erro) {
    $("auto-mensagem").textContent = "Não carregou a automação: " + erro.message;
    return;
  }
  desenharAutomacao();
  const exportando = estado.automacao.cortes.some((corte) => corte.estado === "exportando" || corte.estado === "na_fila");
  if (estado.automacao.rodando || exportando) carregarAutomacao.espera = setTimeout(carregarAutomacao, 3000);
  else if (estado.painel === "automatico") carregarAutomacao.espera = setTimeout(carregarAutomacao, 15000);
}

function preencherSeLivre(id, valor) {
  const controle = $(id);
  if (document.activeElement === controle) return;
  if (controle.type === "checkbox") controle.checked = Boolean(valor);
  else controle.value = String(valor);
}

function desenharAutomacao() {
  const dados = estado.automacao;
  if (!dados) return;
  const configuracao = dados.config;
  preencherSeLivre("auto-ligada", configuracao.ligada);
  preencherSeLivre("auto-dias", configuracao.dias);
  preencherSeLivre("auto-intervalo", configuracao.intervalo_min);
  preencherSeLivre("auto-formato", configuracao.formato);
  preencherSeLivre("auto-max", configuracao.max_cortes_por_rodada);
  $("auto-max-valor").textContent = configuracao.max_cortes_por_rodada;
  let mensagem = dados.rodando ? "Rodando agora: " + dados.mensagem : dados.mensagem;
  if (!dados.rodando && dados.proxima_rodada) {
    mensagem += ". Próxima rodada às " + new Date(dados.proxima_rodada * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) + ".";
  }
  $("auto-mensagem").textContent = mensagem;
  $("auto-rodar").disabled = dados.rodando;
  $("auto-rodar").textContent = dados.rodando ? "Rodando…" : "Rodar agora";
  const paraRevisar = dados.cortes.filter((corte) => corte.estado === "para_revisar").length;
  $("contador-automatico").textContent = paraRevisar ? "(" + paraRevisar + ")" : "";

  const filtro = estado.filtroAutomatico;
  const visiveis = dados.cortes.filter((corte) => (filtro === "para_revisar"
    ? ["para_revisar", "exportando", "na_fila", "falhou"].includes(corte.estado)
    : corte.estado === filtro));
  const assinatura = filtro + "|" + visiveis.map((corte) => corte.id + ":" + corte.estado).join(",");
  // Sem mudança nos cortes, não recria a lista: não interrompe um vídeo que alguém está assistindo.
  if (assinatura === estado.assinaturaAutomatico) return;
  estado.assinaturaAutomatico = assinatura;
  const lista = $("auto-cortes");
  lista.innerHTML = "";
  if (!visiveis.length) {
    lista.appendChild(el("li", "vazio-lista", filtro === "para_revisar"
      ? "Nenhum corte esperando revisão. Ligue a automação ou clique em Rodar agora."
      : "Nada aqui ainda."));
  }
  visiveis.forEach((corte) => lista.appendChild(itemCorteAutomatico(corte, dados.motivos_descarte)));
}

function itemCorteAutomatico(corte, motivos) {
  const item = el("li", "auto-corte estado-" + corte.estado);
  if (corte.arquivo && (corte.estado === "para_revisar" || corte.estado === "aprovado")) {
    const video = el("video");
    video.src = "/api/automacao/cortes/" + corte.id + "/video";
    video.controls = true;
    video.preload = "metadata";
    video.playsInline = true;
    item.appendChild(video);
  }
  item.append(
    el("div", "auto-tag", corte.tag),
    el("div", "auto-headline", corte.headline),
    el("div", "tarefa-msg", corte.video_titulo + " · " + fmtTempo(corte.inicio) + " a " + fmtTempo(corte.fim) + " (" + fmtDuracao(corte.fim - corte.inicio) + ")"),
    el("div", "tarefa-msg", (NOMES_ORIGEM[corte.origem] || corte.origem) + (corte.motivo ? " · " + corte.motivo : "")),
  );
  if (corte.estado === "exportando" || corte.estado === "na_fila") item.appendChild(el("div", "video-estado", "Exportando…"));
  if (corte.estado === "falhou") item.appendChild(el("div", "video-estado estado-falhou", "Falhou: " + (corte.aviso || "")));
  if (corte.estado === "descartado") item.appendChild(el("div", "video-estado estado-falhou", "Descartado: " + (corte.motivo_descarte || "")));
  const botoes = el("div", "auto-botoes");
  const botao = (texto, classe, acao) => {
    const elemento = el("button", classe, texto);
    elemento.type = "button";
    elemento.addEventListener("click", acao);
    botoes.appendChild(elemento);
  };
  if (corte.estado === "para_revisar") {
    botao("Aprovar", "botao-pri pequeno", () => decidirCorte(corte.id, "aprovar"));
    const motivo = el("select");
    (motivos || []).forEach((texto) => {
      const opcao = el("option", null, texto);
      opcao.value = texto;
      motivo.appendChild(opcao);
    });
    botoes.appendChild(motivo);
    botao("Descartar", "botao-sec pequeno", () => decidirCorte(corte.id, "descartar", motivo.value));
  }
  if (corte.arquivo && corte.estado !== "exportando") {
    botao("Mostrar na pasta", "botao-sec pequeno", async () => {
      try {
        await api("/api/automacao/cortes/" + corte.id + "/mostrar", { method: "POST" });
      } catch (erro) {
        avisar("Não deu para abrir a pasta: " + erro.message, "erro");
      }
    });
  }
  botao("Ajustar no editor", "botao-sec pequeno", () => abrirCorteNoEditor(corte));
  item.appendChild(botoes);
  return item;
}

async function salvarConfigAutomacao() {
  try {
    await postar("/api/automacao/config", {
      ligada: $("auto-ligada").checked,
      dias: Number($("auto-dias").value),
      intervalo_min: Number($("auto-intervalo").value),
      formato: $("auto-formato").value,
      max_cortes_por_rodada: Number($("auto-max").value),
    });
    await carregarAutomacao();
  } catch (erro) {
    avisar("Não deu para salvar a automação: " + erro.message, "erro");
  }
}

async function rodarAutomacao() {
  try {
    await postar("/api/automacao/rodar", {});
    avisar("Rodada começando. Os cortes aparecem aqui conforme ficam prontos.", "ok");
    setTimeout(carregarAutomacao, 1200);
  } catch (erro) {
    avisar("Não deu para rodar: " + erro.message, "erro");
  }
}

async function decidirCorte(ident, acao, motivo) {
  try {
    await postar("/api/automacao/cortes/" + ident, { acao: acao, motivo: motivo || "" });
    avisar(acao === "aprovar"
      ? "Aprovado: o arquivo foi para downloads\\aprovados."
      : "Descartado. O motivo fica guardado para calibrar a automação.", "ok");
    estado.assinaturaAutomatico = "";
    await carregarAutomacao();
  } catch (erro) {
    avisar("Não deu para registrar: " + erro.message, "erro");
  }
}

function abrirCorteNoEditor(corte) {
  estado.corteAoAbrir = corte;
  trocarPainel("estilo");
  if (estado.origemLista !== "chub") trocarOrigem("chub");
  const video = estado.videos.find((item) => item.youtube_id === corte.youtube_id) ||
    { youtube_id: corte.youtube_id, titulo: corte.video_titulo, origem: "chub", duracao_s: 0, fontes: "" };
  abrirVideo(video);
}

function abrirCorteAutomatico(corte) {
  if (corte.formato && FORMATOS[corte.formato]) trocarFormato(corte.formato);
  definirCorte(corte.inicio, corte.fim);
  ajustarVista();
  mudarEstilo("tag", corte.tag);
  mudarEstilo("headline", corte.headline);
  irPara(corte.inicio, false);
}

// ---------- em massa ----------

function desenharVideosMassa() {
  const lista = $("massa-videos");
  lista.innerHTML = "";
  const locais = estado.locais.filter((video) => video.estado === "pronto");
  const videos = estado.videos.concat(locais);
  if (!videos.length) {
    lista.appendChild(el("li", "vazio-lista", "Carregue a lista de vídeos do Chub à esquerda."));
  }
  videos.forEach((video) => {
    const item = el("li");
    const rotulo = el("label");
    const caixa = el("input");
    caixa.type = "checkbox";
    caixa.checked = estado.massaSelecionados.has(video.youtube_id);
    caixa.addEventListener("change", () => {
      if (caixa.checked) estado.massaSelecionados.add(video.youtube_id);
      else estado.massaSelecionados.delete(video.youtube_id);
      $("massa-contagem").textContent = estado.massaSelecionados.size;
    });
    const texto = el("span", null, video.titulo);
    texto.appendChild(el("small", null, [fmtData(video.publicado_em), video.blocos + " blocos", video.origem === "local" ? "meu link" : ""].filter(Boolean).join(" · ")));
    rotulo.append(caixa, texto);
    item.appendChild(rotulo);
    lista.appendChild(item);
  });
  $("massa-contagem").textContent = estado.massaSelecionados.size;
}

async function comecarMassa() {
  const modo = document.querySelector("input[name=massa-modo]:checked").value;
  if (!estado.massaSelecionados.size) {
    avisar("Marque pelo menos um vídeo.", "erro");
    return;
  }
  try {
    await postar("/api/massa", {
      modo: modo,
      videos: Array.from(estado.massaSelecionados),
      por_video: Number($("massa-quantos").value),
      so_prontos: $("massa-so-prontos").checked,
      formato: $("massa-formato").value,
    });
    avisar(modo === "editar" ? "Edição em massa começando." : "Download em massa começando.", "ok");
    await carregarMassa();
  } catch (erro) {
    avisar("Não deu para começar: " + erro.message, "erro");
  }
}

async function carregarMassa() {
  clearTimeout(carregarMassa.espera);
  try {
    estado.massa = (await api("/api/massa")).trabalhos;
  } catch (erro) {
    return;
  }
  const lista = $("massa-trabalhos");
  lista.innerHTML = "";
  estado.massa.forEach((trabalho) => lista.appendChild(itemTrabalhoMassa(trabalho)));
  if (estado.massa.some((trabalho) => trabalho.estado === "na_fila" || trabalho.estado === "rodando")) {
    carregarMassa.espera = setTimeout(carregarMassa, 2500);
  }
}

function itemTrabalhoMassa(trabalho) {
  const classe = trabalho.estado === "pronto" ? " pronta" : (trabalho.estado === "falhou" ? " falhou" : "");
  const item = el("li", "tarefa" + classe);
  const nome = trabalho.modo === "editar" ? "Edição automática" : "Download dos melhores momentos";
  const prontos = trabalho.itens.filter((parte) => parte.estado === "pronta").length;
  item.append(
    el("div", "tarefa-titulo", nome + " · " + trabalho.videos.length + " vídeo(s) · " + trabalho.por_video + " por vídeo"),
    el("div", "tarefa-msg", trabalho.mensagem),
    el("div", "tarefa-msg", prontos + " de " + trabalho.itens.length + " arquivo(s) prontos"),
  );
  if (trabalho.itens.length) {
    const partes = el("ul", "massa-itens");
    trabalho.itens.forEach((parte) => {
      const texto = (NOMES_ESTADO[parte.estado] || parte.estado) + " · " + (parte.tag ? parte.tag + " " : "") + parte.titulo +
        " (" + fmtDuracao(parte.fim - parte.inicio) + ")" + (parte.legenda ? " + .srt" : "");
      partes.appendChild(el("li", "estado-" + parte.estado, texto));
    });
    item.appendChild(partes);
  }
  if (trabalho.pasta) {
    const botao = el("button", "botao-sec pequeno", "Abrir a pasta");
    botao.type = "button";
    botao.addEventListener("click", async () => {
      try {
        await api("/api/massa/" + trabalho.id + "/abrir", { method: "POST" });
      } catch (erro) {
        avisar("Não deu para abrir a pasta: " + erro.message, "erro");
      }
    });
    item.appendChild(botao);
  }
  return item;
}

// ---------- servidor: conexão e versão ----------

async function verificarServidor() {
  clearTimeout(verificarServidor.espera);
  let dados = null;
  try {
    const resposta = await fetch("/api/vivo", { cache: "no-store" });
    dados = await resposta.json();
  } catch (erro) {
    estado.semConexao = true;
    mostrarFaixa("Sem conexão com o servidor do Indomável. Abra o Iniciar_Indomavel.bat de novo.", false);
    verificarServidor.espera = setTimeout(verificarServidor, 5000);
    return;
  }
  const eraProblema = estado.semConexao || estado.servidorAntigo;
  estado.semConexao = false;
  if (dados.app !== "indomavel" || !dados.versao) {
    estado.servidorAntigo = true;
    mostrarFaixa("Esta tela está falando com um servidor antigo do Indomável. Feche a janela do servidor e abra o Iniciar_Indomavel.bat de novo.", false);
  } else if (estado.versaoServidor && dados.versao !== estado.versaoServidor) {
    mostrarFaixa("O Indomável foi atualizado. Recarregue a página para usar a versão nova.", true);
  } else {
    estado.servidorAntigo = false;
    estado.versaoServidor = dados.versao;
    $("faixa-servidor").hidden = true;
    if (eraProblema) {
      estado.layoutChave = "";
      pedirLayout();
    }
  }
  verificarServidor.espera = setTimeout(verificarServidor, 15000);
}

function mostrarFaixa(texto, comBotao) {
  $("faixa-texto").textContent = texto;
  $("faixa-botao").hidden = !comBotao;
  $("faixa-servidor").hidden = false;
}

// ---------- ligações ----------

function teclado(evento) {
  const alvo = evento.target.tagName;
  if (alvo === "INPUT" || alvo === "SELECT" || alvo === "TEXTAREA") return;
  if (!estado.video) return;
  const tecla = evento.key.toLowerCase();
  if (evento.code === "Space") {
    alternarPlay();
  } else if (tecla === "i") {
    marcarAgora("inicio");
  } else if (tecla === "o") {
    marcarAgora("fim");
  } else if (evento.key === "ArrowLeft" || evento.key === "ArrowRight") {
    const passo = (evento.shiftKey ? 0.1 : 1) * (evento.key === "ArrowLeft" ? -1 : 1);
    buscar(Math.max(0, tempoAtual() + passo), true);
  } else {
    return;
  }
  evento.preventDefault();
}

// ---------- painel: gravação de live ----------

async function carregarLiveStatus() {
  try {
    const dados = await api("/api/live/status");
    atualizarUiLive(dados);
  } catch (e) {
    // Silencioso em caso de erro passageiro de rede
  }
}

function formatarTempoS(segundos) {
  const s = Math.floor(segundos || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const seg = s % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(seg).padStart(2, "0")}`;
}

function formatarMinutos(segundos) {
  const m = Math.floor((segundos || 0) / 60);
  const s = Math.floor((segundos || 0) % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatarEstadoLive(est) {
  const mapa = {
    gravando: "🔴 Gravando",
    gravado: "💾 Gravado",
    analisando_ia: "🧠 Analisando IA",
    pronto_upload: "📤 Pronto p/ Upload",
    enviando: "🚀 Enviando",
    enviado: "✅ Enviado",
    erro: "⚠️ Erro",
  };
  return mapa[est] || est;
}

function atualizarUiLive(dados) {
  if (!dados) return;
  const gravando = Boolean(dados.gravando);
  const sessao = dados.sessao_ativa;
  const partes = dados.partes || [];

  // Top header badge
  const badgeHeader = $("badge-live-rec");
  if (badgeHeader) {
    badgeHeader.hidden = !gravando;
    if (gravando && sessao) {
      const decorrido = Math.floor(Date.now() / 1000) - (sessao.criado_em || Math.floor(Date.now() / 1000));
      $("texto-badge-live").textContent = `GRAVANDO LIVE (${formatarTempoS(decorrido)})`;
    }
  }

  // Live panel counter badge
  const contadorAba = $("contador-live");
  if (contadorAba) {
    contadorAba.textContent = gravando ? "●" : (partes.length ? `(${partes.length})` : "");
    contadorAba.style.color = gravando ? "#ff4757" : "";
  }

  // Panel buttons and badges
  const badgePainel = $("live-badge-status");
  const btnIniciar = $("btn-iniciar-live");
  const btnParar = $("btn-parar-live");
  const cardMonitor = $("live-card-monitor");

  if (badgePainel) {
    badgePainel.textContent = gravando ? "GRAVANDO AO VIVO" : (sessao ? "FINALIZADO" : "PRONTO");
    badgePainel.className = "chip " + (gravando ? "chip-gravando-live" : "chip-sucesso");
  }

  if (btnIniciar) btnIniciar.hidden = gravando;
  if (btnParar) btnParar.hidden = !gravando;

  if (cardMonitor) {
    cardMonitor.hidden = !sessao && !gravando;
    if (sessao) {
      $("live-monitor-titulo").textContent = sessao.titulo || sessao.youtube_id;
      const decorrido = (gravando ? Math.floor(Date.now() / 1000) : (sessao.atualizado_em || Math.floor(Date.now() / 1000))) - (sessao.criado_em || Math.floor(Date.now() / 1000));
      $("live-tempo-decorrido").textContent = formatarTempoS(Math.max(0, decorrido));
      $("live-bloco-atual").textContent = `Parte ${(partes.length + 1)}`;
      $("live-pasta-info").textContent = `📁 ${sessao.pasta_destino}`;
    }
  }

  // Render partes
  const listaPartes = $("live-lista-partes");
  const contadorPartes = $("live-contador-partes");
  if (contadorPartes) contadorPartes.textContent = partes.length;

  if (listaPartes) {
    if (!partes.length) {
      listaPartes.innerHTML = '<li class="vazio-lista">Nenhuma parte gravada nesta sessão ainda.</li>';
      return;
    }
    listaPartes.innerHTML = "";
    partes.forEach((parte) => {
      const li = el("li", "live-parte-item");

      const topo = el("div", "live-parte-topo");
      const tit = el("strong", "live-parte-titulo", `Parte ${parte.numero_parte} [${formatarMinutos(parte.inicio_s)} - ${formatarMinutos(parte.fim_s)}]`);
      const statusBadge = el("span", `live-parte-status live-status-${parte.estado}`, formatarEstadoLive(parte.estado));
      topo.appendChild(tit);
      topo.appendChild(statusBadge);
      li.appendChild(topo);

      if (parte.erro_mensagem) {
        const erroBox = el("div", "dica", `⚠️ ${parte.erro_mensagem}`);
        erroBox.style.color = "#ff6b6b";
        li.appendChild(erroBox);
      }

      // Detalhes expansíveis (Capítulos e Resumo)
      if (parte.resumo || parte.capitulos) {
        const detalhes = el("div", "live-detalhes-bloco");
        if (parte.capitulos) {
          const detCap = el("details", "");
          const sumCap = el("summary", "", "⏱ Capítulos do YouTube");
          const preCap = el("pre", "", parte.capitulos);
          detCap.appendChild(sumCap);
          detCap.appendChild(preCap);
          detalhes.appendChild(detCap);
        }
        if (parte.resumo) {
          const detRes = el("details", "");
          const sumRes = el("summary", "", "📋 Resumo IA");
          const pRes = el("p", "dica", parte.resumo);
          pRes.style.color = "#eee";
          detRes.appendChild(sumRes);
          detRes.appendChild(pRes);
          detalhes.appendChild(detRes);
        }
        li.appendChild(detalhes);
      }

      // Ações do bloco
      const acoes = el("div", "dock-item-acoes");
      acoes.style.marginTop = "6px";

      if (parte.youtube_url) {
        const linkYt = el("a", "botao-pri pequeno", "Ver no YouTube ↗");
        linkYt.href = parte.youtube_url;
        linkYt.target = "_blank";
        linkYt.rel = "noopener";
        acoes.appendChild(linkYt);
      }

      const btnAbrir = el("button", "botao-sec pequeno", "📁 Abrir Arquivo");
      btnAbrir.type = "button";
      btnAbrir.addEventListener("click", () => {
        api(`/api/live/partes/${parte.id}/abrir`, { method: "POST" }).catch((err) => avisar(err.message, "erro"));
      });
      acoes.appendChild(btnAbrir);

      const btnReprocessar = el("button", "botao-sec pequeno", "🔄 Reprocessar IA");
      btnReprocessar.type = "button";
      btnReprocessar.addEventListener("click", async () => {
        try {
          await api(`/api/live/partes/${parte.id}/reprocessar`, { method: "POST" });
          avisar(`Reprocessando IA da Parte ${parte.numero_parte}...`);
          carregarLiveStatus();
        } catch (err) {
          avisar(err.message, "erro");
        }
      });
      acoes.appendChild(btnReprocessar);

      li.appendChild(acoes);
      listaPartes.appendChild(li);
    });
  }
}

async function carregarModoCortes() {
  try {
    const dados = await api("/api/automacao/cortes/status");
    estado.modoAutoCortes = Boolean(dados.modo_automatico);
    atualizarBotaoModoCortes();
  } catch (e) {
    // silencioso
  }
}

async function carregarConfigCortes() {
  try {
    const res = await api("/api/automacao/config-cortes");
    if (res && res.config) {
      estado.configCortes = res.config;
    }
  } catch (e) {
    // silencioso
  }
}

async function atualizarStatusDriveModal() {
  const badge = $("drive-status-badge");
  const msg = $("drive-status-msg");
  const btnAuth = $("btn-conectar-oauth-drive");
  const boxManual = $("box-codigo-manual");
  if (!badge) return;
  badge.textContent = "Verificando...";
  badge.className = "badge-drive";
  try {
    const res = await api("/api/drive/status");
    if (res.conectado && res.oauth_conectado) {
      badge.textContent = "☁️ Nuvem Ativa (OAuth)";
      badge.className = "badge-drive nuvem";
      if (msg) msg.textContent = `Conectado à nuvem pessoal na pasta ${res.pasta_id}. Uploads automáticos ativos.`;
      if (btnAuth) {
        btnAuth.textContent = "✅ Conta Google Conectada";
        btnAuth.style.background = "#2ed573";
        btnAuth.style.borderColor = "#2ed573";
        btnAuth.disabled = true;
      }
      if (boxManual) boxManual.style.display = "none";
    } else if (res.conectado && res.tipo_credencial === "service_account") {
      badge.textContent = "⚠️ Cota 0b (Requer OAuth)";
      badge.className = "badge-drive local";
      if (msg) msg.textContent = res.mensagem;
      if (btnAuth) {
        btnAuth.textContent = "🔑 Conectar Conta Google Pessoal";
        btnAuth.style.background = "#ff9f43";
        btnAuth.style.borderColor = "#ff9f43";
        btnAuth.disabled = false;
        if (res.url_autorizacao) {
          btnAuth.dataset.url = res.url_autorizacao;
        }
      }
      if (boxManual) boxManual.style.display = res.oauth_configurado ? "block" : "none";
    } else {
      badge.textContent = "📁 Fallback Local";
      badge.className = "badge-drive local";
      if (msg) msg.textContent = res.mensagem || `Salva localmente em ${res.pasta_local}.`;
      if (btnAuth) {
        btnAuth.textContent = "🔑 Conectar Conta Google";
        btnAuth.style.background = "#4285F4";
        btnAuth.style.borderColor = "#4285F4";
        btnAuth.disabled = false;
        if (res.url_autorizacao) {
          btnAuth.dataset.url = res.url_autorizacao;
        }
      }
      if (boxManual) boxManual.style.display = res.oauth_configurado ? "block" : "none";
    }
  } catch (e) {
    badge.textContent = "⚠️ Erro Conexão";
    badge.className = "badge-drive";
    if (msg) msg.textContent = "Não foi possível verificar status da nuvem: " + e.message;
  }
}

function abrirModalConfigCortes() {
  const modal = $("modal-config-cortes");
  if (!modal) return;
  const cfg = estado.configCortes || {
    proporcao: "1:1",
    video_inicial_id: "",
    variacoes: { com_legenda: false, sem_legenda: true, so_legenda: false, cru: true, headlines: true },
    visual: { marca_dagua: false, cortar_topo: 140, tamanho_headline: 44 },
    drive: { pasta_id: "1wBxCAat68t-jLBl3RJxCBJmZNvPAjz-G" }
  };

  // Ponto de Partida dos Cortes
  const selVideo = $("cfg-video-inicial");
  if (selVideo) {
    const selecionadoAtual = cfg.video_inicial_id || "";
    selVideo.innerHTML = `
      <option value="">▶️ Todos os vídeos elegíveis da playlist</option>
      <option value="__apenas_novos__">🆕 Apenas novos vídeos adicionados a partir de agora</option>
    `;
    if (Array.isArray(estado.videosPlaylist)) {
      estado.videosPlaylist.forEach(v => {
        const opt = document.createElement("option");
        opt.value = v.youtube_id;
        opt.textContent = `A partir de: ${v.titulo || v.youtube_id}`;
        selVideo.appendChild(opt);
      });
    }
    selVideo.value = selecionadoAtual;
  }

  // Proporção
  const radio = document.querySelector(`input[name="proporcao"][value="${cfg.proporcao || '1:1'}"]`);
  if (radio) radio.checked = true;

  // Variações
  const vars = cfg.variacoes || {};
  if ($("chk-var-sem-legenda")) $("chk-var-sem-legenda").checked = vars.sem_legenda !== false;
  if ($("chk-var-com-legenda")) $("chk-var-com-legenda").checked = Boolean(vars.com_legenda);
  if ($("chk-var-so-legenda")) $("chk-var-so-legenda").checked = Boolean(vars.so_legenda);
  if ($("chk-var-cru")) $("chk-var-cru").checked = vars.cru !== false;
  if ($("chk-var-headlines")) $("chk-var-headlines").checked = vars.headlines !== false;

  // Visual
  const vis = cfg.visual || {};
  if ($("chk-vis-marca")) $("chk-vis-marca").checked = Boolean(vis.marca_dagua);
  if ($("cfg-cortar-topo")) {
    $("cfg-cortar-topo").value = vis.cortar_topo != null ? vis.cortar_topo : 140;
    if ($("cfg-cortar-topo-val")) $("cfg-cortar-topo-val").textContent = $("cfg-cortar-topo").value;
  }
  if ($("cfg-tamanho-hl")) {
    $("cfg-tamanho-hl").value = vis.tamanho_headline != null ? vis.tamanho_headline : 44;
    if ($("cfg-tamanho-hl-val")) $("cfg-tamanho-hl-val").textContent = $("cfg-tamanho-hl").value;
  }

  // Drive
  const drv = cfg.drive || {};
  if ($("cfg-drive-pasta-id")) $("cfg-drive-pasta-id").value = drv.pasta_id || "1wBxCAat68t-jLBl3RJxCBJmZNvPAjz-G";

  atualizarStatusDriveModal();
  if (typeof modal.showModal === "function") {
    modal.showModal();
  } else {
    modal.hidden = false;
  }
}

function fecharModalConfigCortes() {
  const modal = $("modal-config-cortes");
  if (!modal) return;
  if (typeof modal.close === "function") {
    modal.close();
  } else {
    modal.hidden = true;
  }
}

async function salvarConfigCortes(evento) {
  if (evento) evento.preventDefault();
  const radioProp = document.querySelector('input[name="proporcao"]:checked');
  const proporcao = radioProp ? radioProp.value : "1:1";
  const videoInicial = $("cfg-video-inicial") ? $("cfg-video-inicial").value : "";

  const dados = {
    proporcao: proporcao,
    video_inicial_id: videoInicial,
    variacoes: {
      sem_legenda: $("chk-var-sem-legenda") ? $("chk-var-sem-legenda").checked : true,
      com_legenda: $("chk-var-com-legenda") ? $("chk-var-com-legenda").checked : false,
      so_legenda: $("chk-var-so-legenda") ? $("chk-var-so-legenda").checked : false,
      cru: $("chk-var-cru") ? $("chk-var-cru").checked : true,
      headlines: $("chk-var-headlines") ? $("chk-var-headlines").checked : true,
    },
    visual: {
      marca_dagua: $("chk-vis-marca") ? $("chk-vis-marca").checked : false,
      cortar_topo: $("cfg-cortar-topo") ? Number($("cfg-cortar-topo").value) : 140,
      tamanho_headline: $("cfg-tamanho-hl") ? Number($("cfg-tamanho-hl").value) : 44,
    },
    drive: {
      pasta_id: $("cfg-drive-pasta-id") ? $("cfg-drive-pasta-id").value.trim() : "1wBxCAat68t-jLBl3RJxCBJmZNvPAjz-G",
      upload_ativo: true,
    }
  };

  try {
    const res = await postar("/api/automacao/config-cortes", dados);
    estado.configCortes = res.config;
    fecharModalConfigCortes();
    avisar("Preferências de cortes salvas com sucesso!", "ok");
  } catch (err) {
    avisar("Erro ao salvar preferências: " + err.message, "erro");
  }
}

function atualizarBotaoModoCortes() {
  const btn = $("btn-toggle-auto-cortes");
  if (!btn) return;
  if (estado.modoAutoCortes) {
    btn.textContent = "⚡ Cortes: LIGADO";
    btn.style.background = "#2ed573";
    btn.style.borderColor = "#2ed573";
    btn.style.color = "#000";
    btn.style.fontWeight = "bold";
  } else {
    btn.textContent = "⚡ Cortes: DESLIGADO";
    btn.style.background = "";
    btn.style.borderColor = "";
    btn.style.color = "";
    btn.style.fontWeight = "";
  }
}

function vincular() {
  $("form-link").addEventListener("submit", (evento) => {
    evento.preventDefault();
    if ($("link").value.trim()) enviarLink($("link").value.trim(), false);
  });
  document.querySelectorAll(".aba[data-origem]").forEach((aba) => aba.addEventListener("click", () => trocarOrigem(aba.dataset.origem)));
  if ($("btn-toggle-auto-cortes")) {
    $("btn-toggle-auto-cortes").addEventListener("click", async () => {
      const btn = $("btn-toggle-auto-cortes");
      btn.disabled = true;
      try {
        const dados = await postar("/api/automacao/cortes/toggle", { ativo: !estado.modoAutoCortes });
        estado.modoAutoCortes = Boolean(dados.modo_automatico);
        atualizarBotaoModoCortes();
        if (estado.modoAutoCortes) {
          const prop = (estado.configCortes && estado.configCortes.proporcao) || "1:1";
          avisar(`⚡ Cortes automáticos ATIVADOS em formato ${prop} para Google Drive.`, "ok");
        } else {
          avisar(dados.mensagem || "Modo de cortes alterado", "ok");
        }
      } catch (e) {
        avisar("Erro ao alterar modo: " + e.message, "erro");
      } finally {
        btn.disabled = false;
      }
    });
  }
  if ($("btn-config-auto-cortes")) {
    $("btn-config-auto-cortes").addEventListener("click", () => abrirModalConfigCortes());
  }
  if ($("btn-fechar-modal-cortes")) {
    $("btn-fechar-modal-cortes").addEventListener("click", () => fecharModalConfigCortes());
  }
  if ($("btn-cancelar-config-cortes")) {
    $("btn-cancelar-config-cortes").addEventListener("click", () => fecharModalConfigCortes());
  }
  if ($("form-config-cortes")) {
    $("form-config-cortes").addEventListener("submit", salvarConfigCortes);
  }
  if ($("btn-testar-drive")) {
    $("btn-testar-drive").addEventListener("click", async () => {
      avisar("Testando conexão com o Google Drive...", "ok");
      await atualizarStatusDriveModal();
    });
  }
  if ($("btn-abrir-pasta-drive")) {
    $("btn-abrir-pasta-drive").addEventListener("click", async () => {
      try {
        await postar("/api/drive/abrir-pasta-local", {});
        avisar("Abrindo pasta dos cortes no Windows Explorer...", "ok");
      } catch (e) {
        avisar("Erro ao abrir pasta: " + e.message, "erro");
      }
    });
  }
  if ($("btn-abrir-pasta-cortes-topo")) {
    $("btn-abrir-pasta-cortes-topo").addEventListener("click", async () => {
      try {
        await postar("/api/drive/abrir-pasta-local", {});
        avisar("Abrindo pasta dos cortes no Windows Explorer...", "ok");
      } catch (e) {
        avisar("Erro ao abrir pasta: " + e.message, "erro");
      }
    });
  }
  if ($("btn-enviar-credenciais")) {
    $("btn-enviar-credenciais").addEventListener("click", async () => {
      const fileInput = $("input-arquivo-credenciais");
      if (!fileInput || !fileInput.files.length) {
        avisar("Selecione um arquivo .json de credenciais primeiro!", "erro");
        return;
      }
      const arquivo = fileInput.files[0];
      const leitor = new FileReader();
      leitor.onload = async (evt) => {
        try {
          const conteudo = evt.target.result;
          JSON.parse(conteudo);
          await postar("/api/drive/configurar", { credenciais: conteudo });
          avisar("Credenciais do Google Drive salvas com sucesso!", "ok");
          await atualizarStatusDriveModal();
        } catch (err) {
          avisar("Erro no arquivo de credenciais: " + err.message, "erro");
        }
      };
      leitor.readAsText(arquivo, "UTF-8");
    });
  }
  if ($("btn-conectar-oauth-drive")) {
    $("btn-conectar-oauth-drive").addEventListener("click", async () => {
      try {
        const res = await api("/api/drive/auth-url");
        if (res && res.url) {
          window.open(res.url, "_blank", "width=600,height=700");
          avisar("Janela de login do Google aberta! Conceda permissão para a pasta do Drive.", "ok");
          const intv = setInterval(async () => {
            const st = await api("/api/drive/status");
            if (st && (st.oauth_conectado || st.tipo_credencial === 'oauth_token')) {
              clearInterval(intv);
              avisar("Google Drive conectado com sucesso na nuvem!", "ok");
              atualizarStatusDriveModal();
            }
          }, 3000);
          setTimeout(() => clearInterval(intv), 120000);
        }
      } catch (err) {
        avisar("Erro ao iniciar login Google: " + err.message, "erro");
      }
    });
  }
  if ($("btn-salvar-codigo-oauth")) {
    $("btn-salvar-codigo-oauth").addEventListener("click", async () => {
      const cod = $("input-codigo-oauth") ? $("input-codigo-oauth").value.trim() : "";
      if (!cod) {
        avisar("Cole o código ou URL de autorização primeiro!", "erro");
        return;
      }
      try {
        await postar("/api/drive/conectar-codigo", { codigo: cod });
        avisar("Google Drive conectado com sucesso na nuvem!", "ok");
        if ($("input-codigo-oauth")) $("input-codigo-oauth").value = "";
        await atualizarStatusDriveModal();
      } catch (err) {
        avisar("Falha ao validar código: " + err.message, "erro");
      }
    });
  }
  if ($("cfg-cortar-topo")) {
    $("cfg-cortar-topo").addEventListener("input", (e) => {
      if ($("cfg-cortar-topo-val")) $("cfg-cortar-topo-val").textContent = e.target.value;
    });
  }
  if ($("cfg-tamanho-hl")) {
    $("cfg-tamanho-hl").addEventListener("input", (e) => {
      if ($("cfg-tamanho-hl-val")) $("cfg-tamanho-hl-val").textContent = e.target.value;
    });
  }
  if ($("btn-sync-playlist")) {
    $("btn-sync-playlist").addEventListener("click", async () => {
      $("btn-sync-playlist").disabled = true;
      try {
        await postar("/api/playlist/sincronizar", {});
        avisar("Sincronizando playlist com o YouTube...", "ok");
        if ($("playlist-status-texto")) $("playlist-status-texto").textContent = "Sincronizando YouTube…";
        if ($("playlist-ponto-sync")) $("playlist-ponto-sync").className = "ponto-sync sincronizando";
        setTimeout(carregarPlaylist, 1500);
      } catch (e) {
        avisar("Erro ao sincronizar playlist: " + e.message, "erro");
      } finally {
        setTimeout(() => { if ($("btn-sync-playlist")) $("btn-sync-playlist").disabled = false; }, 2000);
      }
    });
  }
  document.querySelectorAll(".aba[data-painel]").forEach((aba) => aba.addEventListener("click", () => trocarPainel(aba.dataset.painel)));
  document.querySelectorAll(".aba[data-ordem]").forEach((aba) => aba.addEventListener("click", () => {
    document.querySelectorAll(".aba[data-ordem]").forEach((outra) => outra.classList.toggle("ativa", outra === aba));
    estado.ordem = aba.dataset.ordem;
    desenharBlocos();
  }));
  document.querySelectorAll("[data-formato]").forEach((botao) => botao.addEventListener("click", () => trocarFormato(botao.dataset.formato)));
  const buscarVideos = adiar(() => {
    estado.busca = $("busca").value.trim();
    carregarVideos(true);
  }, 350);
  $("busca").addEventListener("input", buscarVideos);
  $("fonte").addEventListener("change", () => {
    estado.fonte = $("fonte").value;
    carregarVideos(true);
  });
  $("mais-videos").addEventListener("click", () => {
    estado.pagina += 1;
    carregarVideos(false);
  });

  $("play").addEventListener("click", alternarPlay);

  let arrastoVideo = null;
  const cliquePlay = $("clique-play");
  cliquePlay.addEventListener("pointerdown", (e) => {
    arrastoVideo = {
      startX: e.clientX,
      startY: e.clientY,
      origX: estado.estilo.enquadramento_x || 0,
      origY: estado.estilo.enquadramento_y || 0,
      origCortar: estado.estilo.cortar_topo || 0,
      moved: false,
      pointerId: e.pointerId,
    };
    try { cliquePlay.setPointerCapture(e.pointerId); } catch (err) {}
  });
  cliquePlay.addEventListener("pointermove", (e) => {
    if (!arrastoVideo || arrastoVideo.pointerId !== e.pointerId) return;
    const dx = e.clientX - arrastoVideo.startX;
    const dy = e.clientY - arrastoVideo.startY;
    if (!arrastoVideo.moved && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
      arrastoVideo.moved = true;
      cliquePlay.classList.add("arrastando");
    }
    if (!arrastoVideo.moved) return;

    const area = $("area-video");
    const larguraArea = area.clientWidth || 1;
    const alturaArea = area.clientHeight || 1;
    const escala = estado.escala || 1;

    const deltaX = (dx / (larguraArea * 0.45));
    const novoX = limitar(arrastoVideo.origX - deltaX, -1, 1);
    estado.estilo.enquadramento_x = Number(novoX.toFixed(2));

    const zoom = estado.estilo.zoom || 1.0;
    if (e.shiftKey || zoom <= 1.05) {
      const deltaCortar = -dy / escala;
      estado.estilo.cortar_topo = Math.max(0, Math.min(300, Math.round(arrastoVideo.origCortar + deltaCortar)));
    } else {
      const deltaY = (dy / (alturaArea * 0.45));
      const novoY = arrastoVideo.origY - deltaY;
      if (novoY < -1.0) {
        estado.estilo.enquadramento_y = -1.0;
        const excedente = (-1.0 - novoY) * (alturaArea * 0.45) / escala;
        estado.estilo.cortar_topo = Math.max(0, Math.min(300, Math.round(arrastoVideo.origCortar + excedente)));
      } else if (novoY > 1.0) {
        estado.estilo.enquadramento_y = 1.0;
      } else {
        estado.estilo.enquadramento_y = Number(novoY.toFixed(2));
      }
    }

    posicionarPlayer();
    preencherControlesEstilo();
  });
  const finalizarArrastoVideo = (e) => {
    if (!arrastoVideo) return;
    const foiArrasto = arrastoVideo.moved;
    try { cliquePlay.releasePointerCapture(arrastoVideo.pointerId); } catch (err) {}
    cliquePlay.classList.remove("arrastando");
    arrastoVideo = null;
    if (!foiArrasto) {
      alternarPlay();
    } else {
      const paraGuardar = Object.assign({}, estado.estilo);
      delete paraGuardar.headline;
      guardarLocal("indomavel.estilo", paraGuardar);
      pedirLayoutAdiado();
    }
  };
  cliquePlay.addEventListener("pointerup", finalizarArrastoVideo);
  cliquePlay.addEventListener("pointercancel", finalizarArrastoVideo);

  $("tocar-corte").addEventListener("click", tocarCorte);
  $("mudo").addEventListener("click", () => {
    if (!estado.player || !estado.playerPronto) return;
    if (estado.player.isMuted()) estado.player.unMute(); else estado.player.mute();
    setTimeout(atualizarTransporte, 150);
  });
  $("tela-cheia").addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else $("palco-externo").requestFullscreen().catch((erro) => avisar("Tela cheia indisponível: " + erro.message, "erro"));
  });
  document.addEventListener("fullscreenchange", () => setTimeout(aplicarLayout, 60));

  document.querySelectorAll("[data-ajuste]").forEach((botao) => botao.addEventListener("click", () => {
    const [borda, delta] = botao.dataset.ajuste.split(":");
    ajustar(borda, Number(delta));
  }));
  $("in-inicio").addEventListener("change", () => lerCampoTempo("inicio"));
  $("in-fim").addEventListener("change", () => lerCampoTempo("fim"));
  $("zoom-mais").addEventListener("click", () => aproximar(1 / 1.6, tempoAtual()));
  $("zoom-menos").addEventListener("click", () => aproximar(1.6, tempoAtual()));
  $("zoom-ajustar").addEventListener("click", ajustarVista);
  $("copiar-transcricao").addEventListener("click", copiarTranscricao);
  $("btn-exportar").addEventListener("click", exportar);
  if ($("btn-quick-exportar")) $("btn-quick-exportar").addEventListener("click", exportar);
  if ($("btn-reset-enquadramento")) $("btn-reset-enquadramento").addEventListener("click", () => {
    mudarEstilo("zoom", 1.0);
    mudarEstilo("enquadramento_x", 0.0);
    mudarEstilo("enquadramento_y", 0.0);
    mudarEstilo("cortar_topo", 0);
    posicionarPlayer();
    preencherControlesEstilo();
    avisar("Enquadramento restaurado ao padrão.", "ok");
  });
  if ($("btn-exportar-lote")) $("btn-exportar-lote").addEventListener("click", exportarLote);
  if ($("btn-abrir-pasta-cortes")) $("btn-abrir-pasta-cortes").addEventListener("click", abrirPastaCortes);
  if ($("exp-legenda")) $("exp-legenda").addEventListener("change", atualizarResumoExportacao);
  if ($("exp-headline")) $("exp-headline").addEventListener("change", atualizarResumoExportacao);
  $("btn-baixar-cru").addEventListener("click", baixarCru);
  $("btn-blocos-crus").addEventListener("click", baixarBlocosCrus);
  $("btn-exportacoes").addEventListener("click", toggleDockExportacao);
  if ($("dock-btn-minimizar")) $("dock-btn-minimizar").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleDockExportacao();
  });
  if ($("dock-cabeca")) $("dock-cabeca").addEventListener("click", () => {
    const dock = $("dock-exportacao");
    if (dock && dock.classList.contains("minimizado")) toggleDockExportacao();
  });
  if ($("dock-btn-pasta")) $("dock-btn-pasta").addEventListener("click", (e) => {
    e.stopPropagation();
    abrirPastaCortes();
  });

  document.querySelectorAll(".pill-tatico").forEach((pill) => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".pill-tatico").forEach((p) => p.classList.remove("ativa"));
      pill.classList.add("ativa");
      estado.filtroTatico = pill.dataset.filtro || "todos";
      carregarVideos(true);
    });
  });
  vincularAngulosHeadline();

  const trilha = $("trilha");
  trilha.addEventListener("pointerdown", (evento) => iniciarArrasto(evento, "cabeca"));
  $("selecao").addEventListener("pointerdown", (evento) => {
    if (evento.target.dataset.alca) return;
    iniciarArrasto(evento, evento.shiftKey ? "mover" : "cabeca");
  });
  document.querySelectorAll(".alca").forEach((alca) => alca.addEventListener("pointerdown", (evento) => iniciarArrasto(evento, alca.dataset.alca)));
  $("cabeca-topo").addEventListener("pointerdown", (evento) => iniciarArrasto(evento, "cabeca"));
  document.addEventListener("pointermove", moverArrasto);
  document.addEventListener("pointerup", terminarArrasto);
  document.addEventListener("pointercancel", terminarArrasto);
  trilha.addEventListener("wheel", (evento) => {
    evento.preventDefault();
    if (evento.shiftKey) {
      const deslocamento = (estado.vista.fim - estado.vista.inicio) * 0.15 * Math.sign(evento.deltaY);
      definirVista(estado.vista.inicio + deslocamento, estado.vista.fim + deslocamento);
    } else {
      aproximar(evento.deltaY > 0 ? 1.25 : 0.8, tempoDoPonteiro(evento));
    }
  }, { passive: false });
  $("visao-geral").addEventListener("pointerdown", (evento) => {
    const total = duracaoTotal();
    if (!total) return;
    const caixa = $("visao-geral").getBoundingClientRect();
    const t = limitar((evento.clientX - caixa.left) / caixa.width * total, 0, total);
    const largura = estado.vista.fim - estado.vista.inicio;
    definirVista(t - largura / 2, t + largura / 2);
    buscar(t, true);
  });

  ["auto-ligada", "auto-dias", "auto-intervalo", "auto-formato"].forEach((id) => $(id).addEventListener("change", salvarConfigAutomacao));
  $("auto-max").addEventListener("input", () => { $("auto-max-valor").textContent = $("auto-max").value; });
  $("auto-max").addEventListener("change", salvarConfigAutomacao);
  $("auto-rodar").addEventListener("click", rodarAutomacao);
  $("massa-quantos").addEventListener("input", () => { $("massa-quantos-valor").textContent = $("massa-quantos").value; });
  $("massa-comecar").addEventListener("click", comecarMassa);
  $("massa-limpar").addEventListener("click", () => {
    estado.massaSelecionados.clear();
    desenharVideosMassa();
  });
  $("massa-marcar-dia").addEventListener("click", () => {
    const limite = Date.now() - 48 * 3600 * 1000;
    estado.videos.concat(estado.locais).forEach((video) => {
      if (video.publicado_em && new Date(video.publicado_em).getTime() >= limite && video.blocos) estado.massaSelecionados.add(video.youtube_id);
    });
    desenharVideosMassa();
  });
  $("faixa-botao").addEventListener("click", () => window.location.reload());
  document.querySelectorAll(".aba[data-filtro]").forEach((aba) => aba.addEventListener("click", () => {
    document.querySelectorAll(".aba[data-filtro]").forEach((outra) => outra.classList.toggle("ativa", outra === aba));
    estado.filtroAutomatico = aba.dataset.filtro;
    estado.assinaturaAutomatico = "";
    desenharAutomacao();
  }));

  // Gravação de live
  const btnIniciarLive = $("btn-iniciar-live");
  if (btnIniciarLive) {
    btnIniciarLive.addEventListener("click", async () => {
      const url = ($("live-url").value || "").trim();
      if (!url) {
        avisar("Informe o link da transmissão ao vivo.", "atencao");
        return;
      }
      const playlist_id = ($("live-playlist").value || "").trim();
      const dvr = $("live-dvr").checked;
      const duracao_chunk_s = parseInt($("live-duracao").value, 10) || 1800;
      btnIniciarLive.disabled = true;
      try {
        await api("/api/live/iniciar", {
          method: "POST",
          body: JSON.stringify({ url, playlist_id, dvr, duracao_chunk_s }),
        });
        avisar("Gravação iniciada! Acompanhe o progresso no painel.");
        carregarLiveStatus();
      } catch (err) {
        avisar("Erro ao iniciar gravação: " + err.message, "erro");
      } finally {
        btnIniciarLive.disabled = false;
      }
    });
  }

  const btnPararLive = $("btn-parar-live");
  if (btnPararLive) {
    btnPararLive.addEventListener("click", async () => {
      btnPararLive.disabled = true;
      try {
        await api("/api/live/parar", { method: "POST" });
        avisar("Gravação interrompida.");
        carregarLiveStatus();
      } catch (err) {
        avisar("Erro ao parar: " + err.message, "erro");
      } finally {
        btnPararLive.disabled = false;
      }
    });
  }

  const btnUsarAtual = $("live-btn-usar-atual");
  if (btnUsarAtual) {
    btnUsarAtual.addEventListener("click", () => {
      if (estado.video && estado.video.youtube_id) {
        $("live-url").value = `https://www.youtube.com/watch?v=${estado.video.youtube_id}`;
      } else {
        avisar("Nenhum vídeo selecionado à esquerda.", "atencao");
      }
    });
  }

  const btnLoginYt = $("btn-login-youtube");
  if (btnLoginYt) {
    btnLoginYt.addEventListener("click", async () => {
      try {
        await api("/api/live/login-youtube", { method: "POST" });
        avisar("Navegador aberto! Faça login no YouTube Studio e feche a janela quando terminar.");
      } catch (err) {
        avisar(err.message, "erro");
      }
    });
  }

  const btnLimparRetencao = $("btn-limpar-retencao");
  if (btnLimparRetencao) {
    btnLimparRetencao.addEventListener("click", async () => {
      try {
        const res = await api("/api/live/limpar-antigos", { method: "POST" });
        const liberadosMb = ((res.resultado && res.resultado.bytes_liberados) || 0) / (1024 * 1024);
        avisar(`Limpeza: ${res.resultado.removidos} vídeos limpos (${liberadosMb.toFixed(1)} MB liberados).`);
      } catch (err) {
        avisar(err.message, "erro");
      }
    });
  }

  new ResizeObserver(() => aplicarLayout()).observe($("palco-externo"));
  new ResizeObserver(() => desenharTrilha()).observe($("trilha"));
  document.addEventListener("keydown", teclado);
}

function laco() {
  if (estado.buscaPendente != null && performance.now() - estado.ultimaBusca >= INTERVALO_BUSCA_MS && !estado.arrasto) {
    buscar(estado.buscaPendente, true);
  } else if (estado.buscaPendente != null && performance.now() - estado.ultimaBusca >= INTERVALO_BUSCA_MS) {
    buscar(estado.buscaPendente);
  }
  const agora = tempoAtual();
  atualizarLegenda(agora);
  const cabeca = $("cabeca");
  const x = tempoParaX(agora);
  cabeca.hidden = x < -2 || x > larguraTrilha() + 2;
  cabeca.style.left = x + "px";
  const total = duracaoTotal();
  if (total) $("cabeca-geral").style.left = (100 * agora / total) + "%";
  $("tempo-atual").textContent = fmtTempo(agora, 1);
  $("tempo-no-corte").textContent = estado.inicio != null && agora >= estado.inicio && agora <= estado.fim
    ? "+" + fmtTempo(agora - estado.inicio, 1) + " de " + fmtTempo(estado.fim - estado.inicio, 1)
    : (estado.inicio != null ? "fora do corte" : "");
  if (estado.tocandoCorte && !estado.arrasto && estado.fim != null && agora >= estado.fim && estadoPlayer() === 1) {
    if ($("repetir").checked) {
      irPara(estado.inicio, true);
    } else {
      estado.player.pauseVideo();
      estado.tocandoCorte = false;
      atualizarTransporte();
    }
  }
  requestAnimationFrame(laco);
}

async function iniciar() {
  await verificarServidor();
  try {
    estado.estiloPadrao = (await api("/api/estilo")).estilo;
  } catch (erro) {
    avisar("O servidor não respondeu o estilo padrão: " + erro.message, "erro");
  }
  estado.estilo = Object.assign({}, estado.estiloPadrao, lerLocal("indomavel.estilo", {}), { headline: "" });
  estado.formato = FORMATOS[lerLocal("indomavel.formato", "9:16")] ? lerLocal("indomavel.formato", "9:16") : "9:16";
  document.querySelectorAll("[data-formato]").forEach((botao) => botao.classList.toggle("ativo", botao.dataset.formato === estado.formato));
  vincularEstilo();
  preencherControlesEstilo();
  vincular();
  requestAnimationFrame(laco);
  verificarChub();
  carregarFontes().catch((erro) => avisar("Fontes do Chub não carregaram: " + erro.message, "erro"));
  pedirLayout();
  atualizarResumoExportacao();
  await carregarVideos(true);
  carregarLocais();
  carregarPlaylist();
  atualizarTarefas();
  carregarAutomacao();
  carregarModoCortes();
  carregarConfigCortes();
  carregarMassa();
  carregarLiveStatus();
  setInterval(carregarLiveStatus, 3000);
}

iniciar();
