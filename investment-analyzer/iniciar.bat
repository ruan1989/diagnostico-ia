@echo off
REM Sobe o painel InvestAI no Windows, com dados REAIS de mercado.
REM
REM   iniciar.bat              dados reais da Bitget
REM   iniciar.bat --simulado   gerador sintetico, sem rede
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set SIMULADO=0
if /i "%~1"=="--simulado" set SIMULADO=1

echo.
echo   InvestAI - preparando o painel
echo   ----------------------------------------

REM ------------------------------------------------------------ 1) Python
set PY=
for %%C in (py python) do (
  if not defined PY (
    %%C -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if !errorlevel! equ 0 set PY=%%C
  )
)
if not defined PY (
  echo   ERRO: preciso de Python 3.11 ou mais novo.
  echo   Baixe em https://www.python.org/downloads/ e marque "Add Python to PATH".
  pause
  exit /b 1
)
for /f "delims=" %%V in ('%PY% --version') do echo   Python: %%V

REM ------------------------------------ 2) ambiente virtual e dependencias
if not exist .venv (
  echo   Criando ambiente virtual ^(so na primeira vez^)...
  %PY% -m venv .venv
)
set VENV_PY=.venv\Scripts\python.exe
if not exist "%VENV_PY%" (
  echo   ERRO: nao consegui criar o ambiente virtual.
  pause
  exit /b 1
)

echo   Instalando dependencias...
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -r requirements.txt

REM ------------------------------------------ 3) token de acesso do painel
REM Fica so no seu computador. Sem ele, qualquer processo local poderia
REM acionar o motor.
if not exist .env (
  for /f "delims=" %%T in ('"%VENV_PY%" -c "import secrets; print(secrets.token_urlsafe(24))"') do set TOKEN=%%T
  > .env echo INVESTAI_API_TOKEN=!TOKEN!
  echo   Token de acesso criado em .env
)
for /f "usebackq tokens=1,* delims==" %%A in (".env") do set %%A=%%B

set PYTHONPATH=%CD%\src;%PYTHONPATH%

REM ------------------------------- 4) conferir a Bitget antes de subir
if "%SIMULADO%"=="0" (
  echo.
  echo   Conferindo a conexao com a Bitget...
  "%VENV_PY%" scripts\cli.py conferir-bitget --tentativas 2
  set CODIGO=!errorlevel!
  if "!CODIGO!"=="2" (
    echo.
    echo   A sua rede nao alcanca a Bitget. Subindo em MODO SIMULADO.
    echo   Os precos serao gerados, nao cotacoes reais.
    set SIMULADO=1
  )
  if "!CODIGO!"=="1" (
    echo.
    echo   A Bitget respondeu algo diferente do esperado ^(acima^).
    echo   Pare aqui e mostre a saida: usar dados que o conector le errado
    echo   e pior do que nao usar dados.
    pause
    exit /b 1
  )
)

if "%SIMULADO%"=="1" (
  set INVESTAI_SYNTHETIC=1
  set MODO=SIMULADO ^(precos gerados^)
) else (
  set INVESTAI_SYNTHETIC=
  set MODO=DADOS REAIS da Bitget
)

if not defined INVESTAI_HOST set INVESTAI_HOST=127.0.0.1
if not defined INVESTAI_PORT set INVESTAI_PORT=8000
set URL=http://%INVESTAI_HOST%:%INVESTAI_PORT%

echo.
echo   ----------------------------------------
echo   Painel:  %URL%
echo   Token:   %INVESTAI_API_TOKEN%
echo   Modo:    %MODO%
echo   ----------------------------------------
echo.
echo   Cole o token no campo "Token da API", no canto superior direito.
echo   Para parar: Ctrl+C nesta janela.
echo.

start "" "%URL%"
"%VENV_PY%" -m uvicorn investai.api:factory --factory --host %INVESTAI_HOST% --port %INVESTAI_PORT%
endlocal
