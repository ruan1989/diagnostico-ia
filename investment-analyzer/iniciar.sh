#!/usr/bin/env bash
# Sobe o painel InvestAI no seu computador, com dados REAIS de mercado.
#
# Faz tudo sozinho: ambiente virtual, dependências, token de acesso, checagem
# da conexão com a Bitget e abertura do navegador.
#
#   ./iniciar.sh              dados reais de mercado da Bitget
#   ./iniciar.sh --simulado   gerador sintético, sem rede
set -euo pipefail
cd "$(dirname "$0")"

SIMULADO=0
[ "${1:-}" = "--simulado" ] && SIMULADO=1

echo
echo "  InvestAI — preparando o painel"
echo "  ----------------------------------------"

# ------------------------------------------------- 1) Python
PY=""
for cand in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PY="$cand"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "  ERRO: preciso de Python 3.11 ou mais novo."
  echo "  Baixe em https://www.python.org/downloads/ e rode este arquivo de novo."
  exit 1
fi
echo "  Python: $($PY --version)"

# ------------------------------------- 2) ambiente virtual e dependências
if [ ! -d .venv ]; then
  echo "  Criando ambiente virtual (só na primeira vez)..."
  "$PY" -m venv .venv
fi
VENV_PY=".venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY=".venv/Scripts/python.exe"

echo "  Instalando dependências..."
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r requirements.txt

# --------------------------------------------- 3) token de acesso do painel
# Fica só no seu computador, num arquivo legível apenas por você. Sem ele,
# qualquer processo local poderia acionar o motor.
if [ ! -f .env ]; then
  TOKEN="$("$VENV_PY" -c 'import secrets; print(secrets.token_urlsafe(24))')"
  printf 'INVESTAI_API_TOKEN=%s\n' "$TOKEN" > .env
  chmod 600 .env 2>/dev/null || true
  echo "  Token de acesso criado em .env"
fi
set -a; . ./.env; set +a

export PYTHONPATH="${PWD}/src:${PYTHONPATH:-}"

# ------------------------------------- 4) conferir a Bitget antes de subir
if [ "$SIMULADO" = "0" ]; then
  echo
  echo "  Conferindo a conexão com a Bitget..."
  set +e
  "$VENV_PY" scripts/cli.py conferir-bitget --tentativas 2
  CODIGO=$?
  set -e
  if [ "$CODIGO" = "2" ]; then
    echo
    echo "  A sua rede não alcança a Bitget. Subindo em MODO SIMULADO."
    echo "  Os preços serão gerados, não cotações reais."
    SIMULADO=1
  elif [ "$CODIGO" = "1" ]; then
    echo
    echo "  A Bitget respondeu algo diferente do esperado (acima)."
    echo "  Pare aqui e me mostre a saída: usar dados que o conector lê errado"
    echo "  é pior do que não usar dados."
    exit 1
  fi
fi

if [ "$SIMULADO" = "1" ]; then
  export INVESTAI_SYNTHETIC=1
else
  unset INVESTAI_SYNTHETIC 2>/dev/null || true
fi

HOST="${INVESTAI_HOST:-127.0.0.1}"
PORT="${INVESTAI_PORT:-8000}"
URL="http://${HOST}:${PORT}"

if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  echo
  echo "  AVISO: expondo em ${HOST}. Este painel controla ordens reais."
  echo "  Só faça isso atrás de HTTPS e com um token forte."
fi

echo
echo "  ----------------------------------------"
echo "  Painel:  $URL"
echo "  Token:   $INVESTAI_API_TOKEN"
echo "  Modo:    $([ "$SIMULADO" = "1" ] && echo 'SIMULADO (preços gerados)' || echo 'DADOS REAIS da Bitget')"
echo "  ----------------------------------------"
echo
echo "  Cole o token no campo 'Token da API', no canto superior direito."
echo "  Para parar: Ctrl+C."
echo

# ---------------------------------------------------- 5) abrir o navegador
( sleep 3
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1
  elif command -v open >/dev/null 2>&1; then open "$URL" >/dev/null 2>&1
  fi ) &

exec "$VENV_PY" -m uvicorn investai.api:factory --factory --host "$HOST" --port "$PORT"
