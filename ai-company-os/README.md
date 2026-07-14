# AI Company OS

> **O Sistema Operacional da Empresa, operado por conversa.**
> Uma única tela. A IA controla CRM, Financeiro, Marketing, RH, Projetos e mais —
> orquestrando agentes especializados que colaboram entre si.

Este repositório contém o **núcleo funcional e a arquitetura completa** do AI Company OS.
Ele **roda de verdade, sem nenhuma chave de API** (usando um provedor de LLM _mock_
determinístico), e está preparado para escalar módulo a módulo até se tornar a
infraestrutura operacional de uma empresa.

---

## Por que "OS" e não "mais um SaaS"

A tese é simples: o empresário não deveria abrir 12 sistemas. Deveria **conversar**.

```
Você: "Quanto lucrei este mês?"
IA:   → aciona o agente Finance → consulta o módulo Financeiro → responde com números reais.

Você: "Envie um orçamento de R$ 4.200 para o João e agende follow-up em 3 dias."
IA:   → aciona Sales → cria proposta no CRM → agenda tarefa → confirma.
```

O AI Core **interpreta**, **planeja**, **delega** para o agente certo e **executa**
chamando ferramentas reais dos módulos. Cada empresa (tenant) tem seu próprio
"cérebro digital" com memória própria.

---

## O que já funciona nesta versão (núcleo)

| Área | Estado |
|------|--------|
| AI Core (interpretar → planejar → executar) | ✅ funcional |
| Roteador de LLM multi-provedor (mock / Anthropic / OpenAI) | ✅ funcional (mock por padrão) |
| Motor de agentes + Conselho de Agentes | ✅ funcional |
| Registro de ferramentas (tool calling) | ✅ funcional |
| Memória em camadas (curto/longo prazo, por empresa/usuário) | ✅ funcional (in-memory) |
| Módulo CRM (clientes, leads, propostas) | ✅ funcional |
| Módulo Financeiro (lançamentos, fluxo de caixa, lucro) | ✅ funcional |
| Auth multi-tenant + RBAC | ✅ funcional |
| **Pagamentos globais** — comprador paga na moeda local, você recebe na sua | ✅ funcional (mock) |
| Multi-moeda (BRL/USD/EUR/GBP/JPY/INR/MXN/ARS/NGN/ZAR + BTC/ETH/USDT/USDC) | ✅ funcional |
| Métodos: PIX, Boleto, Cartão, SEPA, ACH, FedNow, UPI, SPEI, OXXO, iDEAL, PayPal, Cripto | ✅ roteamento por país |
| Planos a partir de **250/mês** (Trial/Starter/Business/Enterprise/White-Label) | ✅ funcional |
| **Bilíngue PT/EN** (interface + IA) | ✅ funcional |
| Tela única conversacional (web) + landing SEO bilíngue | ✅ funcional |
| Marketing, RH, Projetos, BI, Estoque, Documentos | 🧱 _scaffold_ / roadmap |

**Go-to-market** (como levar isso a quem busca, sem spam): ver **[GO_TO_MARKET.md](./GO_TO_MARKET.md)**.
Landing bilíngue pronta para SEO: **[`landing/index.html`](./landing/index.html)**.

Veja **[ARCHITECTURE.md](./ARCHITECTURE.md)** para o desenho completo (microserviços-ready)
e **[ROADMAP.md](./ROADMAP.md)** para a estratégia de expansão por módulos.

---

## Quickstart (roda sem chave de API)

Requisitos: **Node.js 20+**.

```bash
cd ai-company-os
npm install
npm run dev
```

Isso sobe:
- **API** em `http://localhost:4000`
- **Web (tela única)** em `http://localhost:5173`

Abra a web e converse. Exemplos que funcionam de ponta a ponta com o provedor _mock_:

- `cadastre o cliente Maria Souza, email maria@acme.com`
- `crie uma proposta de 4200 para Maria Souza`
- `lance uma receita de 4200 recebida da Maria`
- `quanto lucrei este mês?`
- `qual meu fluxo de caixa?`
- `chame o conselho: devo contratar um vendedor?`

### Usando um LLM de verdade

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-...
# ou
export LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...
npm run dev
```

O provedor Anthropic usa `claude-opus-4-8` por padrão (configurável via `ANTHROPIC_MODEL`).

---

## Estrutura

```
ai-company-os/
├── apps/
│   ├── api/          # Backend TypeScript (Fastify) — AI Core, agentes, módulos
│   └── web/          # Frontend React — a tela única conversacional
├── packages/
│   └── shared/       # Tipos e contratos compartilhados
├── infra/            # docker-compose, deploy (K8s/Terraform stubs)
├── ARCHITECTURE.md
└── ROADMAP.md
```

## Scripts

| Comando | O que faz |
|---------|-----------|
| `npm run dev` | Sobe API + Web em modo desenvolvimento |
| `npm run dev:api` | Só a API |
| `npm run dev:web` | Só a web |
| `npm test` | Testes do núcleo (AI Core, agentes, módulos) |
| `npm run build` | Build de produção |
| `npm run lint` | Type-check |

---

## Segurança & Multi-tenant

Todo request carrega um contexto de **tenant** (empresa) + **usuário** + **papel (RBAC)**.
Dados de módulos, memória e billing são isolados por `tenantId`. Ver `apps/api/src/modules/auth`.

## Licença

MIT — ver [LICENSE](./LICENSE).
