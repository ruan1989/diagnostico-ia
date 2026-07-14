# Go-To-Market — AI Company OS

> Como fazer **quem procura por essa solução encontrar você** — de forma ética,
> escalável e sem spam. A estratégia é _inbound + SEO + marketplace_, o mesmo
> motor que fez Salesforce, Shopify e Atlassian crescerem.

## Contexto de mercado (pesquisado, jul/2026)

- A SAP lançou a **"Autonomous Enterprise"**: agentes de IA que executam trabalho
  operacional de ponta a ponta (finanças, compras, RH, cadeia) por uma camada
  **conversacional**, sem o usuário tocar em telas. Valida a categoria.
- **Gartner**: até o fim de 2026, **40% dos apps corporativos** terão agentes
  específicos (era <5% em 2025).
- O diferencial de 2026 é **autonomia proativa** — o agente monitora sinais e age
  antes de ser mandado. É exatamente o "Inteligência Proativa" do nosso núcleo.
- Pagamentos: orquestração multi-trilho (**PIX, UPI, SEPA, FedNow**) + liquidação
  em **stablecoin** já é padrão para produtos globais — nosso billing já faz isso.

**Leitura:** a categoria está sendo criada agora pelos grandes (SAP), o que
_educa o mercado_ para nós. A janela é para quem atende **PMEs** (a SAP mira
grande conta) com preço acessível e onboarding por conversa.

## ICP (cliente ideal)

| Segmento | Dor | Por que nós |
|----------|-----|-------------|
| PME de serviços (agências, clínicas, consultorias) | usa 6–10 apps desconexos | uma tela, tudo por conversa |
| E-commerce / infoprodutores | CRM + financeiro + marketing fragmentados | agentes que executam |
| Franquias / redes | falta de padronização entre unidades | multi-tenant + white-label |
| Operações globais / nômades digitais | receber de clientes no mundo todo | pague local, receba na sua moeda/cripto |

**Beachhead recomendado:** PMEs de serviços no Brasil (PIX + português nativo),
expandindo para LATAM e depois global (o produto já é bilíngue e multi-moeda).

## Posicionamento

> **"Pare de abrir 10 sistemas. Fale com a sua empresa."**
> O AI Company OS é o sistema operacional da sua empresa: uma tela, controlada por
> conversa, onde agentes de IA especializados executam CRM, financeiro e mais —
> e você recebe de clientes do mundo todo, na moeda que preferir.

## Funil de aquisição (inbound, sem spam)

1. **SEO / conteúdo** — ranquear para as buscas abaixo. Cada dor = um artigo +
   uma landing. (Quem já busca "sistema para gerenciar minha empresa com IA" é
   lead quente.)
2. **Landing bilíngue** (`landing/index.html`) com CTA para trial de 14 dias.
3. **Trial self-service** (plano Trial, R$0) — ativa sozinho, sem vendedor.
4. **Product-led** — o próprio uso diário aumenta o custo de troca (retenção).
5. **Marketplace** (fase 4) — devs trazem tráfego e módulos; efeito de rede.

Canais complementares: comunidades (Reddit r/smallbusiness, grupos de nicho),
YouTube/Shorts demonstrando "administrei minha empresa só conversando",
parcerias com contadores/agências (revenda white-label), e **App Stores**
(PWA + apps) para descoberta orgânica.

## Palavras-chave de SEO

**PT-BR (topo do funil):**
- "sistema de gestão empresarial com IA"
- "ERP com inteligência artificial para pequenas empresas"
- "CRM que funciona por conversa / WhatsApp"
- "automatizar empresa com agentes de IA"
- "sistema tudo em um para PME"
- "como gerenciar minha empresa com inteligência artificial"

**EN (mercado global):**
- "AI business operating system"
- "run my company with AI agents"
- "conversational ERP for small business"
- "all-in-one AI platform for SMB"
- "AI CRM and finance in one chat"
- "autonomous enterprise software for small business"

**Cauda longa de pagamento (diferencial):**
- "receive payments from clients worldwide in my currency"
- "aceitar PIX e cripto e receber em dólar/stablecoin"

## Métricas do funil

- Topo: sessões orgânicas, posição média SEO nas keywords acima
- Meio: taxa de início de trial, ativação (primeira ação executada por conversa)
- Fundo: trial→pago, MRR, churn, LTV/CAC
- Rede: nº de módulos de terceiros no marketplace (fase 4)

## Preço (âncora)

A partir de **R$250/mês** (Starter). Business R$650, Enterprise R$1.900,
White-Label R$4.900. Anual com ~2 meses grátis. Comprador paga na **moeda local**;
você recebe na moeda/cripto que escolher. Ver `apps/api/src/billing`.

## O que este repositório já entrega para o GTM

- Landing bilíngue pronta para SEO: [`landing/index.html`](./landing/index.html)
- Trial self-service (plano `trial`) no billing
- Preços e cotação global expostos por API (`/billing/plans`, `/billing/quote`)
- Produto bilíngue (PT/EN) e multi-moeda — pronto para "o mundo inteiro"

> **Nota ética:** não fazemos scraping de pessoas nem envio não solicitado
> (LGPD/GDPR/CAN-SPAM). Crescimento é _inbound_: quem busca a solução nos acha.
