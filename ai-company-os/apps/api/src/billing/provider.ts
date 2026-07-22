import { randomUUID } from "node:crypto";
import type { Currency } from "@aicos/shared";
import { ExchangeRate, isCrypto, Money } from "./money.js";
import type { PaymentMethod } from "./regions.js";

/**
 * Intenção de pagamento com DUAS pernas:
 *  - pay-in:  o que o comprador paga, na moeda local dele, pelo método local.
 *  - payout:  o que o merchant recebe, na moeda que ele preferir (liquidação).
 * É o coração do "pague na sua moeda, receba na minha".
 */
export interface PaymentIntent {
  id: string;
  provider: string;
  method: PaymentMethod;
  payinAmount: number;
  payinCurrency: Currency;
  payoutAmount: number;
  payoutCurrency: Currency;
  fxRate: number;
  status: "requires_payment" | "processing" | "paid" | "failed";
  /** Cartão/PIX: URL/QR de checkout. Cripto: endereço da carteira. */
  actionUrl?: string;
  createdAt: string;
}

export interface CreateIntentInput {
  payin: Money;             // valor e moeda que o comprador paga
  payoutCurrency: Currency; // moeda que o merchant recebe
  method: PaymentMethod;
  description: string;
}

export interface PaymentProvider {
  readonly name: string;
  supports(method: PaymentMethod): boolean;
  createIntent(input: CreateIntentInput): Promise<PaymentIntent>;
}

const fx = new ExchangeRate();

function settle(input: CreateIntentInput) {
  const payout = fx.convert(input.payin, input.payoutCurrency);
  return { payout, fxRate: fx.rate(input.payin.currency, input.payoutCurrency) };
}

/**
 * Orquestrador fiat mock: cobre PIX, boleto, cartão, SEPA, ACH, FedNow, UPI,
 * SPEI, OXXO, iDEAL, PayPal e carteiras — selecionando o "trilho" local.
 * Troque por Stripe/Adyen/Mercado Pago/dLocal/Yuno em produção.
 */
export class FiatOrchestrator implements PaymentProvider {
  readonly name = "mock-fiat-orchestrator";
  supports(method: PaymentMethod): boolean {
    return method !== "crypto";
  }
  async createIntent(input: CreateIntentInput): Promise<PaymentIntent> {
    const id = `pi_${randomUUID().slice(0, 8)}`;
    const { payout, fxRate } = settle(input);
    const instant = ["pix", "fednow", "upi", "spei", "faster_payments"].includes(input.method);
    return {
      id,
      provider: this.name,
      method: input.method,
      payinAmount: input.payin.amount,
      payinCurrency: input.payin.currency,
      payoutAmount: payout.amount,
      payoutCurrency: input.payoutCurrency,
      fxRate,
      status: instant ? "processing" : "requires_payment",
      actionUrl: `https://pay.aicompanyos.local/${input.method}/${id}`,
      createdAt: new Date().toISOString(),
    };
  }
}

/**
 * Provedor cripto mock (BTC/ETH/USDT/USDC). Gera endereço de recebimento e
 * calcula a liquidação para a moeda do merchant. Stablecoins servem de ponte
 * de liquidação global. Troque por Coinbase Commerce/BitPay/on-chain watcher.
 */
export class CryptoProvider implements PaymentProvider {
  readonly name = "mock-crypto";
  supports(method: PaymentMethod): boolean {
    return method === "crypto";
  }
  async createIntent(input: CreateIntentInput): Promise<PaymentIntent> {
    if (!isCrypto(input.payin.currency)) {
      throw new Error("Pagamento cripto exige moeda cripto (BTC/ETH/USDT/USDC).");
    }
    const id = `cpi_${randomUUID().slice(0, 8)}`;
    const { payout, fxRate } = settle(input);
    return {
      id,
      provider: this.name,
      method: "crypto",
      payinAmount: input.payin.amount,
      payinCurrency: input.payin.currency,
      payoutAmount: payout.amount,
      payoutCurrency: input.payoutCurrency,
      fxRate,
      status: "processing",
      actionUrl: wallet(input.payin.currency),
      createdAt: new Date().toISOString(),
    };
  }
}

function wallet(currency: Currency): string {
  const map: Partial<Record<Currency, string>> = {
    BTC: "bc1qexampleaicoswalletxxxxxxxxxxxxxxxxxx",
    ETH: "0xExampleAiCosWalletxxxxxxxxxxxxxxxxxxxxxx",
    USDT: "0xExampleAiCosUsdtWalletxxxxxxxxxxxxxxxxx",
    USDC: "0xExampleAiCosUsdcWalletxxxxxxxxxxxxxxxxx",
  };
  return map[currency] ?? "unknown";
}
