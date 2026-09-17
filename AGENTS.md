# Indomável — instruções para qualquer agente (Claude Code, Antigravity)

Leia inteiro antes de mudar qualquer arquivo. Cada linha aqui muda uma decisão.

## O que é

Ferramenta local para tirar cortes do Renan Santos **prontos para publicar** a partir do
**Campaign Hub (Chub)**. O Chub já faz o trabalho pesado: puxa os vídeos do YouTube,
divide em blocos, resume e marca momentos fortes (quem escreve os blocos é o
`openai/gpt-5.6-luna`). O Indomável **consome** esse resultado; não tenta replicá-lo.

Etapas, na ordem de prioridade do dono do projeto:

1. **Editor em tempo real** ✔ (15/09/2026): vídeos recentes do Chub, clique no bloco, preview
   no formato (9:16, 4:5, 1:1, 16:9) com card de etiqueta + headline, legenda palavra por
   palavra no estilo da Tropa e rodapé; trilha profissional com alças que controlam o player;
   exportação do vídeo pronto (e do trecho cru + .srt para o CapCut).
2. **Link de fora do Chub** ✔: cola o link, o programa baixa a legenda automática do YouTube
   (ou transcreve com Whisper se não houver) e divide em blocos com o Gemini, no formato do Chub.
3. **Automação** ✔: vigia vídeos novos do Chub, escolhe cortes (blocos prontos e trechos dentro
   de blocos longos pelo Gemini), escreve o card, exporta e deixa em "para revisar".
4. **Headlines** ✔: sugestões do Gemini no padrão dos cortes do Drive, construídas em torno da
   categoria, dos temas e dos momentos fortes do bloco; a primeira sugestão entra no card sozinha
   enquanto a pessoa não escreve ou escolhe outra.
5. **Em massa** ✔ (aba *Em massa*): marca vídeos e baixa os N melhores momentos de cada um pelo
   potencial do Chub (bloco com 2 s de folga + .srt), ou edita automaticamente (corte curto com card
   e headline; **legenda num .srt separado, não queimada**, porque a legenda automática pode ter erros).
   A automação que vigia vídeos novos também exporta com a legenda separada.
6. **Todos os blocos sem edição** ✔ (16/09/2026, aba *Exportar*, cartão "Todos os blocos deste vídeo")
   - **Download:** o vídeo inteiro é baixado **uma vez**, na maior qualidade do YouTube, em `PASTA_VIDEOS` (`D:\Indomavel\videos`).
     - `youtube.escolher_formatos` pega a maior resolução; nela, prefere H.264 a VP9/AV1, porque é o que qualquer editor abre.
     - As lives do Renan chegam só a 1080p30.
   - **Recorte sem perda** (`indomavel/recorte.py`): cada bloco do Chub é copiado sem recomprimir, com `-c copy`, sem usar a placa de vídeo.
     - Sem recomprimir, o corte só pode começar num quadro-chave, e nessas lives eles vêm a cada 1 a 7 s.
     - Por isso cada arquivo começa no último quadro-chave antes do bloco e termina 2 s depois dele.
     - A `.srt` é calculada a partir desse começo real.
   - **Saída:** `downloads/blocos sem edição/<vídeo>/NN - <título> (<início>).mp4` + `.srt`, na ordem do vídeo.
   - **Sem blocos no Chub:** vídeo sem blocos (a legenda do YouTube ainda não saiu, comum nas primeiras horas de uma live longa) → o botão fica desligado e a API responde 409.

## Como rodar

- Abrir: `Iniciar_Indomavel.bat` → `http://127.0.0.1:5055`. O `.bat` precisa ficar **sem acentos
  e com quebras de linha CRLF** (o teste `tests/test_lancador.py` confere).
- O `.bat` compara a versão do código (`indomavel/versao.py`, resumo dos arquivos) com a do servidor
  aberto (`/api/vivo`) e reinicia o servidor se forem diferentes. Em 15/09 um servidor antigo ficou
  aberto e a tela nova deu "erro 404" em tudo. A tela também mostra uma faixa vermelha quando o
  servidor é antigo, some ou foi atualizado.
- Testar uma cópia sem derrubar a aberta: `.venv\Scripts\python.exe rodar.py --porta 5056`.
- Ambiente próprio: `.venv\` (Python 3.13). Nunca use o venv de outro projeto.
- Testes sem rede: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
- Chub ao vivo: `.venv\Scripts\python.exe scripts\verificar_chub.py`
- Download real: `.venv\Scripts\python.exe scripts\testar_download.py`
- Exportação real (gera quadros PNG para olhar): `.venv\Scripts\python.exe scripts\testar_exportacao.py`
- Régua dos blocos contra o Chub: `.venv\Scripts\python.exe scripts\regua_blocos.py [ids]` (gasta muita cota do Gemini)
- JS: `C:\Users\nandi\AppData\Local\hermes\node\node.exe --check web\static\js\app.js`
- Testes da tela (Playwright, Chromium): `npx playwright test` (ou `npm run e2e`; `npm run e2e:ui` abre o modo
  visual). Ficam em `e2e/`, separados dos testes Python em `tests/`. O `playwright.config.js` sobe uma cópia
  na porta 5056 (ou reaproveita a que já estiver aberta) e usa o Chub de verdade. Relatório em
  `playwright-report/`. Reinstalar com `npm install` e `npx playwright install chromium`.

Antes de dizer que terminou, siga
[`.claude/skills/conferir-antes-de-entregar/SKILL.md`](.claude/skills/conferir-antes-de-entregar/SKILL.md).

## Estrutura

| Caminho | Papel |
| --- | --- |
| `indomavel/config.py` | Lê o `.env`, acha ffmpeg/node, lista de modelos do Gemini |
| `indomavel/chub.py` | Conexão com o Chub (MCP por HTTP); toda falha vira `ChubErro` |
| `indomavel/legendas.py` | Legenda automática do YouTube (json3) → palavras e frases no formato do Chub |
| `indomavel/palavras.py` | Tempo de cada palavra de um vídeo (cache em `dados/palavras`) |
| `indomavel/legenda.py` | `.srt` de uma linha e trechos curtos com palavra em destaque |
| `indomavel/render.py` | Layout, moldura PNG (card + rodapé), ASS da legenda e exportação com ffmpeg |
| `indomavel/youtube.py` | Download só do trecho (editor) ou do vídeo inteiro na maior qualidade, uma vez (`baixar_video_maximo`) |
| `indomavel/recorte.py` | Recorte sem perda (`-c copy`) a partir do último quadro-chave antes do bloco |
| `indomavel/tarefas.py` | Fila de trabalhos pesados (download cru e exportação), um por vez |
| `indomavel/gemini.py` | Chamadas ao Gemini em JSON, com troca de modelo em 503/429 |
| `indomavel/blocador.py` | Divide transcrição em blocos no formato do Chub (vídeos de fora) |
| `indomavel/transcricao_local.py` | Whisper na CPU, plano B sem legenda do YouTube |
| `indomavel/acervo_local.py`, `processador.py` | Vídeos de fora do Chub em `dados/videos/<id>/` e sua fila |
| `indomavel/headlines.py` | Sugestões de etiqueta + headline (cache em `dados/headlines`) |
| `indomavel/automacao.py` | Vigia, escolha de cortes, exportação e vereditos (`dados/automacao`) |
| `indomavel/massa.py` | Baixar ou editar em massa os melhores momentos de vários vídeos |
| `indomavel/versao.py` | Versão do código, usada pelo `.bat` e pela faixa de aviso da tela |
| `indomavel/servidor.py` | Servidor Flask e API |
| `web/` | Tela (HTML, CSS e JS separados) |
| `downloads/` | `<data>/` (tela), `em_massa/`, `automatico/<data>/`, `aprovados/<data>/` |
| `relatorios/` | Régua dos blocos e logs de testes longos |

## Fatos sobre o Chub que mudam o código

- O programa fala com o Chub sozinho: `CHUB_MCP_URL` no `.env` (a chave faz parte do endereço).
- **Falha nunca vira lista vazia.** Lista vazia significa que o Chub respondeu que não há nada.
- Fontes sincronizadas: lives e vídeos do canal do Renan, "Entrevistas do Renan", "Vídeos com ou
  sobre Renan" (contínuas) e Análises Renais (importado uma vez). **Não temos acesso para incluir
  vídeos nessas playlists** — por isso existe a etapa 2.
- O texto do Chub **é** a legenda automática do YouTube (99,8% das palavras iguais, mesmos tempos).
  O json3 do YouTube ainda dá o tempo de cada palavra, que o Chub não expõe: a legenda do editor
  e da exportação usa esse tempo.
- **Bloco não é short**: metade passa de 90 s; a pauta do Chub prefere blocos de 4–10 min.
- `renanSpeaking` do Chub é conservador (em comício do próprio Renan costuma vir `false`); na tela
  aparece como "Locutor não confirmado", nunca como "outra pessoa".
- Use `densityRank`/`selfContainedRank` (percentis), não os valores brutos.
- "Pronto para short" = Renan falando, não precisa de contexto, 20–120 s.

## Gemini (plano gratuito)

- A chave do `.env` é da API do Gemini (não Vertex). Modelos que responderam: `gemini-3.8-flash`,
  `gemini-3.6-flash`, `gemini-flash-latest`, `gemini-2.5-flash`. Os 3.x vivem dando 503 (alta demanda).
- A cota gratuita acaba rápido: em 15/09 a régua de 3 vídeos + um link + sugestões de headline
  estourou a cota (429). Por isso: modelo com 503 fica 10 min de lado e com 429 fica 30 min; a
  automação sem Gemini exporta só blocos prontos com o título do Chub como headline.
- Régua de 15/09 (Anúncio Especial, prompt antigo, gemini-2.5-flash): 26 blocos contra 30 do Chub,
  F1 dos inícios 0,39 (±2 frases), mediana 82 s contra 53 s, `renan_falando` igual em 46%. O prompt
  foi recalibrado depois (blocos menores, `renan_falando` e `precisa_contexto` mais conservadores),
  mas **ainda não foi medido de novo** por falta de cota. Os outros 2 vídeos da régua falharam por cota.

## Regras

- Não mexa em `C:\Users\nandi\missao-creator-studio` (demo de apresentação).
- Não escreva HTML/JS por PowerShell: here-strings trocam crases e `${}`. Use arquivos separados e `node --check`.
- Classes CSS são globais: um nome genérico (ex.: `.cabeca`) já quebrou a coluna da esquerda.
- Não coloque a chave do Chub nem a do Google em nada que vá para o navegador.
- A automação nunca publica nada: só exporta e espera revisão.
- Mudança observável na tela: conferir no navegador, não só no código.
- Skill `frontend-design` (anthropics/skills, em `.claude/skills` e `.agents/skills`, registrada em
  `skills-lock.json`): vale para a **interface** em `web/`. Ela manda evitar rótulos em maiúsculas e uma
  palavra de destaque colorida, mas isso **é** o padrão dos cortes do Drive (etiqueta em caixa alta com
  "!", palavra amarela na legenda): não mude o card, a legenda nem o rodapé do vídeo por causa dela.

## Referências fora desta pasta

- Cortes dos editores (molde de card, legenda, headline e duração):
  `G:\.shortcut-targets-by-id\18LbiYrzH4gAKj6ox8Qa0XlC1I8lmy03N\PARA APROVAÇÃO`
  — 436 cortes catalogados em `C:\Users\nandi\FuriaClipsData\database\editorial_learning.sqlite3`
  (tabela `drive_cuts_calibration`). Mediana de 71 s; 4:5, 9:16 e 1:1.
- Perfil de voz do Renan (ainda não usado): `C:\Users\nandi\missao-shorts\cache\renan_multi_centroid_profile.npz`.
- Histórico que **não** é base deste projeto: `missao-shorts` (Antigravity) e o repositório `SAGIEV007/furia-clips`.
