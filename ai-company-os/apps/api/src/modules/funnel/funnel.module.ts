import type { RequestContext } from "@aicos/shared";
import { EventBus } from "../../platform/event-bus.js";
import { InMemoryRepository, type Entity } from "../../platform/repository.js";
import type { ToolRegistry } from "../../core/tools/registry.js";

export type FunnelArea = "sales" | "support" | "finance" | "hr" | "marketing" | "general";
export type FunnelStage = "new" | "qualified" | "proposal" | "won" | "lost";

export interface Lead extends Entity {
  name: string;
  contact: string;
  need: string;
  area: FunnelArea;
  stage: FunnelStage;
  score: number; // 0-100
  source: string;
  createdAt: string;
}

const AREA_AGENT: Record<FunnelArea, string> = {
  sales: "Sales AI",
  support: "Support AI",
  finance: "Finance AI",
  hr: "HR AI",
  marketing: "Marketing AI",
  general: "Company AI",
};

/**
 * Funil de aquisição: identifica a NECESSIDADE do cliente (PT/EN), roteia para
 * a ÁREA certa, pontua o lead e o coloca em um estágio do pipeline. Cada lead é
 * atendido pelo agente especializado da sua área.
 */
export class FunnelModule {
  readonly leads = new InMemoryRepository<Lead>();

  constructor(private readonly events: EventBus) {}

  /** Classifica a área a partir do texto do cliente (bilíngue). */
  classify(text: string): FunnelArea {
    const t = text.toLowerCase();
    if (/(preç|preco|orçament|orcament|compr|contrat|plano|vend|demo|price|pricing|quote|buy|purchase|subscribe)/.test(t)) return "sales";
    if (/(erro|bug|problema|não funciona|nao funciona|ajuda|suporte|support|help|issue|broken|not working)/.test(t)) return "support";
    if (/(fatura|cobrança|cobranca|reembolso|nota fiscal|invoice|billing|refund|charge|payment issue)/.test(t)) return "finance";
    if (/(vaga|currículo|curriculo|emprego|trabalhar|carreira|job|career|hiring|resume|apply)/.test(t)) return "hr";
    if (/(parceria|imprensa|afiliad|partnership|press|affiliate|collab|media)/.test(t)) return "marketing";
    return "general";
  }

  /** Pontua o lead (0-100) a partir de sinais de intenção de compra. */
  score(text: string): number {
    const t = text.toLowerCase();
    let s = 40;
    if (/(preç|orçament|plano|budget|price|pricing|quote|comprar|buy)/.test(t)) s += 20;
    if (/(hoje|urgent|agora|asap|imediat|now|this week|esta semana)/.test(t)) s += 15;
    if (/(empresa|equipe|funcionári|time|company|team|employees|staff)/.test(t)) s += 15;
    if (/[\d.,]+\s*(reais|r\$|usd|dólar|dolar|k|mil)/.test(t)) s += 10;
    return Math.min(100, s);
  }

  intake(
    ctx: Pick<RequestContext, "tenantId">,
    input: { name: string; contact: string; need: string; source?: string },
  ): Lead {
    const area = this.classify(input.need);
    const score = this.score(input.need);
    const stage: FunnelStage = score >= 60 ? "qualified" : "new";
    const lead = this.leads.create(ctx.tenantId, {
      name: input.name,
      contact: input.contact,
      need: input.need,
      area,
      stage,
      score,
      source: input.source ?? "chat",
      createdAt: new Date().toISOString(),
    });
    void this.events.publish("funnel.lead.created", ctx.tenantId, lead);
    return lead;
  }

  pipeline(ctx: Pick<RequestContext, "tenantId">): Record<FunnelStage, number> {
    const base: Record<FunnelStage, number> = { new: 0, qualified: 0, proposal: 0, won: 0, lost: 0 };
    for (const l of this.leads.list(ctx.tenantId)) base[l.stage] += 1;
    return base;
  }

  agentFor(area: FunnelArea): string {
    return AREA_AGENT[area];
  }

  register(registry: ToolRegistry): void {
    registry.register({
      name: "funnel.qualify",
      description: "Qualifica um lead: identifica a necessidade, roteia para a área e pontua.",
      params: {
        name: { type: "string", description: "Nome do prospecto", required: true },
        contact: { type: "string", description: "Email/telefone do prospecto" },
        need: { type: "string", description: "O que o cliente precisa (texto livre)", required: true },
      },
      handler: (ctx, input) => {
        const lead = this.intake(ctx, {
          name: String(input.name),
          contact: String(input.contact ?? ""),
          need: String(input.need),
        });
        return {
          ok: true,
          summary: `Lead "${lead.name}" roteado para ${this.agentFor(lead.area)} (área: ${lead.area}, score ${lead.score}, estágio ${lead.stage}).`,
          data: { kind: "lead", title: "Lead qualificado", payload: lead },
        };
      },
    });

    registry.register({
      name: "funnel.pipeline",
      description: "Mostra o funil de vendas por estágio.",
      params: {},
      handler: (ctx) => {
        const p = this.pipeline(ctx);
        return {
          ok: true,
          summary: `Funil: ${p.new} novos, ${p.qualified} qualificados, ${p.proposal} em proposta, ${p.won} ganhos, ${p.lost} perdidos.`,
          data: { kind: "pipeline", title: "Funil de vendas", payload: p },
        };
      },
    });
  }
}
