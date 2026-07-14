# Roadmap — AI Company OS

Estratégia: **arquitetura preparada para tudo, lançamento por módulos.** Cada fase
entrega valor de uso diário e aumenta o custo de troca (retenção).

## Fase 0 — Núcleo (este repositório) ✅
- AI Core: interpretar → planejar → delegar → executar → aprender
- LLM Router multi-provedor (mock/Anthropic/OpenAI) com troca por custo
- Motor de agentes + Conselho de Agentes
- Memória em camadas + Tool Registry + EventBus
- Módulos CRM e Financeiro funcionais
- Auth multi-tenant + RBAC
- Billing multi-moeda (BRL/USD/cripto) — abstração + provedores mock
- Tela única conversacional

## Fase 1 — Operação comercial (0–3 meses)
- CRM completo: funil, follow-up automático, WhatsApp/Email
- Atendimento omnichannel (WhatsApp/Instagram/Telegram) centralizado
- Financeiro: boletos, PIX, notas, contas a pagar/receber, centro de custos
- Billing real: Stripe + Mercado Pago + Crypto (on-chain) em produção
- Aprendizado proativo: previsão de vendas e de cancelamento

## Fase 2 — Gestão interna (3–6 meses)
- Projetos (Kanban/Scrum/Gantt) com IA priorizando
- RH: contratação assistida, folha, ponto, avaliações
- Documentos: editor + assinatura digital + OCR + resumo automático
- BI conversacional: dashboards explicados pela IA

## Fase 3 — Marketing & Crescimento (6–9 meses)
- Landing pages, campanhas (Google/Meta/TikTok), email marketing
- Automação visual estilo n8n (drag-and-drop de eventos/condições/agentes)
- Estoque com previsão de reposição

## Fase 4 — Ecossistema (9–18 meses)
- **Marketplace**: devs publicam módulos, agentes e integrações; comissão por venda
- SDK + API pública (REST/GraphQL/WebSocket) + Webhooks + OAuth
- Efeito de rede: mais empresas → mais devs → mais soluções → mais empresas
- White-label + parceiros

## Métricas-alvo por fase
- Fase 1: uso diário (DAU/tenant), nº de ações executadas por conversa
- Fase 2: nº de módulos ativos por empresa (proxy de custo de troca)
- Fase 3: receita por automação, CAC/LTV
- Fase 4: GMV do marketplace, nº de módulos de terceiros

## 🧠 Camada de Inteligência — "pensar à frente do cliente"

Já implementado (Fase 0): **Inteligência Proativa** — o sistema analisa o estado da
empresa e antecipa riscos/ações sem ser perguntado (caixa negativo, leads quentes
sem proposta, propostas sem follow-up, projeção do mês), sugere o comando e a IA
alerta sozinha após cada ação. Ver `core/proactive` (API) e `engine` (web).

Próximas tecnologias para torná-lo verdadeiramente inteligente:

1. **LLM real + RAG** — trocar o mock por Claude/GPT com _function calling_ e uma
   base de conhecimento vetorial (pgvector/Qdrant) treinada com os documentos da
   empresa (PDF, e-mail, WhatsApp), para respostas com contexto real.
2. **Previsão (ML)** — modelos de séries temporais para prever vendas, fluxo de
   caixa, churn e reposição de estoque (hoje há projeção linear simples).
3. **Automações proativas** — quando um insight surge, o sistema **executa** a ação
   aprovada (ex.: dispara follow-up no WhatsApp) via construtor visual estilo n8n.
4. **Agentes autônomos com objetivos** — cada agente monitora sua área 24/7 e age
   dentro de limites definidos (orçamento, alçada), reportando ao "conselho".
5. **Memória semântica de longo prazo** — aprende padrões da empresa e melhora as
   sugestões com o tempo (a interface `MemoryStore` já prevê a camada vetorial).
6. **Integrações** — WhatsApp, e-mail, bancos (Open Finance), notas fiscais, Google
   Ads/Meta, para o sistema ter dados reais e agir no mundo.
7. **Voz** — comando e resposta por voz (STT/TTS) na tela única.

Ver também `SECURITY.md` para o que blindar antes de dados reais.

## Planos comerciais
| Plano | Foco |
|-------|------|
| Free | 1 usuário, CRM básico, mock de IA / cota pequena |
| Starter | Pequenos negócios, CRM + Financeiro |
| Business | Multi-usuário, todos os módulos operacionais + automações |
| Enterprise | SSO, auditoria, SLA, modelos dedicados |
| White-Label | Revenda com marca própria + marketplace |
