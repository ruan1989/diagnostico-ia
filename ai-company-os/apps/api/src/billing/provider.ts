import { randomUUID } from "node:crypto";
import type { Currency } from "@aicos/shared";
import { isCrypto, Money } from "./money.js";

export type PaymentMethod = "card" | "pix" | "boleto" | "paypal" | "crypto";

export interface PaymentIntent {
  id: string;
  provider: string;
  method: PaymentMethod;
  amount: number;
  currency: Currency;
  status: "requires_payment" | "processing" | "paid" | "failed";
  /** Para cartão/PIX: URL de checkout. Para cripto: endereço da carteira. */
  actionUrl?: string;
  createdAt: string;
}

export interface CreateIntentInput {
  amount: Money;
  method: PaymentMethod;
  description: string;
}

/** Contrato de qualquer provedor de pagamento (Stripe, Mercado Pago, Crypto…). */
export interface PaymentProvider {
  readonly name: string;
  supports(method: PaymentMethod): boolean;
  createIntent(input: CreateIntentInput): Promise<PaymentIntent>;
}

/**
 * Provedor mock que cobre cartão, PIX, boleto e PayPal. Simula checkout sem
 * chamar nenhuma API externa — troque por Stripe/Mercado Pago em produção.
 */
export class MockFiatProvider implements PaymentProvider {
  readonly name = "mock-fiat";
  supports(method: PaymentMethod): boolean {
    return method !== "crypto";
  }
  async createIntent(input: CreateIntentInput): Promise<PaymentIntent> {
    const id = `pi_${randomUUID().slice(0, 8)}`;
    return {
      id,
      provider: this.name,
      method: input.method,
      amount: input.amount.amount,
      currency: input.amount.currency,
      status: "requires_payment",
      actionUrl: `https://checkout.local/${input.method}/${id}`,
      createdAt: new Date().toISOString(),
    };
  }
}

/**
 * Provedor cripto mock (BTC/ETH/USDT). Gera um endereço de recebimento e uma
 * intenção "aguardando confirmação on-chain". Troque por um gateway real
 * (Coinbase Commerce, BitPay, ou watcher on-chain próprio).
 */
export class MockCryptoProvider implements PaymentProvider {
  readonly name = "mock-crypto";
  supports(method: PaymentMethod): boolean {
    return method === "crypto";
  }
  async createIntent(input: CreateIntentInput): Promise<PaymentIntent> {
    if (!isCrypto(input.amount.currency)) {
      throw new Error("Pagamento cripto exige moeda cripto (BTC/ETH/USDT).");
    }
    const id = `cpi_${randomUUID().slice(0, 8)}`;
    const wallet = mockWallet(input.amount.currency);
    return {
      id,
      provider: this.name,
      method: "crypto",
      amount: input.amount.amount,
      currency: input.amount.currency,
      status: "processing",
      actionUrl: wallet,
      createdAt: new Date().toISOString(),
    };
  }
}

function mockWallet(currency: Currency): string {
  const map: Partial<Record<Currency, string>> = {
    BTC: "bc1qexampleaicoswalletxxxxxxxxxxxxxxxxxx",
    ETH: "0xExampleAiCosWalletxxxxxxxxxxxxxxxxxxxxxx",
    USDT: "0xExampleAiCosUsdtWalletxxxxxxxxxxxxxxxxx",
  };
  return map[currency] ?? "unknown";
}
