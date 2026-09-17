---
name: conferir-antes-de-entregar
description: Antes de dizer que uma tarefa do Indomável está pronta, provar que está — mostrando o comando e a saída inteira, nunca um resumo. Use ao terminar qualquer tarefa e sempre que for escrever "funciona", "passou", "testei" ou "está pronto".
---

# Conferir antes de entregar

Adaptada da skill de mesmo nome do Furia Clips. Ela nasceu quando um agente
respondeu `19 passed` para uma bateria de 1.196 testes: tinha rodado um arquivo
só. No Antigravity o mesmo defeito apareceu com outra cara — "12 cortes
renderizados" saía de um `render.py` que só imprimia essa frase.

Resposta que cria certeza falsa é pior que tarefa não feita: a próxima decisão é
tomada em cima dela.

## Três perguntas antes de escrever "pronto"

1. **Rodei o que pediram, ou uma parte?**
2. **O número faz sentido no tamanho da coisa?** Um download de 30 s não termina
   em 0,1 s; a lista de recentes do Chub não tem 0 vídeos.
3. **Estou entregando a saída ou a minha leitura dela?**

## As provas deste projeto

| O que mudou | Prova mínima |
| --- | --- |
| Qualquer código Python | `.venv\Scripts\python.exe -m unittest discover -s tests -v` |
| Conexão com o Chub | `.venv\Scripts\python.exe scripts\verificar_chub.py` (fala com o servidor de verdade) |
| JavaScript da tela | `node --check web\static\js\app.js` |
| Download de trecho | `.venv\Scripts\python.exe scripts\testar_download.py` (baixa e confere com ffprobe) |
| Tela | abrir `http://127.0.0.1:5055`, usar o fluxo que mudou e ler o console do navegador |

Entregue o comando e a saída. Se a saída for longa, o começo e o fim — nunca o
meio editado.

## Nunca

- Resumir número. "Passou tudo" não é resultado; `Ran 11 tests ... OK` é.
- Dizer "deve funcionar". Ou rodou, ou não rodou.
- Trocar a tarefa por uma menor sem avisar.
- Aceitar lista vazia do Chub como "não há nada" sem ver se veio de uma falha.

## Quando não deu

```
Não consegui: <comando>
Erro inteiro: <cole>
Onde parei: <última coisa que funcionou>
```

Dizer que não deu é uma resposta completa. Dizer que deu sem ter dado estraga
todas as sessões seguintes.
