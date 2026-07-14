import type { Currency } from "@aicos/shared";
import { Money } from "./money.js";
import {
  MockCryptoProvider,
  MockFiatProvider,
  type CreateIntentInput,
  type PaymentIntent,
  type PaymentMethod,
  type PaymentProvider,
} from "./provider.js";

export type PlanId = "free" | "starter" | "business" | "enterprise" | "white_label";
export type BillingCycle = "monthly" | "annual";

export interface Plan {
  id: PlanId;
  name: string;
  priceMonthlyUsd: number;
  features: string[];
}

export const PLANS: Plan[] = [
  { id: "free", name: "Free", priceMonthlyUsd: 0, features: ["1 usuário", "CRM básico", "IA com cota"] },
  { id: "starter", name: "Starter", priceMonthlyUsd: 29, features: ["CRM + Financeiro", "3 usuários"] },
  { id: "business", name: "Business", priceMonthlyUsd: 99, features: ["Todos os módulos", "Automações", "10 usuários"] },
  { id: "enterprise", name: "Enterprise", priceMonthlyUsd: 499, features: ["SSO", "Auditoria", "SLA", "Modelo dedicado"] },
  { id: "white_label", name: "White-Label", priceMonthlyUsd: 1500, features: ["Marca própria", "Marketplace", "Revenda"] },
];

/**
 * Orquestra cobrança em múltiplas moedas (fiat + cripto) e múltiplos métodos.
 * Escolhe o provedor certo por método e converte o preço do plano para a moeda
 * pedida pelo cliente.
 */
export class BillingService {
  private readonly providers: PaymentProvider[] = [new MockFiatProvider(), new MockCryptoProvider()];

  plans(): Plan[] {
    return PLANS;
  }

  private providerFor(method: PaymentMethod): PaymentProvider {
    const provider = this.providers.find((p) => p.supports(method));
    if (!provider) throw new Error(`Nenhum provedor suporta o método ${method}.`);
    return provider;
  }

  async checkout(input: CreateIntentInput): Promise<PaymentIntent> {
    return this.providerFor(input.method).createIntent(input);
  }

  /** Cria a cobrança de uma assinatura de plano numa moeda/método escolhidos. */
  async subscribe(
    planId: PlanId,
    cycle: BillingCycle,
    method: PaymentMethod,
    currency: Currency,
  ): Promise<{ plan: Plan; amount: Money; intent: PaymentIntent }> {
    const plan = PLANS.find((p) => p.id === planId);
    if (!plan) throw new Error(`Plano desconhecido: ${planId}`);
    const months = cycle === "annual" ? 12 : 1;
    const discount = cycle === "annual" ? 0.83 : 1; // ~2 meses grátis no anual
    const usd = Money.of(plan.priceMonthlyUsd * months * discount, "USD");
    const amount = new (await import("./money.js")).ExchangeRate().convert(usd, currency);
    const intent = await this.checkout({
      amount,
      method,
      description: `Assinatura ${plan.name} (${cycle})`,
    });
    return { plan, amount, intent };
  }
}
