import { describe, expect, it } from "vitest";
import { BillingService } from "../billing/billing.service.js";
import { ExchangeRate, Money } from "../billing/money.js";

describe("Billing multi-moeda", () => {
  it("converte USD para BRL e cripto", () => {
    const fx = new ExchangeRate();
    const usd = Money.of(100, "USD");
    expect(fx.convert(usd, "BRL").amount).toBeCloseTo(540, 0);
    expect(fx.convert(usd, "BTC").amount).toBeGreaterThan(0);
  });

  it("assina plano Business no cartão em BRL", async () => {
    const billing = new BillingService();
    const { plan, amount, intent } = await billing.subscribe("business", "monthly", "card", "BRL");
    expect(plan.id).toBe("business");
    expect(amount.currency).toBe("BRL");
    expect(intent.status).toBe("requires_payment");
    expect(intent.actionUrl).toContain("checkout");
  });

  it("assina plano em cripto (BTC) via provedor cripto", async () => {
    const billing = new BillingService();
    const { intent } = await billing.subscribe("enterprise", "annual", "crypto", "BTC");
    expect(intent.provider).toBe("mock-crypto");
    expect(intent.currency).toBe("BTC");
    expect(intent.actionUrl).toMatch(/^bc1/);
  });

  it("desconto anual reduz o valor equivalente", async () => {
    const billing = new BillingService();
    const monthly = await billing.subscribe("business", "monthly", "card", "USD");
    const annual = await billing.subscribe("business", "annual", "card", "USD");
    expect(annual.amount.amount).toBeLessThan(monthly.amount.amount * 12);
  });
});
