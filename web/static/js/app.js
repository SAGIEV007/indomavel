"use strict";

// Indomável — editor: escolhe o bloco, ajusta o corte na trilha, vê o vídeo pronto em tempo real e exporta.

const FORMATOS = { "9:16": [1080, 1920], "4:5": [1080, 1350], "1:1": [1080, 1080], "16:9": [1920, 1080] };
const NOMES_ESTADO = { na_fila: "Na fila", baixando: "Em andamento", pronta: "Pronto", falhou: "Falhou" };
const NOMES_ORIGEM = { bloco_pronto: "Bloco pronto do Chub", dentro_de_bloco: "Trecho escolhido dentro de um bloco longo" };
const ESTADOS_LOCAIS = {
  na_fila: "Na fila", legenda: "Buscando legenda", transcrevendo: "Transcrevendo", blocos: "Dividindo em blocos",
  pronto: "Pronto", falhou: "Falhou", interrompido: "Interrompido",
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
  origemLista: "chub", pagina: 0, fonte: "", busca: "", tokenVideos: 0, videos: [], locais: [],
  video: null, blocos: [], frases: [], ordem: "potencial", bloco: null,
  inicio: null, fim: null,
  player: null, playerPronto: false, pendente: null, duracao: 0, tocandoCorte: false,
  formato: "9:16", estilo: {}, estiloPadrao: {}, layout: null, layoutChave: "", escala: 1,
  trechos: [], trechosChave: "", ultimoTrecho: undefined,
  vista: { inicio: 0, fim: 60 }, arrasto: null, ultimaBusca: 0, buscaPendente: null, tempoVisual: null,
  headlinesPorBloco: {}, sugestoesChave: "", tarefas: [], painel: "blocos",
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
  lista.appendChild(carregando);
  const parametros = new URLSearchParams({ pagina: String(estado.pagina), fonte: estado.fonte, q: estado.busca });
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
  const situacao = video.blocos ? video.blocos + " blocos" : (video.tem_transcricao ? "legenda pronta, blocos a caminho" : "aguardando legenda");
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
  $("mais-videos").hidden = true;
  if (origem === "chub") {
    carregarVideos(true);
  } else {
    $("lista-videos").innerHTML = "";
    carregarLocais();
  }
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
  // Mesma conta do ffmpeg na exportação: cobre a área, aplica o zoom e desloca pelo enquadramento.
  const escala = Math.max(larguraArea / 1920, alturaArea / 1080) * (estado.estilo.zoom || 1);
  const larguraPlayer = 1920 * escala;
  const alturaPlayer = 1080 * escala;
  const caixa = $("player-caixa");
  caixa.style.width = larguraPlayer + "px";
  caixa.style.height = alturaPlayer + "px";
  caixa.style.left = (-(larguraPlayer - larguraArea) / 2 * (1 + (estado.estilo.enquadramento_x || 0))) + "px";
  caixa.style.top = (-(alturaPlayer - alturaArea) / 2 * (1 + (estado.estilo.enquadramento_y || 0))) + "px";
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
    const item = el("li", destaques.has(frase.i) ? "destaque" : "");
    item.dataset.inicio = frase.inicio;
    item.dataset.fim = frase.fim;
    const texto = el("span", null, frase.texto);
    if (frase.conferir) texto.appendChild(el("span", "conferir", "conferir no áudio"));
    const botoes = el("span", "frase-botoes");
    const botaoInicio = el("button", null, "Início");
    botaoInicio.type = "button";
    botaoInicio.title = "O corte começa nesta frase";
    botaoInicio.addEventListener("click", (evento) => {
      evento.stopPropagation();
      definirCorte(frase.inicio, Math.max(estado.fim, frase.inicio + CORTE_MINIMO_S));
      buscar(frase.inicio, true);
    });
    const botaoFim = el("button", null, "Fim");
    botaoFim.type = "button";
    botaoFim.title = "O corte termina nesta frase";
    botaoFim.addEventListener("click", (evento) => {
      evento.stopPropagation();
      definirCorte(Math.min(estado.inicio, frase.fim - CORTE_MINIMO_S), frase.fim);
      buscar(frase.fim, true);
    });
    botoes.append(botaoInicio, botaoFim);
    item.append(el("span", "quando", fmtTempo(frase.inicio)), texto, botoes);
    item.addEventListener("click", () => irPara(frase.inicio, true));
    lista.appendChild(item);
  }
  marcarFrasesNoCorte();
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
    const valor = estado.estilo[rotulo.dataset.valorDe];
    rotulo.textContent = typeof valor === "number" && !Number.isInteger(valor) ? valor.toFixed(2) : valor;
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
  if (chave === "zoom" || chave.startsWith("enquadramento")) posicionarPlayer();
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
    }
  } catch (erro) {
    if (chave !== estado.sugestoesChave) return;
    lista.innerHTML = "";
    lista.appendChild(el("li", "erro-lista", "Sem sugestões agora: " + erro.message));
  }
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
  if (estado.inicio == null) {
    $("resumo-exportacao").textContent = "Escolha um bloco e ajuste o corte.";
    return;
  }
  const [largura, altura] = FORMATOS[estado.formato];
  const card = estado.estilo.card && (estado.estilo.tag || estado.estilo.headline)
    ? "card “" + [estado.estilo.tag, estado.estilo.headline].filter(Boolean).join(" ") + "”"
    : "sem card";
  $("resumo-exportacao").textContent = estado.formato + " (" + largura + "×" + altura + ") · " + fmtDuracao(estado.fim - estado.inicio, true) +
    " · " + card + " · " + (estado.estilo.legenda ? "com legenda" : "sem legenda");
}

async function exportar() {
  if (!estado.video || estado.inicio == null) return;
  $("btn-exportar").disabled = true;
  try {
    await postar("/api/exportar", {
      youtube_id: estado.video.youtube_id, inicio: estado.inicio, fim: estado.fim, formato: estado.formato,
      estilo: estado.estilo, titulo: estado.estilo.headline || (estado.bloco && estado.bloco.titulo) || estado.video.titulo,
    });
    avisar("Exportação na fila. Acompanhe em Exportar.", "ok");
    trocarPainel("exportar");
    await atualizarTarefas();
  } catch (erro) {
    avisar("Não deu para exportar: " + erro.message, "erro");
  } finally {
    $("btn-exportar").disabled = estado.inicio == null;
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
    await atualizarTarefas();
  } catch (erro) {
    avisar("Não deu para baixar: " + erro.message, "erro");
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
  lista.innerHTML = "";
  if (!estado.tarefas.length) lista.appendChild(el("li", "vazio-lista", "Nada exportado ainda."));
  estado.tarefas.forEach((tarefa) => lista.appendChild(itemTarefa(tarefa)));
  const ativas = estado.tarefas.filter((tarefa) => tarefa.estado === "na_fila" || tarefa.estado === "baixando").length;
  const texto = ativas ? "(" + ativas + " em andamento)" : (estado.tarefas.length ? "(" + estado.tarefas.length + ")" : "");
  $("contador-tarefas").textContent = texto;
  $("contador-painel").textContent = ativas ? "(" + ativas + ")" : "";
  if (ativas) atualizarTarefas.espera = setTimeout(atualizarTarefas, 1500);
}

function itemTarefa(tarefa) {
  const item = el("li", "tarefa " + tarefa.estado);
  let tipo = tarefa.tipo === "exportacao" ? "Vídeo pronto " + tarefa.formato : "Trecho sem edição";
  let alcance = fmtTempo(tarefa.inicio, 1) + " a " + fmtTempo(tarefa.fim, 1);
  if (tarefa.tipo === "blocos_crus") {
    tipo = "Todos os blocos, sem edição";
    alcance = (tarefa.feitos || 0) + " de " + tarefa.blocos.length + " blocos";
  }
  item.append(
    el("div", "tarefa-titulo", tarefa.titulo || tarefa.youtube_id),
    el("div", "tarefa-msg", tipo + " · " + alcance),
    el("div", "tarefa-msg", NOMES_ESTADO[tarefa.estado] + " · " + tarefa.mensagem),
  );
  if (tarefa.estado === "pronta") {
    item.appendChild(el("div", "tarefa-msg", nomeArquivo(tarefa.arquivo) + (tarefa.legenda ? " + .srt" : "")));
    const botao = el("button", "botao-sec pequeno", "Mostrar na pasta");
    botao.type = "button";
    botao.addEventListener("click", async () => {
      try {
        await api("/api/trechos/" + tarefa.id + "/mostrar", { method: "POST" });
      } catch (erro) {
        avisar("Não deu para abrir a pasta: " + erro.message, "erro");
      }
    });
    item.appendChild(botao);
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
  if (nome === "exportar") atualizarTarefas();
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

function vincular() {
  $("form-link").addEventListener("submit", (evento) => {
    evento.preventDefault();
    if ($("link").value.trim()) enviarLink($("link").value.trim(), false);
  });
  document.querySelectorAll(".aba[data-origem]").forEach((aba) => aba.addEventListener("click", () => trocarOrigem(aba.dataset.origem)));
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
  $("clique-play").addEventListener("click", alternarPlay);
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
  $("btn-baixar-cru").addEventListener("click", baixarCru);
  $("btn-blocos-crus").addEventListener("click", baixarBlocosCrus);
  $("btn-exportacoes").addEventListener("click", () => trocarPainel("exportar"));

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
  atualizarTarefas();
  carregarAutomacao();
  carregarMassa();
}

iniciar();
