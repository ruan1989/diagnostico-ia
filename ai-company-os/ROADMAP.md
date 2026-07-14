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

## Planos comerciais
| Plano | Foco |
|-------|------|
| Free | 1 usuário, CRM básico, mock de IA / cota pequena |
| Starter | Pequenos negócios, CRM + Financeiro |
| Business | Multi-usuário, todos os módulos operacionais + automações |
| Enterprise | SSO, auditoria, SLA, modelos dedicados |
| White-Label | Revenda com marca própria + marketplace |
