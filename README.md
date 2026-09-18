# INDOMÁVEL CREATOR STUDIO

Estação de trabalho editorial privada de alta performance para garimpar, editar, legendar e exportar cortes do YouTube e transmissões ao vivo a partir dos dados estruturados do Campaign Hub (Chub MCP).

---

## 🚀 Como Iniciar

### Opção 1: Atalho Automático
Dê um duplo clique no arquivo:
```cmd
C:\indomavel\Iniciar_Indomavel.bat
```
Ele configura o ambiente virtual Python se necessário e inicia a aplicação na porta **5055**:
👉 **http://127.0.0.1:5055**

### Opção 2: Linha de Comando (PowerShell / CMD)
```powershell
cd C:\indomavel
.venv\Scripts\python.exe rodar.py
```

---

## 🛠️ Arquitetura e Funcionalidades Implementadas

Desde o início do projeto, o Indomável Creator Studio foi desenhado e expandido com as seguintes frentes de engenharia:

### 1. Feed do Garimpo & Integração Chub MCP (`indomavel/chub.py`)
- Conexão direta com a API / PostgreSQL do Campaign Hub via MCP.
- Listagem inteligente de vídeos catalogados e transmissões recentes.
- Filtros táticos de 1 clique no frontend:
  * **[Todos]**: Ordem cronológica completa do acervo.
  * **[🔥 Mais Virais]**: Cortes com `density_rank > 80`.
  * **[🥊 Confronto & STF]**: Pautas jurídicas e enfrentamento.
  * **[💰 Economia & Rombo]**: Temas fiscais e orçamentários.
  * **[⏳ Ideal < 90s]**: Blocos concisos na faixa de retenção de Shorts/Reels/TikTok.

### 2. Player de Streaming Instantâneo (Zero-Download) (`web/static/js/app.js`)
- Carregamento de vídeos diretamente via YouTube IFrame API no segundo exato de cada bloco.
- O operador assiste e ajusta cortes sem precisar esperar o download prévio de vídeos longos de 2 a 3 horas.
- Seek sincronizado milissegundo a milissegundo com a régua da timeline e tabela de frases.

### 3. Edição de Legendas e Transcrição em Tempo Real (`indomavel/legenda.py`, `indomavel/render.py`)
- Transcrição dividida palavra por palavra com Voice Activity Detection (VAD).
- O operador pode editar frases ao vivo no formulário, refletindo imediatamente no preview e gravando no arquivo MP4 via FFmpeg.
- Renderizador de legendas animadas formato `.ass` com fonte Bebas Neue, destaque dinâmico de palavras e posicionamento inteligente.

### 4. Geometria de Enquadramento Blindada & Anti-GC (`indomavel/render.py`)
- Formatos suportados: 9:16 vertical, 3:4, 4:5, 1:1 quadrado e 16:9 limpo.
- **Anti-GC (Cortar Topo)**: Controle deslizante (0 a 300px) e arrasto direto no player com o mouse (drag-to-pan) para ocultar tarjas superiores, banners de lives ou GC da transmissão original.

### 5. Motor de Gravação Contínua de Lives (`indomavel/gravador_live.py`)
- Fatiamento contínuo de transmissões ao vivo em pedaços de 15 a 30 minutos via `yt-dlp` e FFmpeg.
- Transcrição e extração de capítulos e resumos via IA.
- Política de retenção local de 48 horas para limpeza automática de transmissões passadas.

### 6. Sistema Inteligente de Headlines com Gemini 3.8 Flash (`indomavel/headlines.py`)
- Integração com a família Gemini (3.8-flash, 3.7-flash, 2.5-flash) e fallback para heurísticas determinísticas de alta conversão.
- 3 Ângulos Oficiais: **Noticioso**, **Confronto** e **Citação Direta**.
- **Locutor Inteligente**: Detecção rigorosa de quem está falando. NÃO assume cegamente que é o Renan Santos — só atribui ao Renan quando confirmado pela transcrição ou metadados; caso contrário, foca no debate ou no orador convidado.

### 7. Pacotes de Cortes e Google Drive (`indomavel/cortador_automatico.py`, `indomavel/google_drive.py`)
- **Estrutura Hierárquica Oficial no Google Drive e Local**:
  `[Pasta Raiz: 1wBxCAat68t-jLBl3RJxCBJmZNvPAjz-G ou output/google_drive/]`
    └─ `[Nome do Vídeo Fonte]` (ex: `FUTURO GLORIOSO TOUR - SANTA CATARINA - Parte 7`)
         ├─ `Cortes com headline/`
         │     ├─ `FernandoXX_sem_legenda.mp4` (ou `_headline.mp4`)
         │     └─ `FernandoXX_headlines.txt` (arquivo com sugestões extras de headline e arquétipos)
         └─ `Cortes originais/`
               ├─ `FernandoXX_cru.mp4` (corte sem perda na resolução original)
               └─ `FernandoXX_cru.srt` (arquivo de legenda sincronizado)
  *(Se habilitadas as modalidades adicionais: `Cortes com headline e legenda/` e `Cortes com legenda/`).*
- **Proporção Padrão 1:1 (Quadrado)** com crop superior anti-banner configurado como padrão (`cortar_topo: 140px`).
- **Autenticação e Cota Google Drive**:
  * **Causa de Zero Cortes em Drives Pessoais com Service Account**: Contas de serviço GCP possuem 0 bytes de cota de armazenamento em contas pessoais `@gmail.com`. Elas conseguem criar pastas vazias (0 bytes), mas o upload de arquivos de vídeo falha com erro 403 `storageQuotaExceeded`.
  * **Solução Oficial OAuth 2.0**: O script `Conectar_Google_Drive.bat` (ou o botão na interface web) autentica a conta pessoal do operador (Fernando) via OAuth 2.0 e salva `dados/token.json`, utilizando a cota real da sua conta Google e subindo todos os cortes pendentes automaticamente.
  * **Espelhamento Local Garantido**: Enquanto o OAuth não for ativado, 100% dos cortes e pastas continuam sendo gerados na pasta local `output/google_drive/[Nome do Vídeo]/[Modalidade]/`.

### 8. Fila de Execução Sequencial Estrita
- Garante a conclusão de 100% dos cortes de um vídeo antes de iniciar o próximo vídeo da fila, evitando sobrecarga de CPU/GPU e fragmentação de arquivos.

### 9. Automação Contínua Não-Parante (`VigilanteCortesContinuo`)
- **Supervisão Contínua**: O `VigilanteCortesContinuo` monitora continuamente a playlist de transmissões (`dados/playlist_aovivo.json`).
- **Resiliência Offline**: Se a API do Gemini falhar (ex: erro 403 de ativação ou cota), o sistema ativa automaticamente `blocos_heuristicos(...)` com segmentação determinística (30s a 75s), garantindo que nenhum vídeo das 21h em diante fique travado ou sem cortes.
- **Não para até ser desativado**: Uma vez ativado (`modo_automatico: true` em `dados/automacao/config_cortes.json`), processa sequencialmente todos os vídeos produzidos sem interrupção.
- Botão de alternância no frontend: `[⚡ Cortes: DESLIGADO / LIGADO]`.

### 10. Validação Rigorosa e Política de Exclusão Automática de Cache
- **Verificação de Integridade**: Antes de qualquer exclusão, o sistema valida se todos os arquivos obrigatórios do pacote `FernandoXX` foram gerados com tamanho > 0 bytes.
- **Limpeza Automática de Disco**: Assim que a integridade for confirmada, o vídeo bruto grande local (`PASTA_VIDEOS/<id>.mp4`) e arquivos intermediários em downloads são liberados automaticamente (`RETENCAO_LIMPA = True`).
- **Limpeza de Inicialização**: Purga arquivos `.tmp`, `.part`, `.partial` e pastas vazias geradas por interrupções ou desligamento da máquina.

---

## 🧪 Testes Automatizados

O projeto conta com uma suíte abrangente de **125 testes unitários e de integração** cobrindo todas as camadas:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Resultado:**
- 125 testes executados
- 123 aprovados com 100% de sucesso
- 2 testes de live externa pulados intencionalmente (requerem stream ativo no YouTube)
- 0 falhas, 0 erros.

---

## 📡 Endpoints da API

- `GET /api/vivo`: Status da aplicação e versão.
- `GET /api/videos`: Listagem de vídeos do Acervo Chub com filtros táticos.
- `GET /api/cortes/<id>` ou `GET /api/cortes_automaticos/<id>`: Status dos cortes e pacotes FernandoXX de um vídeo.
- `GET /api/automacao/cortes/status`: Status do modo automático e executor sequencial.
- `POST /api/automacao/cortes/toggle`: Liga ou desliga o modo de cortes automáticos.
- `POST /api/cortes/<id>/disparar`: Dispara geração de cortes FernandoXX para um vídeo.
- `POST /api/cortes/<id>/refazer`: Apaga cortes anteriores e refaz do zero.
- `POST /api/cortes/<id>/limpar-cache`: Libera manualmente arquivos de cache bruto de um vídeo.
- `POST /api/cortes/limpar-cache-geral`: Varre todos os vídeos já concluídos e libera cache residual.

---

## 🌐 Publicação no GitHub

Para vincular e subir o repositório no seu perfil do GitHub (https://github.com/SAGIEV007):

```powershell
cd C:\indomavel
git remote add origin https://github.com/SAGIEV007/indomavel.git
git branch -M main
git push -u origin main
```
