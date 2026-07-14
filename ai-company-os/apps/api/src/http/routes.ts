import type { FastifyInstance } from "fastify";
import type { Currency, Lang } from "@aicos/shared";
import type { Container } from "../bootstrap.js";
import { AppError } from "../platform/errors.js";
import type { BillingCycle, PlanId } from "../billing/billing.service.js";
import { Money } from "../billing/money.js";
import type { PaymentMethod } from "../billing/regions.js";
import { validate } from "../platform/validation.js";
import { clientIp } from "./security.js";

function bearer(header?: string): string | undefined {
  if (!header) return undefined;
  return header.replace(/^Bearer\s+/i, "").trim() || undefined;
}

const MAX_MESSAGE = 4000;

export function registerRoutes(app: FastifyInstance, c: Container): void {
  app.get("/health", async () => ({
    status: "ok",
    llm: c.llm.activeProvider,
    baseCurrency: c.env.baseCurrency,
    payoutCurrency: c.env.payoutCurrency,
    tools: c.tools.availableFor({ tenantId: "demo", userId: "u_owner", role: "owner" }).length,
  }));

  // ── Auth ──────────────────────────────────────────────────────────
  app.post("/auth/signup", async (req, reply) => {
    const ip = clientIp(req);
    if (!c.limiters.auth.allow(`signup:${ip}`).ok) throw new AppError("Muitas tentativas. Aguarde.", 429, "rate_limited");
    const b = validate<{ email: string; password: string }>(
      { email: { type: "email", required: true }, password: { type: "string", required: true, max: 200 } },
      req.body,
    );
    const result = c.auth.signup(b.email, b.password);
    return reply.status(201).send(result);
  });

  app.post("/auth/login", async (req) => {
    const ip = clientIp(req);
    if (!c.limiters.auth.allow(`login:${ip}`).ok) throw new AppError("Muitas tentativas. Aguarde.", 429, "rate_limited");
    const b = validate<{ email: string; password: string; code?: string }>(
      { email: { type: "email", required: true }, password: { type: "string", required: true, max: 200 }, code: { type: "string", max: 10 } },
      req.body,
    );
    return c.auth.login(b.email, b.password, ip, b.code);
  });

  // 2FA (TOTP): configurar e ativar
  app.post("/auth/2fa/setup", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    return c.auth.setupTwoFactor(ctx);
  });
  app.post("/auth/2fa/enable", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    const b = validate<{ code: string }>({ code: { type: "string", required: true, max: 10 } }, req.body);
    return c.auth.enableTwoFactor(ctx, b.code);
  });

  // ── Chat (tela única) ─────────────────────────────────────────────
  app.post("/chat", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    if (!c.limiters.chat.allow(`chat:${ctx.tenantId}:${ctx.userId}`).ok) {
      throw new AppError("Limite de mensagens atingido. Aguarde um instante.", 429, "rate_limited");
    }
    const { message, lang } = (req.body ?? {}) as { message?: string; lang?: Lang };
    if (!message || !message.trim()) throw new AppError("Mensagem vazia.");
    if (message.length > MAX_MESSAGE) throw new AppError(`Mensagem muito longa (máx ${MAX_MESSAGE}).`);
    return c.orchestrator.handle(ctx, message, lang === "en" ? "en" : "pt");
  });

  app.get("/agents", async () => c.agents.list());

  // Inteligência Proativa: insights antecipados sobre a empresa.
  app.get("/insights", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    const { lang } = (req.query ?? {}) as { lang?: Lang };
    return c.proactive.review(ctx, lang === "en" ? "en" : "pt");
  });

  // ── Funil de aquisição (captura de lead pública, pré-login) ───────
  app.post("/funnel/intake", async (req) => {
    const ip = clientIp(req);
    if (!c.limiters.auth.allow(`intake:${ip}`).ok) throw new AppError("Muitas tentativas. Aguarde.", 429, "rate_limited");
    const b = validate<{ name?: string; contact?: string; need: string; tenantId?: string }>(
      { name: { type: "string", max: 120 }, contact: { type: "string", max: 200 }, need: { type: "string", required: true, max: 2000 }, tenantId: { type: "string", max: 64 } },
      req.body,
    );
    const lead = c.funnel.intake(
      { tenantId: b.tenantId ?? "demo" },
      { name: String(b.name ?? "Prospecto"), contact: String(b.contact ?? ""), need: b.need, source: "web" },
    );
    return {
      lead,
      routedTo: c.funnel.agentFor(lead.area),
      message: `Obrigado! Encaminhei você para nosso ${c.funnel.agentFor(lead.area)} (${lead.area}). Retornaremos em breve.`,
    };
  });

  // ── Billing global ────────────────────────────────────────────────
  app.get("/billing/plans", async () => ({ baseCurrency: c.env.baseCurrency, plans: c.billing.plans() }));

  app.get("/billing/options", async (req) => {
    const { country } = (req.query ?? {}) as { country?: string };
    return c.billing.paymentOptions(country);
  });

  app.post("/billing/quote", async (req) => {
    const b = (req.body ?? {}) as {
      planId?: PlanId; cycle?: BillingCycle; country?: string; method?: PaymentMethod;
      payoutCurrency?: Currency; payinCurrency?: Currency;
    };
    return c.billing.quote(b.planId ?? "starter", b.cycle ?? "monthly", b.country, b.method ?? "card", {
      payoutCurrency: b.payoutCurrency, payinCurrency: b.payinCurrency,
    });
  });

  app.post("/billing/subscribe", async (req) => {
    const b = (req.body ?? {}) as {
      planId?: PlanId; cycle?: BillingCycle; country?: string; method?: PaymentMethod;
      payoutCurrency?: Currency; payinCurrency?: Currency;
    };
    const idempotencyKey = (req.headers["idempotency-key"] as string) || undefined;
    return c.billing.subscribe(b.planId ?? "starter", b.cycle ?? "monthly", b.country, b.method ?? "card", {
      payoutCurrency: b.payoutCurrency, payinCurrency: b.payinCurrency, idempotencyKey,
    });
  });

  // Webhook de confirmação do provedor. Verifica a assinatura HMAC (o campo
  // `raw` é a string exatamente assinada; em produção vem no corpo cru + header).
  app.post("/billing/webhook", async (req) => {
    const b = (req.body ?? {}) as { raw?: string; signature?: string };
    const signature = b.signature ?? (req.headers["x-signature"] as string) ?? "";
    const intent = c.billing.handleWebhook(String(b.raw ?? ""), signature);
    c.audit.record({ action: "billing.webhook", severity: "info", meta: { intentId: intent.id, status: intent.status } });
    return { ok: true, intent };
  });

  // ── Módulos (acesso direto) ───────────────────────────────────────
  app.get("/crm/customers", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    return c.crm.customers.list(ctx.tenantId);
  });

  app.get("/finance/summary", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    return { profit: c.finance.profitThisMonth(ctx), cashflow: c.finance.cashflow(ctx) };
  });

  // ── Admin (owner/admin) — auditoria de segurança ──────────────────
  app.get("/admin/audit", async (req) => {
    const ctx = c.auth.resolve(bearer(req.headers.authorization));
    c.auth.requireRole(ctx, ["owner", "admin"]);
    return {
      events: c.audit.list({ tenantId: ctx.tenantId }),
      suspicious: c.audit.suspiciousLoginBursts(),
    };
  });
}
