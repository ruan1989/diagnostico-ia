# Arquitetura — AI Company OS

## Princípio norteador

> **Monólito modular, microserviços-ready.**

Produtos que tentam nascer como 20 microserviços morrem na infraestrutura antes de
achar o produto. Nós começamos como um **monólito modular** com fronteiras internas
rígidas (cada módulo expõe apenas contratos), de modo que **qualquer módulo pode ser
extraído para um microserviço** sem reescrever o resto. As fronteiras já existem; só
a topologia de deploy muda.

```
                          ┌─────────────────────────────┐
                          │         TELA ÚNICA           │
                          │   (Web / PWA / Mobile)       │
                          └───────────────┬──────────────┘
                                          │  linguagem natural
                                          ▼
                          ┌─────────────────────────────┐
                          │           API Gateway         │
                          │   Auth · RBAC · Multi-tenant  │
                          └───────────────┬──────────────┘
                                          ▼
        ┌─────────────────────────── AI CORE ───────────────────────────┐
        │  Interpretar → Planejar → Delegar → Executar → Aprender        │
        │                                                                │
        │   ┌──────────┐   ┌───────────────┐   ┌────────────────────┐    │
        │   │ LLM Router│  │ Motor de       │  │ Memória em camadas │    │
        │   │ (multi-   │  │ Agentes +      │  │ (curto/longo/vetor)│    │
        │   │  provedor)│  │ Conselho       │  └────────────────────┘    │
        │   └──────────┘   └───────┬────────┘                            │
        └──────────────────────────┼─────────────────────────────────────┘
                                   │ Tool Registry (contratos dos módulos)
        ┌──────────┬──────────┬────┴─────┬──────────┬──────────┬─────────┐
        ▼          ▼          ▼          ▼          ▼          ▼         ▼
      CRM      Financeiro  Marketing    RH        Projetos   Estoque   BI ...
        │          │          │          │          │          │
        └──────────┴──────────┴──────────┴──────────┴──────────┘
                                   │
                       Persistência (Repos) · Eventos · Billing
```

## Camadas

### 1. Interface — Tela única
Uma superfície conversacional. Tudo é feito por texto/voz. Componentes ricos
(tabelas, gráficos, cards) são renderizados como respostas estruturadas da IA, não
como telas navegáveis por menu.

### 2. Gateway / Auth / Multi-tenant
Cada request resolve um `RequestContext = { tenantId, userId, role }`.
RBAC controla quais ferramentas cada papel pode executar. Isolamento por `tenantId`
em toda leitura/escrita.

### 3. AI Core
O cérebro. Pipeline:

1. **Interpretar** — entende a intenção do usuário.
2. **Planejar** — quebra em passos e escolhe qual agente/ferramenta usa.
3. **Delegar** — roteia para o agente especializado.
4. **Executar** — o agente chama ferramentas reais (tool calling) dos módulos.
5. **Aprender** — grava a decisão na memória para contexto futuro.

Componentes:
- **LLM Router** (`core/llm`): abstrai provedores (mock, Anthropic, OpenAI, Gemini…).
  Troca por custo/desempenho. O _mock_ é determinístico e roda sem chave — todo o
  sistema é demonstrável offline.
- **Motor de Agentes** (`core/agents`): cada agente tem objetivo, personalidade,
  ferramentas permitidas e memória. O **Conselho** faz vários agentes debaterem,
  proporem e votarem soluções para problemas complexos.
- **Tool Registry** (`core/tools`): o único ponto onde os módulos expõem capacidades
  para a IA. Cada ferramenta declara nome, schema de entrada, RBAC e handler.
- **Memória** (`core/memory`): camadas (curto prazo/sessão, longo prazo/empresa,
  usuário, semântica/vetorial). Hoje in-memory; interface pronta para Qdrant/pgvector.

### 4. Módulos de negócio
Cada módulo (`modules/*`) é autocontido: modelo de domínio + repositório +
serviço + **ferramentas** que registra no Tool Registry. Um módulo **nunca** importa
o repositório de outro — só fala via ferramentas/eventos. É isso que permite extraí-lo.

Implementados no núcleo: `auth`, `crm`, `finance`.
Scaffold/roadmap: `marketing`, `hr`, `projects`, `inventory`, `bi`, `documents`.

### 5. Billing (multi-moeda + cripto)
`billing/` define uma abstração `PaymentProvider` com implementações plugáveis:
Stripe, Mercado Pago, PIX, PayPal e **Crypto** (BTC/ETH/USDT). `Money` é uma
_value object_ com moeda (BRL, USD, EUR, BTC…) e conversão via `ExchangeRate`.
Assinaturas mensais/anuais + planos (Free/Starter/Business/Enterprise/White-Label).

### 6. Eventos & Aprendizado
Um `EventBus` interno publica eventos de domínio (`crm.lead.created`,
`finance.entry.recorded`…). Isso alimenta automações e o aprendizado proativo
(detectar padrões, prever cancelamentos/estoque). Hoje in-process; pronto para
Kafka/RabbitMQ.

## Do monólito modular aos microserviços

Como cada módulo já fala só por contrato + eventos, extrair um microserviço é:

1. Empacotar `modules/finance` num serviço próprio.
2. Trocar as chamadas in-process por RPC/HTTP no lugar do import direto (o AI Core já
   chama tudo via Tool Registry — nada nele muda).
3. Trocar `EventBus` in-process por Kafka.

Nenhuma regra de negócio é reescrita.

## Stack de produção (alvo)

| Camada | Tecnologia |
|--------|-----------|
| Runtime | Node.js + TypeScript |
| API | Fastify |
| Banco relacional | PostgreSQL (+ pgvector) |
| Vetorial | Qdrant / pgvector |
| Cache / filas | Redis |
| Eventos | Kafka / RabbitMQ |
| Objetos | MinIO / S3 |
| Observabilidade | Prometheus + Grafana + OpenTelemetry |
| Orquestração | Docker + Kubernetes |
| IaC | Terraform |
| Edge | Nginx + Cloudflare |

O núcleo neste repo usa **repositórios in-memory** por padrão para rodar sem
dependências. A troca para Postgres é uma implementação da interface `Repository`.
