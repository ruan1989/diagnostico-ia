import { describe, expect, it } from "vitest";
import type { RequestContext } from "@aicos/shared";
import { buildContainer } from "../bootstrap.js";

const ctx: RequestContext = { tenantId: "t1", userId: "u1", role: "owner" };

describe("Inteligência Proativa", () => {
  it("empresa vazia → 'tudo sob controle'", () => {
    const c = buildContainer();
    const list = c.proactive.review(ctx);
    expect(list.some((i) => i.id === "all_good")).toBe(true);
  });

  it("detecta caixa negativo (crítico)", () => {
    const c = buildContainer();
    c.finance.record(ctx, "expense", 5000, "aluguel");
    const list = c.proactive.review(ctx);
    const crit = list.find((i) => i.id === "cash_negative");
    expect(crit?.severity).toBe("critical");
    expect(list[0].severity).toBe("critical"); // ordenado por severidade
  });

  it("detecta lead quente sem proposta e sugere ação", () => {
    const c = buildContainer();
    c.funnel.intake(ctx, { name: "Ana Quente", contact: "", need: "quero preço urgente para minha empresa" });
    const list = c.proactive.review(ctx);
    const hot = list.find((i) => i.id === "hot_lead_no_proposal");
    expect(hot).toBeDefined();
    expect(hot?.suggestion).toContain("Ana Quente");
  });

  it("projeta o fechamento do mês quando há receita", () => {
    const c = buildContainer();
    c.finance.record(ctx, "income", 3000, "venda");
    const list = c.proactive.review(ctx);
    expect(list.some((i) => i.id === "forecast")).toBe(true);
  });

  it("responde à intenção 'o que devo fazer?' pela IA", async () => {
    const c = buildContainer();
    c.finance.record(ctx, "expense", 9000, "custo alto");
    const res = await c.orchestrator.handle(ctx, "o que devo fazer agora?");
    const step = res.steps.find((s) => s.tool === "insights.review");
    expect(step?.ok).toBe(true);
    expect(res.data.some((d) => d.kind === "insights")).toBe(true);
  });
});
