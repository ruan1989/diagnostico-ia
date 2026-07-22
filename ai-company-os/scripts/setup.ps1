# ─────────────────────────────────────────────────────────────────────────
# Company OS — setup e execução local no Windows (PowerShell)
# Uso:  Abra o PowerShell na pasta do projeto e rode:  ./scripts/setup.ps1
# Ele instala as dependências e sobe API (:4000) + Web (:5173).
# Requisito: Node.js 20+ (o script avisa se faltar e abre a página de download).
# ─────────────────────────────────────────────────────────────────────────
$ErrorActionPreference = "Stop"

function Test-Command($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

Write-Host "== Company OS :: setup local ==" -ForegroundColor Cyan

# 1) Node.js
if (-not (Test-Command node)) {
  Write-Host "Node.js nao encontrado." -ForegroundColor Yellow
  if (Test-Command winget) {
    Write-Host "Instalando Node.js LTS via winget..." -ForegroundColor Cyan
    winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
    Write-Host "Feche e reabra o PowerShell e rode o script de novo." -ForegroundColor Yellow
    exit 0
  } else {
    Start-Process "https://nodejs.org/en/download"
    throw "Instale o Node.js 20+ e rode o script novamente."
  }
}
$nodeVer = (node -v)
Write-Host "Node.js $nodeVer OK" -ForegroundColor Green

# 2) Pasta do projeto (raiz do ai-company-os)
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "Projeto: $root" -ForegroundColor DarkGray

# 3) Dependencias
Write-Host "Instalando dependencias (npm install)..." -ForegroundColor Cyan
npm install

# 4) .env (se nao existir)
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Criado .env a partir de .env.example (usa IA mock por padrao)." -ForegroundColor Green
}

# 5) Testes rapidos (opcional, garante que esta tudo ok)
Write-Host "Rodando testes..." -ForegroundColor Cyan
npm test

# 6) Subir API + Web
Write-Host "Subindo API (:4000) e Web (:5173)... Ctrl+C para parar." -ForegroundColor Cyan
Start-Process "http://localhost:5173"
npm run dev
