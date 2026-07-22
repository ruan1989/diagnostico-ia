#!/usr/bin/env bash
# Company OS — setup e execução local (Linux/macOS)
# Uso:  bash scripts/setup.sh
set -euo pipefail

echo "== Company OS :: setup local =="
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js não encontrado. Instale o Node 20+ (https://nodejs.org) e rode de novo."
  exit 1
fi
echo "Node.js $(node -v) OK"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "Projeto: $ROOT"

echo "Instalando dependências..."
npm install

if [ ! -f .env ]; then cp .env.example .env; echo "Criado .env (IA mock por padrão)."; fi

echo "Rodando testes..."
npm test

echo "Subindo API (:4000) e Web (:5173)... Ctrl+C para parar."
npm run dev
