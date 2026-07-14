import { describe, expect, it } from "vitest";
import { BillingService } from "../billing/billing.service.js";
import { ExchangeRate, Money } from "../billing/money.js";
import { hmacSign } from "../platform/crypto.js";

describe("Billing global (pague na sua moeda, receba na minha)", () => {
  it("converte entre fiat e cripto", () => {
    const fx = new ExchangeRate();
    const usd = Money.of(100, "USD");
    expect(fx.convert(usd, "BRL").amount).toBeCloseTo(540, 0);
    expect(fx.convert(usd, "INR").amount).toBeCloseTo(8400, 0);
    expect(fx.convert(usd, "BTC").amount).toBeGreaterThan(0);
  });

  it("plano Starter começa em 250 na moeda-base", () => {
    const billing = new BillingService("BRL", "BRL");
    const starter = billing.plans().find((p) => p.id === "starter");
    expect(starter?.basePriceMonthly).toBe(250);
  });

  it("cotação: comprador no Brasil paga em BRL via PIX, merchant recebe em BRL", () => {
    const billing = new BillingService("BRL", "BRL");
    const q = billing.quote("starter", "monthly", "BR", "pix");
    expect(q.payin.currency).toBe("BRL");
    expect(q.payin.amount).toBe(250);
    expect(q.payout.currency).toBe("BRL");
    expect(q.methodLabel).toBe("PIX");
  });

  it("comprador na Índia paga em INR (UPI); merchant recebe em USD", () => {
    const billing = new BillingService("BRL", "USD");
    const q = billing.quote("business", "monthly", "IN", "upi");
    expect(q.payin.currency).toBe("INR");
    expect(q.payin.amount).toBeGreaterThan(0);
    expect(q.payout.currency).toBe("USD");
  });

  it("assina pagando em cripto (USDT) e liquida em BTC", async () => {
    const billing = new BillingService("BRL", "BTC");
    const { quote, intent } = await billing.subscribe("enterprise", "annual", "US", "crypto");
    expect(intent.provider).toBe("mock-crypto");
    expect(intent.payinCurrency).toBe("USDT");
    expect(intent.payoutCurrency).toBe("BTC");
    expect(quote.payout.currency).toBe("BTC");
    expect(intent.actionUrl).toMatch(/^0x/);
  });

  it("desconto anual: 12 meses cobrando ~10", () => {
    const billing = new BillingService("BRL", "BRL");
    const monthly = billing.quote("business", "monthly", "BR", "pix");
    const annual = billing.quote("business", "annual", "BR", "pix");
    expect(annual.payin.amount).toBeCloseTo(monthly.payin.amount * 10, 0);
    expect(annual.payin.amount).toBeLessThan(monthly.payin.amount * 12);
  });

  it("opções de pagamento variam por país", () => {
    const billing = new BillingService();
    expect(billing.paymentOptions("BR").methods.map((m) => m.id)).toContain("pix");
    expect(billing.paymentOptions("IN").methods.map((m) => m.id)).toContain("upi");
    expect(billing.paymentOptions("DE").localCurrency).toBe("EUR");
    expect(billing.paymentOptions("ZZ").country).toBe("XX"); // fallback global
  });
});

describe("Segurança de pagamento", () => {
  it("idempotência: mesma chave não gera cobrança duplicada", async () => {
    const billing = new BillingService("BRL", "BRL");
    const a = await billing.subscribe("starter", "monthly", "BR", "pix", { idempotencyKey: "k1" });
    const b = await billing.subscribe("starter", "monthly", "BR", "pix", { idempotencyKey: "k1" });
    expect(a.intent.id).toBe(b.intent.id);
    // chave diferente → cobrança diferente
    const cc = await billing.subscribe("starter", "monthly", "BR", "pix", { idempotencyKey: "k2" });
    expect(cc.intent.id).not.toBe(a.intent.id);
  });

  it("webhook: aceita assinatura válida e atualiza status", async () => {
    const secret = "wh-secret";
    const billing = new BillingService("BRL", "BRL", secret);
    const { intent } = await billing.subscribe("starter", "monthly", "BR", "pix");
    const raw = JSON.stringify({ intentId: intent.id, status: "paid" });
    const updated = billing.handleWebhook(raw, hmacSign(raw, secret));
    expect(updated.status).toBe("paid");
    expect(billing.getIntent(intent.id)?.status).toBe("paid");
  });

  it("webhook: rejeita assinatura inválida (anti-fraude)", async () => {
    const billing = new BillingService("BRL", "BRL", "wh-secret");
    const { intent } = await billing.subscribe("starter", "monthly", "BR", "pix");
    const raw = JSON.stringify({ intentId: intent.id, status: "paid" });
    expect(() => billing.handleWebhook(raw, "assinatura-falsa")).toThrow();
    expect(billing.getIntent(intent.id)?.status).not.toBe("paid");
  });

  it("rejeita cobrança com valor inválido", async () => {
    const billing = new BillingService("BRL", "BRL");
    await expect(
      billing.checkout({ payin: Money.of(0, "BRL"), payoutCurrency: "BRL", method: "pix", description: "x" }),
    ).rejects.toThrow();
  });
});
