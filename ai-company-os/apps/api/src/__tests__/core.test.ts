import { describe, expect, it } from "vitest";
import type { RequestContext } from "@aicos/shared";
import { buildContainer } from "../bootstrap.js";

const owner: RequestContext = { tenantId: "t1", userId: "u1", role: "owner" };
const member: RequestContext = { tenantId: "t1", userId: "u2", role: "member" };

describe("AI Core — ciclo conversacional (provedor mock)", () => {
  it("cadastra cliente por linguagem natural", async () => {
    const c = buildContainer();
    const res = await c.orchestrator.handle(owner, "cadastre o cliente Maria Souza, email maria@acme.com");
    expect(res.steps.some((s) => s.tool === "crm.create_customer" && s.ok)).toBe(true);
    expect(c.crm.customers.list("t1").some((cust) => cust.name === "Maria Souza")).toBe(true);
  });

  it("cria proposta e vincula ao cliente", async () => {
    const c = buildContainer();
    await c.orchestrator.handle(owner, "cadastre o cliente Maria Souza");
    const res = await c.orchestrator.handle(owner, "crie uma proposta de 4200 para Maria Souza");
    const step = res.steps.find((s) => s.tool === "crm.create_proposal");
    expect(step?.ok).toBe(true);
    const proposals = c.crm.proposals.list("t1");
    expect(proposals[0]?.amount).toBe(4200);
  });

  it("lança receita e calcula o lucro do mês", async () => {
    const c = buildContainer();
    await c.orchestrator.handle(owner, "lance uma receita de 5000 recebida da Maria");
    await c.orchestrator.handle(owner, "lance uma despesa de 2000 com fornecedor");
    const res = await c.orchestrator.handle(owner, "quanto lucrei este mês?");
    const profit = c.finance.profitThisMonth(owner);
    expect(profit.income).toBe(5000);
    expect(profit.expense).toBe(2000);
    expect(profit.profit).toBe(3000);
    expect(res.reply.toLowerCase()).toContain("lucro");
  });

  it("respeita RBAC: ferramenta financeira não é exposta ao member", async () => {
    const c = buildContainer();
    // finance.record_entry exige owner/admin/manager — não deve nem aparecer para member.
    const memberTools = c.tools.availableFor(member).map((t) => t.name);
    expect(memberTools).not.toContain("finance.record_entry");
    const res = await c.orchestrator.handle(member, "lance uma receita de 1000");
    expect(res.steps.find((s) => s.tool === "finance.record_entry")).toBeUndefined();
    expect(c.finance.cashflow(member).income).toBe(0);
  });

  it("isola dados por tenant", async () => {
    const c = buildContainer();
    await c.orchestrator.handle({ tenantId: "A", userId: "x", role: "owner" }, "cadastre o cliente Cliente Alfa");
    await c.orchestrator.handle({ tenantId: "B", userId: "y", role: "owner" }, "cadastre o cliente Cliente Beta");
    expect(c.crm.customers.list("A")).toHaveLength(1);
    expect(c.crm.customers.list("B")).toHaveLength(1);
    expect(c.crm.customers.list("A")[0].name).not.toBe(c.crm.customers.list("B")[0].name);
  });

  it("entende comandos em inglês (bilíngue)", async () => {
    const c = buildContainer();
    const res = await c.orchestrator.handle(owner, "register the customer John Global, email john@world.com", "en");
    expect(res.steps.some((s) => s.tool === "crm.create_customer" && s.ok)).toBe(true);
    expect(res.reply.startsWith("Done")).toBe(true);
    expect(c.crm.customers.list("t1").some((cust) => cust.name === "John Global")).toBe(true);
  });

  it("reúne o conselho de agentes e decide", async () => {
    const c = buildContainer();
    const res = await c.orchestrator.handle(owner, "chame o conselho: devo contratar um vendedor?");
    const step = res.steps.find((s) => s.tool === "council.deliberate");
    expect(step?.ok).toBe(true);
    const deliberation = res.data.find((d) => d.kind === "deliberation");
    expect(deliberation).toBeDefined();
    const payload = deliberation!.payload as { opinions: unknown[]; decision: string };
    expect(payload.opinions.length).toBeGreaterThan(0);
    expect(payload.decision).toBeTruthy();
  });
});
