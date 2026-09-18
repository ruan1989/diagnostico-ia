#!/usr/bin/env bash
# Sobe o painel InvestAI em 127.0.0.1:8000.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

export PYTHONPATH="${PWD}/src:${PYTHONPATH:-}"
HOST="${INVESTAI_HOST:-127.0.0.1}"
PORT="${INVESTAI_PORT:-8000}"

if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  echo "AVISO: expondo em ${HOST}. Este painel controla ordens reais."
  echo "Só faça isso atrás de HTTPS e com INVESTAI_API_TOKEN forte definido."
fi

echo "Painel em http://${HOST}:${PORT}"
exec python3 -m uvicorn investai.api:factory --factory --host "$HOST" --port "$PORT"
