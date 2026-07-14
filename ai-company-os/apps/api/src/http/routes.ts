import type { FastifyInstance } from "fastify";
import type { Currency } from "@aicos/shared";
import type { Container } from "../bootstrap.js";
import { AppError } from "../platform/errors.js";
import type { BillingCycle, PlanId } from "../billing/billing.service.js";
import { Money } from "../billing/money.js";
import type { PaymentMethod } from "../billing/provider.js";

function bearer(header?: string): string | undefined {
  if (!header) return undefined;
  return header.replace(/^Bearer\s+/i, "").trim() || undefined;
}

export function registerRoutes(app: FastifyInstance, c: Container): void {
  app.get("/health", async () => ({
    status: "ok",
    llm: c.llm.activeProvider,
    tools: c.tools.availableFor({ tenantId: "demo", userId: "u_owner", role: "owner" }).length,
  }));

  app.post("/auth/login", async (req) => {
    const { email, password } = (req.body ?? {}) as { email?: string; password?: string };
    return c.auth.login(String(email), String(password));
  });

  // A tela única fala com este endpoint.
  app.post("/chat", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    const { message } = (req.body ?? {}) as { message?: string };
    if (!message || !message.trim()) throw new AppError("Mensagem vazia.");
    return c.orchestrator.handle(ctx, message);
  });

  app.get("/agents", async () => c.agents.list());

  app.get("/billing/plans", async () => c.billing.plans());

  app.post("/billing/subscribe", async (req) => {
    const b = (req.body ?? {}) as {
      planId?: PlanId;
      cycle?: BillingCycle;
      method?: PaymentMethod;
      currency?: Currency;
    };
    return c.billing.subscribe(
      b.planId ?? "starter",
      b.cycle ?? "monthly",
      b.method ?? "card",
      b.currency ?? "BRL",
    );
  });

  app.post("/billing/checkout", async (req) => {
    const b = (req.body ?? {}) as {
      amount?: number;
      currency?: Currency;
      method?: PaymentMethod;
      description?: string;
    };
    return c.billing.checkout({
      amount: Money.of(Number(b.amount ?? 0), b.currency ?? "BRL"),
      method: b.method ?? "card",
      description: b.description ?? "Cobrança avulsa",
    });
  });

  app.get("/crm/customers", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    return c.crm.customers.list(ctx.tenantId);
  });

  app.get("/finance/summary", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    return { profit: c.finance.profitThisMonth(ctx), cashflow: c.finance.cashflow(ctx) };
  });
}
