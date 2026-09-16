@echo off
rem Inicia o Indomavel. Este arquivo precisa ficar sem acentos e com quebras de linha do Windows (CRLF),
rem senao o cmd se perde e executa pedacos das linhas (erros como "'/d' nao e reconhecido").
rem Se ja houver um servidor aberto de outra versao, ele e reiniciado: a tela nova nunca fala com um servidor antigo.
setlocal
title Indomavel
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
set "PORTA=5055"
if exist ".env" for /f "tokens=1,* delims==" %%a in ('findstr /b /c:"PORTA=" ".env"') do set "PORTA=%%b"
set "URL=http://127.0.0.1:%PORTA%"
set "RESPOSTA=%TEMP%\indomavel_vivo_%PORTA%.json"
set "ARQUIVO_VERSAO=%TEMP%\indomavel_versao_%PORTA%.txt"

if exist "%PY%" goto servidor

echo Preparando o ambiente pela primeira vez. Isso leva alguns minutos...
set "BASE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if exist "%BASE%" goto criar_com_base
where py >nul 2>nul
if errorlevel 1 goto sem_python
py -3 -m venv .venv
if errorlevel 1 goto erro
goto instalar

:criar_com_base
"%BASE%" -m venv .venv
if errorlevel 1 goto erro

:instalar
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto erro
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto erro

:servidor
"%PY%" -m indomavel.versao "%ARQUIVO_VERSAO%"
if errorlevel 1 goto erro_codigo
set /p VERSAO=<"%ARQUIVO_VERSAO%"
curl.exe -s -f -o "%RESPOSTA%" "%URL%/api/vivo"
if errorlevel 1 goto iniciar
findstr /c:"%VERSAO%" "%RESPOSTA%" >nul
if not errorlevel 1 goto abrir
echo Tem um Indomavel de outra versao aberto na porta %PORTA%. Reiniciando com a versao atual...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort %PORTA% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.OwningProcess); if ($p.CommandLine -match 'rodar.py') { Stop-Process -Id $_.OwningProcess -Force } }"
ping -n 3 127.0.0.1 >nul
curl.exe -s -f -o nul "%URL%/api/vivo"
if not errorlevel 1 goto porta_ocupada

:iniciar
echo Iniciando o servidor em %URL% ...
start "Indomavel - servidor" /min "%PY%" rodar.py
set /a TENTATIVA=0

:esperar
ping -n 2 127.0.0.1 >nul
curl.exe -s -f -o "%RESPOSTA%" "%URL%/api/vivo"
if errorlevel 1 goto ainda_nao
findstr /c:"%VERSAO%" "%RESPOSTA%" >nul
if not errorlevel 1 goto abrir
:ainda_nao
set /a TENTATIVA+=1
if %TENTATIVA% lss 90 goto esperar
echo O servidor nao respondeu. Veja a janela "Indomavel - servidor".
pause
exit /b 1

:abrir
if defined INDOMAVEL_SEM_NAVEGADOR exit /b 0
start "" msedge --app=%URL% --start-maximized
exit /b 0

:porta_ocupada
echo A porta %PORTA% esta ocupada por outro programa. Troque PORTA no arquivo .env.
pause
exit /b 1

:erro_codigo
echo Nao consegui ler a versao do programa. Veja a mensagem acima.
pause
exit /b 1

:sem_python
echo Nao encontrei o Python. Instale o Python 3.13 e rode de novo.
pause
exit /b 1

:erro
echo Nao deu para preparar o ambiente. Veja a mensagem acima.
pause
exit /b 1
