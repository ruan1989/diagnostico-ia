import type { Currency } from "@aicos/shared";

export const CURRENCIES: Currency[] = [
  "BRL", "USD", "EUR", "GBP", "JPY", "INR", "MXN", "ARS", "NGN", "ZAR",
  "BTC", "ETH", "USDT", "USDC",
];
export const CRYPTO_CURRENCIES: Currency[] = ["BTC", "ETH", "USDT", "USDC"];
export const STABLECOINS: Currency[] = ["USDT", "USDC"];

export function isCrypto(currency: Currency): boolean {
  return CRYPTO_CURRENCIES.includes(currency);
}

/** Casas decimais para formatação/arredondamento por moeda. */
const DECIMALS: Partial<Record<Currency, number>> = {
  JPY: 0,
  BTC: 8,
  ETH: 6,
  USDT: 2,
  USDC: 2,
};

/** Value object imutável de dinheiro. Fiat e cripto no mesmo modelo. */
export class Money {
  readonly amount: number;
  constructor(amount: number, readonly currency: Currency) {
    if (!CURRENCIES.includes(currency)) throw new Error(`Moeda não suportada: ${currency}`);
    const d = DECIMALS[currency] ?? 2;
    this.amount = Math.round(amount * 10 ** d) / 10 ** d;
  }

  static of(amount: number, currency: Currency): Money {
    return new Money(amount, currency);
  }

  add(other: Money): Money {
    this.assertSame(other);
    return new Money(this.amount + other.amount, this.currency);
  }

  private assertSame(other: Money): void {
    if (other.currency !== this.currency) {
      throw new Error(`Moedas diferentes: ${this.currency} vs ${other.currency}`);
    }
  }

  format(locale = "pt-BR"): string {
    if (isCrypto(this.currency)) return `${this.amount} ${this.currency}`;
    return new Intl.NumberFormat(locale, { style: "currency", currency: this.currency }).format(this.amount);
  }
}

/**
 * Câmbio. Taxas de exemplo (estáticas, base USD) — em produção plugar um feed
 * (exchangerate/coingecko). É o que permite: comprador paga na moeda dele,
 * merchant recebe na moeda que preferir.
 */
const USD_RATES: Record<Currency, number> = {
  USD: 1,
  BRL: 5.4,
  EUR: 0.92,
  GBP: 0.79,
  JPY: 156,
  INR: 84,
  MXN: 18,
  ARS: 950,
  NGN: 1500,
  ZAR: 18,
  USDT: 1,
  USDC: 1,
  BTC: 1 / 68000,
  ETH: 1 / 3500,
};

export class ExchangeRate {
  toUsd(money: Money): number {
    return money.amount / USD_RATES[money.currency];
  }
  convert(money: Money, to: Currency): Money {
    return new Money(this.toUsd(money) * USD_RATES[to], to);
  }
  rate(from: Currency, to: Currency): number {
    return USD_RATES[to] / USD_RATES[from];
  }
}
