import type { FastifyInstance } from "fastify";
import type { Currency, Lang } from "@aicos/shared";
import type { Container } from "../bootstrap.js";
import { AppError } from "../platform/errors.js";
import type { BillingCycle, PlanId } from "../billing/billing.service.js";
import { Money } from "../billing/money.js";
import type { PaymentMethod } from "../billing/regions.js";

function bearer(header?: string): string | undefined {
  if (!header) return undefined;
  return header.replace(/^Bearer\s+/i, "").trim() || undefined;
}

export function registerRoutes(app: FastifyInstance, c: Container): void {
  app.get("/health", async () => ({
    status: "ok",
    llm: c.llm.activeProvider,
    baseCurrency: c.env.baseCurrency,
    payoutCurrency: c.env.payoutCurrency,
    tools: c.tools.availableFor({ tenantId: "demo", userId: "u_owner", role: "owner" }).length,
  }));

  app.post("/auth/login", async (req) => {
    const { email, password } = (req.body ?? {}) as { email?: string; password?: string };
    return c.auth.login(String(email), String(password));
  });

  // A tela única fala com este endpoint. Aceita idioma (pt|en).
  app.post("/chat", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    const { message, lang } = (req.body ?? {}) as { message?: string; lang?: Lang };
    if (!message || !message.trim()) throw new AppError("Mensagem vazia.");
    return c.orchestrator.handle(ctx, message, lang === "en" ? "en" : "pt");
  });

  app.get("/agents", async () => c.agents.list());

  // ── Billing global ────────────────────────────────────────────────
  app.get("/billing/plans", async () => ({
    baseCurrency: c.env.baseCurrency,
    plans: c.billing.plans(),
  }));

  // Métodos e moeda local disponíveis para o país do comprador.
  app.get("/billing/options", async (req) => {
    const { country } = (req.query ?? {}) as { country?: string };
    return c.billing.paymentOptions(country);
  });

  // Preview: quanto o comprador paga (moeda local) e quanto o merchant recebe.
  app.post("/billing/quote", async (req) => {
    const b = (req.body ?? {}) as {
      planId?: PlanId;
      cycle?: BillingCycle;
      country?: string;
      method?: PaymentMethod;
      payoutCurrency?: Currency;
      payinCurrency?: Currency;
    };
    return c.billing.quote(b.planId ?? "starter", b.cycle ?? "monthly", b.country, b.method ?? "card", {
      payoutCurrency: b.payoutCurrency,
      payinCurrency: b.payinCurrency,
    });
  });

  // Cria a cobrança (pay-in na moeda local, payout na moeda do merchant).
  app.post("/billing/subscribe", async (req) => {
    const b = (req.body ?? {}) as {
      planId?: PlanId;
      cycle?: BillingCycle;
      country?: string;
      method?: PaymentMethod;
      payoutCurrency?: Currency;
      payinCurrency?: Currency;
    };
    return c.billing.subscribe(b.planId ?? "starter", b.cycle ?? "monthly", b.country, b.method ?? "card", {
      payoutCurrency: b.payoutCurrency,
      payinCurrency: b.payinCurrency,
    });
  });

  app.post("/billing/checkout", async (req) => {
    const b = (req.body ?? {}) as {
      amount?: number;
      currency?: Currency;
      payoutCurrency?: Currency;
      method?: PaymentMethod;
      description?: string;
    };
    return c.billing.checkout({
      payin: Money.of(Number(b.amount ?? 0), b.currency ?? "BRL"),
      payoutCurrency: b.payoutCurrency ?? (c.env.payoutCurrency as Currency),
      method: b.method ?? "card",
      description: b.description ?? "Cobrança avulsa",
    });
  });

  // ── Módulos (acesso direto, além da conversa) ─────────────────────
  app.get("/crm/customers", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    return c.crm.customers.list(ctx.tenantId);
  });

  app.get("/finance/summary", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    return { profit: c.finance.profitThisMonth(ctx), cashflow: c.finance.cashflow(ctx) };
  });
}
