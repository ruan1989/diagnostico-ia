import type { Currency } from "@aicos/shared";
import { hmacVerify } from "../platform/crypto.js";
import { AppError } from "../platform/errors.js";
import { ExchangeRate, Money } from "./money.js";
import {
  CryptoProvider,
  FiatOrchestrator,
  type CreateIntentInput,
  type PaymentIntent,
  type PaymentProvider,
} from "./provider.js";
import { METHOD_LABELS, regionFor, type PaymentMethod } from "./regions.js";

export type PlanId = "trial" | "starter" | "business" | "enterprise" | "white_label";
export type BillingCycle = "monthly" | "annual";

export interface Plan {
  id: PlanId;
  name: string;
  /** Preço mensal na moeda-base do produto (ver BillingService.baseCurrency). */
  basePriceMonthly: number;
  features: string[];
  highlighted?: boolean;
}

/** Planos a partir de 250/mês (na moeda-base, padrão BRL). */
export const PLANS: Plan[] = [
  { id: "trial", name: "Trial", basePriceMonthly: 0, features: ["14 dias grátis", "1 usuário", "CRM + IA com cota"] },
  { id: "starter", name: "Starter", basePriceMonthly: 250, features: ["CRM + Financeiro", "3 usuários", "Agentes essenciais"] },
  { id: "business", name: "Business", basePriceMonthly: 650, features: ["Todos os módulos", "Automações", "Conselho de Agentes", "10 usuários"], highlighted: true },
  { id: "enterprise", name: "Enterprise", basePriceMonthly: 1900, features: ["SSO + Auditoria", "SLA", "Modelo de IA dedicado", "Usuários ilimitados"] },
  { id: "white_label", name: "White-Label", basePriceMonthly: 4900, features: ["Marca própria", "Marketplace", "Revenda", "Multi-empresa"] },
];

const ANNUAL_MONTHS_CHARGED = 10; // 12 meses pagando 10 (~2 grátis)

export interface Quote {
  plan: Plan;
  cycle: BillingCycle;
  method: PaymentMethod;
  methodLabel: string;
  country: string;
  /** O que o comprador paga, na moeda local dele. */
  payin: { amount: number; currency: Currency };
  /** O que o merchant recebe, na moeda escolhida. */
  payout: { amount: number; currency: Currency };
  fxRate: number;
}

/**
 * Cobrança global. Comprador paga na moeda local dele pelo método local
 * (PIX, UPI, SEPA, cartão, cripto…); o merchant recebe na moeda que preferir
 * (liquidação). Preços a partir de 250/mês na moeda-base.
 */
export class BillingService {
  private readonly providers: PaymentProvider[] = [new FiatOrchestrator(), new CryptoProvider()];
  private readonly fx = new ExchangeRate();
  /** Guarda de idempotência: mesma chave → mesma cobrança (evita duplicidade). */
  private readonly idempotency = new Map<string, PaymentIntent>();
  /** Intenções emitidas, para conciliação via webhook. */
  private readonly intents = new Map<string, PaymentIntent>();

  constructor(
    readonly baseCurrency: Currency = "BRL",
    /** Moeda em que o dono do sistema (merchant) quer receber. */
    private readonly defaultPayoutCurrency: Currency = "BRL",
    /** Segredo para verificar assinaturas de webhook dos provedores. */
    private readonly webhookSecret: string = "dev-webhook-secret",
  ) {}

  plans(): Plan[] {
    return PLANS;
  }

  /** Métodos e moeda local disponíveis para o país do comprador. */
  paymentOptions(country?: string) {
    const region = regionFor(country);
    return {
      country: region.country,
      countryName: region.name,
      localCurrency: region.currency,
      methods: region.methods.map((m) => ({ id: m, label: METHOD_LABELS[m] })),
    };
  }

  private priceInBase(plan: Plan, cycle: BillingCycle): Money {
    const months = cycle === "annual" ? ANNUAL_MONTHS_CHARGED : 1;
    return Money.of(plan.basePriceMonthly * months, this.baseCurrency);
  }

  /** Moeda que o comprador paga: a local do país, ou uma cripto se method=crypto. */
  private payinCurrency(country: string | undefined, method: PaymentMethod, override?: Currency): Currency {
    if (override) return override;
    if (method === "crypto") return "USDT"; // stablecoin padrão para cripto
    return regionFor(country).currency;
  }

  /** Preview de preço, sem criar cobrança. */
  quote(
    planId: PlanId,
    cycle: BillingCycle,
    country: string | undefined,
    method: PaymentMethod,
    opts: { payoutCurrency?: Currency; payinCurrency?: Currency } = {},
  ): Quote {
    const plan = PLANS.find((p) => p.id === planId);
    if (!plan) throw new Error(`Plano desconhecido: ${planId}`);
    const payoutCurrency = opts.payoutCurrency ?? this.defaultPayoutCurrency;
    const payinCur = this.payinCurrency(country, method, opts.payinCurrency);
    const base = this.priceInBase(plan, cycle);
    const payin = this.fx.convert(base, payinCur);
    const payout = this.fx.convert(base, payoutCurrency);
    return {
      plan,
      cycle,
      method,
      methodLabel: METHOD_LABELS[method],
      country: regionFor(country).country,
      payin: { amount: payin.amount, currency: payin.currency },
      payout: { amount: payout.amount, currency: payout.currency },
      fxRate: this.fx.rate(payinCur, payoutCurrency),
    };
  }

  private providerFor(method: PaymentMethod): PaymentProvider {
    const provider = this.providers.find((p) => p.supports(method));
    if (!provider) throw new Error(`Nenhum provedor suporta o método ${method}.`);
    return provider;
  }

  /** Cria a cobrança de assinatura (gera a intenção de pagamento com liquidação). */
  async subscribe(
    planId: PlanId,
    cycle: BillingCycle,
    country: string | undefined,
    method: PaymentMethod,
    opts: { payoutCurrency?: Currency; payinCurrency?: Currency; idempotencyKey?: string } = {},
  ): Promise<{ quote: Quote; intent: PaymentIntent }> {
    const quote = this.quote(planId, cycle, country, method, opts);
    if (quote.payin.amount <= 0) throw new AppError("Valor de cobrança inválido.");
    const input: CreateIntentInput = {
      payin: Money.of(quote.payin.amount, quote.payin.currency),
      payoutCurrency: quote.payout.currency,
      method,
      description: `${quote.plan.name} (${cycle})`,
    };
    const intent = await this.createIntentIdempotent(input, opts.idempotencyKey);
    return { quote, intent };
  }

  async checkout(input: CreateIntentInput, idempotencyKey?: string): Promise<PaymentIntent> {
    if (input.payin.amount <= 0) throw new AppError("Valor de cobrança inválido.");
    return this.createIntentIdempotent(input, idempotencyKey);
  }

  private async createIntentIdempotent(input: CreateIntentInput, key?: string): Promise<PaymentIntent> {
    if (key && this.idempotency.has(key)) return this.idempotency.get(key)!;
    const intent = await this.providerFor(input.method).createIntent(input);
    this.intents.set(intent.id, intent);
    if (key) this.idempotency.set(key, intent);
    return intent;
  }

  /**
   * Recebe uma confirmação do provedor de pagamento. Verifica a ASSINATURA
   * (HMAC) antes de confiar — impede que um atacante marque cobranças como
   * pagas. Nunca armazenamos dados de cartão (PCI: os dados ficam no provedor).
   */
  handleWebhook(rawBody: string, signature: string): PaymentIntent {
    if (!hmacVerify(rawBody, signature, this.webhookSecret)) {
      throw new AppError("Assinatura de webhook inválida.", 401, "invalid_signature");
    }
    let event: { intentId?: string; status?: PaymentIntent["status"] };
    try {
      event = JSON.parse(rawBody);
    } catch {
      throw new AppError("Payload de webhook inválido.");
    }
    const intent = event.intentId ? this.intents.get(event.intentId) : undefined;
    if (!intent) throw new AppError("Cobrança não encontrada.", 404, "intent_not_found");
    if (event.status) intent.status = event.status;
    return intent;
  }

  getIntent(id: string): PaymentIntent | undefined {
    return this.intents.get(id);
  }
}
