# Indomável

Cortes do Renan Santos prontos para publicar, a partir do Campaign Hub (Chub).

## Abrir

Dê dois cliques em `Iniciar_Indomavel.bat`. Na primeira vez ele prepara o ambiente; depois abre a tela em `http://127.0.0.1:5055`.

## Usar

1. **Escolha o vídeo** na coluna da esquerda (vídeos recentes do Chub) ou cole um link do YouTube. Se o vídeo não estiver no Chub, ele aparece em *Meus links* enquanto o programa busca a legenda e divide em blocos.
2. **Clique num bloco** (aba *Blocos*). O preview mostra o vídeo já no formato escolhido, com o card, a legenda e o rodapé.
3. **Ajuste o corte na trilha**: arraste as alças amarelas; clique ou arraste na trilha para mover o vídeo; roda do mouse aproxima. O ímã encaixa as bordas no começo e no fim das frases (segure Alt para soltar).
4. **Card e legenda**: etiqueta, headline (com sugestões do Gemini), tamanhos, cores, fonte, enquadramento e rodapé.
5. **Exportar**: *Exportar vídeo pronto* gera o MP4 igual ao preview. *Baixar só o trecho* gera o MP4 cru e a legenda `.srt` para o CapCut.
6. **Em massa**: marque os vídeos e escolha *Só baixar* (os melhores momentos inteiros, com `.srt`) ou *Editar automaticamente* (corte curto com card e headline, e a legenda num `.srt` separado para corrigir no CapCut). Na mesma aba dá para ligar a vigia de vídeos novos; os cortes dela aparecem para revisar e os aprovados vão para `downloads\aprovados`.

Se aparecer uma faixa vermelha no topo, ela diz o que fazer (servidor antigo, sem conexão ou versão nova).

Atalhos: **espaço** toca/pausa, **I** marca o começo, **O** marca o fim, **← →** andam 1 s (com Shift, 0,1 s).

Os arquivos ficam em `downloads\`.

## Conferir que está tudo funcionando

```
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts\verificar_chub.py
.venv\Scripts\python.exe scripts\testar_download.py
.venv\Scripts\python.exe scripts\testar_exportacao.py
```

Instruções para agentes (Claude Code, Antigravity): [`AGENTS.md`](AGENTS.md).
