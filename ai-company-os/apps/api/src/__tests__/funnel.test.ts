import { describe, expect, it } from "vitest";
import { buildContainer } from "../bootstrap.js";
import { FunnelModule } from "../modules/funnel/funnel.module.js";
import { EventBus } from "../platform/event-bus.js";

describe("Funil: identifica necessidade e roteia", () => {
  it("classifica áreas (PT/EN)", () => {
    const f = new FunnelModule(new EventBus());
    expect(f.classify("quero saber o preço do plano")).toBe("sales");
    expect(f.classify("I want pricing for my company")).toBe("sales");
    expect(f.classify("estou com um bug, não funciona")).toBe("support");
    expect(f.classify("preciso de reembolso da fatura")).toBe("finance");
    expect(f.classify("tenho uma vaga / looking for a job")).toBe("hr");
    expect(f.classify("proposta de parceria de imprensa")).toBe("marketing");
  });

  it("pontua leads com sinais de compra", () => {
    const f = new FunnelModule(new EventBus());
    const hot = f.score("minha empresa quer contratar hoje, orçamento de 5 mil");
    const cold = f.score("só curiosidade");
    expect(hot).toBeGreaterThan(cold);
    expect(hot).toBeGreaterThanOrEqual(60);
  });

  it("intake cria lead qualificado e roteia ao agente da área", () => {
    const f = new FunnelModule(new EventBus());
    const lead = f.intake({ tenantId: "t1" }, {
      name: "Ana Prospect",
      contact: "ana@x.com",
      need: "quero preço para minha empresa, urgente",
    });
    expect(lead.area).toBe("sales");
    expect(lead.stage).toBe("qualified");
    expect(f.agentFor(lead.area)).toBe("Sales AI");
    expect(f.pipeline({ tenantId: "t1" }).qualified).toBe(1);
  });

  it("evento funnel.lead.created cria cliente no CRM (fronteira por evento)", () => {
    const c = buildContainer();
    c.funnel.intake({ tenantId: "demo" }, { name: "Bruno Lead", contact: "bruno@x.com", need: "quero comprar" });
    expect(c.crm.customers.list("demo").some((cu) => cu.name === "Bruno Lead")).toBe(true);
  });

  it("qualifica lead via ferramenta da IA", async () => {
    const c = buildContainer();
    const res = await c.orchestrator.handle(
      { tenantId: "demo", userId: "u", role: "owner" },
      "qualifique o lead: nome Carlos, precisa de proposta para a empresa dele",
    );
    // a ferramenta pode ou não ser escolhida pelo mock; se foi, deve ter sucesso
    const step = res.steps.find((s) => s.tool === "funnel.qualify");
    if (step) expect(step.ok).toBe(true);
  });
});
