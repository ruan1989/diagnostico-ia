import type { Lang, RequestContext } from "@aicos/shared";
import type { CrmModule } from "../../modules/crm/crm.module.js";
import type { FinanceModule } from "../../modules/finance/finance.module.js";
import type { FunnelModule } from "../../modules/funnel/funnel.module.js";

export interface Insight {
  id: string;
  severity: "critical" | "warn" | "info" | "success";
  title: string;
  message: string;
  suggestion?: string;
}

const SEV: Record<Insight["severity"], number> = { critical: 0, warn: 1, info: 2, success: 3 };
const brl = (n: number) => `R$ ${n.toLocaleString("pt-BR")}`;

/**
 * Inteligência Proativa: analisa o estado da empresa e ANTECIPA o que precisa
 * de atenção — sem o usuário pedir. É o que faz o sistema "pensar à frente".
 * Espelha o motor client-side do app web.
 */
export class ProactiveEngine {
  constructor(
    private readonly crm: CrmModule,
    private readonly finance: FinanceModule,
    private readonly funnel: FunnelModule,
  ) {}

  review(ctx: RequestContext, lang: Lang = "pt"): Insight[] {
    const en = lang === "en";
    const out: Insight[] = [];
    const { income, expense } = this.finance.profitThisMonth(ctx);
    const customers = this.crm.customers.list(ctx.tenantId);
    const proposals = this.crm.proposals.list(ctx.tenantId);
    const leads = this.funnel.leads.list(ctx.tenantId);

    if (expense > income && expense > 0) {
      out.push({ id: "cash_negative", severity: "critical",
        title: en ? "Expenses above revenue" : "Despesas acima da receita",
        message: en ? `Expenses (${brl(expense)}) exceed revenue (${brl(income)}) this month.` : `As despesas (${brl(expense)}) superam a receita (${brl(income)}) neste mês.`,
        suggestion: en ? "show cash flow" : "qual meu fluxo de caixa?" });
    }

    const proposalNames = new Set(proposals.map((p) => p.customerName.toLowerCase()));
    const hot = leads.filter((l) => l.score >= 60 && !proposalNames.has(l.name.toLowerCase()));
    if (hot.length) {
      out.push({ id: "hot_lead_no_proposal", severity: "warn",
        title: en ? `${hot.length} hot lead(s) without a proposal` : `${hot.length} lead(s) quente(s) sem proposta`,
        message: en ? `"${hot[0].name}" is qualified but has no proposal.` : `"${hot[0].name}" está qualificado mas sem proposta.`,
        suggestion: en ? `create a proposal of 2000 for ${hot[0].name}` : `crie uma proposta de 2000 para ${hot[0].name}` });
    }

    const pending = proposals.filter((p) => p.status === "sent");
    if (pending.length) {
      out.push({ id: "proposals_pending", severity: "warn",
        title: en ? `${pending.length} proposal(s) awaiting reply` : `${pending.length} proposta(s) aguardando resposta`,
        message: en ? `${brl(pending.reduce((s, p) => s + p.amount, 0))} in the pipeline — follow up.` : `${brl(pending.reduce((s, p) => s + p.amount, 0))} no funil — faça follow-up.` });
    }

    if (customers.length > 0 && income === 0) {
      out.push({ id: "customers_no_revenue", severity: "info",
        title: en ? "Customers but no revenue" : "Clientes, mas sem receita",
        message: en ? "Record your sales to track profit." : "Registre suas vendas para acompanhar o lucro.",
        suggestion: en ? "record income of 3000" : "lance uma receita de 3000" });
    }

    if (income > 0) {
      const now = new Date();
      const day = now.getDate();
      const dim = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
      const proj = Math.round((income / day) * dim);
      out.push({ id: "forecast", severity: "info",
        title: en ? "Month-end forecast" : "Projeção do mês",
        message: en ? `On the current pace: ~${brl(proj)}.` : `No ritmo atual: ~${brl(proj)}.` });
    }

    if (out.length === 0) {
      out.push({ id: "all_good", severity: "success",
        title: en ? "All under control" : "Tudo sob controle",
        message: en ? "No risks detected." : "Nenhum risco detectado." });
    }
    return out.sort((a, b) => SEV[a.severity] - SEV[b.severity]);
  }
}
