import type { Currency } from "@aicos/shared";
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

  constructor(
    readonly baseCurrency: Currency = "BRL",
    /** Moeda em que o dono do sistema (merchant) quer receber. */
    private readonly defaultPayoutCurrency: Currency = "BRL",
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
    opts: { payoutCurrency?: Currency; payinCurrency?: Currency } = {},
  ): Promise<{ quote: Quote; intent: PaymentIntent }> {
    const quote = this.quote(planId, cycle, country, method, opts);
    const input: CreateIntentInput = {
      payin: Money.of(quote.payin.amount, quote.payin.currency),
      payoutCurrency: quote.payout.currency,
      method,
      description: `${quote.plan.name} (${cycle})`,
    };
    const intent = await this.providerFor(method).createIntent(input);
    return { quote, intent };
  }

  async checkout(input: CreateIntentInput): Promise<PaymentIntent> {
    return this.providerFor(input.method).createIntent(input);
  }
}
