import type { RequestContext } from "@aicos/shared";
import { EventBus } from "../../platform/event-bus.js";
import { InMemoryRepository, type Entity } from "../../platform/repository.js";
import type { ToolRegistry } from "../../core/tools/registry.js";

export interface Entry extends Entity {
  type: "income" | "expense";
  amount: number;
  currency: string;
  description: string;
  date: string; // ISO
}

/** Módulo Financeiro: lançamentos, lucro e fluxo de caixa. */
export class FinanceModule {
  readonly entries = new InMemoryRepository<Entry>();

  constructor(private readonly events: EventBus) {}

  record(ctx: RequestContext, type: Entry["type"], amount: number, description: string): Entry {
    const entry = this.entries.create(ctx.tenantId, {
      type,
      amount,
      currency: "BRL",
      description,
      date: new Date().toISOString(),
    });
    void this.events.publish("finance.entry.recorded", ctx.tenantId, entry);
    return entry;
  }

  private sum(ctx: RequestContext, type: Entry["type"], sinceMonth = false): number {
    const now = new Date();
    return this.entries
      .list(ctx.tenantId)
      .filter((e) => e.type === type)
      .filter((e) => {
        if (!sinceMonth) return true;
        const d = new Date(e.date);
        return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear();
      })
      .reduce((s, e) => s + e.amount, 0);
  }

  profitThisMonth(ctx: RequestContext): { income: number; expense: number; profit: number } {
    const income = this.sum(ctx, "income", true);
    const expense = this.sum(ctx, "expense", true);
    return { income, expense, profit: income - expense };
  }

  cashflow(ctx: RequestContext): { balance: number; income: number; expense: number } {
    const income = this.sum(ctx, "income");
    const expense = this.sum(ctx, "expense");
    return { balance: income - expense, income, expense };
  }

  register(registry: ToolRegistry): void {
    registry.register({
      name: "finance.record_entry",
      description: "Registra um lançamento financeiro (receita ou despesa).",
      params: {
        type: { type: "string", description: "'income' (receita) ou 'expense' (despesa)", required: true },
        amount: { type: "number", description: "Valor em BRL", required: true },
        description: { type: "string", description: "Descrição do lançamento" },
      },
      allowedRoles: ["owner", "admin", "manager"],
      handler: (ctx, input) => {
        const type = input.type === "expense" ? "expense" : "income";
        const e = this.record(ctx, type, Number(input.amount), String(input.description ?? ""));
        const label = type === "income" ? "Receita" : "Despesa";
        return {
          ok: true,
          summary: `${label} de R$ ${e.amount.toLocaleString("pt-BR")} registrada${e.description ? ` (${e.description})` : ""}.`,
          data: { kind: "entry", title: `${label} registrada`, payload: e },
        };
      },
    });

    registry.register({
      name: "finance.profit",
      description: "Calcula o lucro do mês atual (receitas - despesas).",
      params: {},
      handler: (ctx) => {
        const p = this.profitThisMonth(ctx);
        return {
          ok: true,
          summary:
            `Neste mês: receita R$ ${p.income.toLocaleString("pt-BR")}, ` +
            `despesa R$ ${p.expense.toLocaleString("pt-BR")}, ` +
            `lucro R$ ${p.profit.toLocaleString("pt-BR")}.`,
          data: { kind: "kpi", title: "Lucro do mês", payload: p },
        };
      },
    });

    registry.register({
      name: "finance.cashflow",
      description: "Mostra o fluxo de caixa acumulado (saldo, receitas, despesas).",
      params: {},
      handler: (ctx) => {
        const c = this.cashflow(ctx);
        return {
          ok: true,
          summary: `Saldo em caixa: R$ ${c.balance.toLocaleString("pt-BR")} (receitas R$ ${c.income.toLocaleString("pt-BR")}, despesas R$ ${c.expense.toLocaleString("pt-BR")}).`,
          data: { kind: "kpi", title: "Fluxo de caixa", payload: c },
        };
      },
    });
  }
}
