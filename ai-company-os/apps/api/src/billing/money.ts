import type { Currency } from "@aicos/shared";

export const CURRENCIES: Currency[] = ["BRL", "USD", "EUR", "BTC", "ETH", "USDT"];
export const CRYPTO_CURRENCIES: Currency[] = ["BTC", "ETH", "USDT"];

export function isCrypto(currency: Currency): boolean {
  return CRYPTO_CURRENCIES.includes(currency);
}

/** Value object imutável de dinheiro. Fiat e cripto no mesmo modelo. */
export class Money {
  constructor(
    readonly amount: number,
    readonly currency: Currency,
  ) {
    if (!CURRENCIES.includes(currency)) throw new Error(`Moeda não suportada: ${currency}`);
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

  format(): string {
    if (isCrypto(this.currency)) return `${this.amount} ${this.currency}`;
    return new Intl.NumberFormat("pt-BR", { style: "currency", currency: this.currency }).format(this.amount);
  }
}

/**
 * Conversão de câmbio. Taxas de exemplo (estáticas) — em produção plugar um
 * feed (ex.: exchangerate/coingecko). Base de referência: USD.
 */
const USD_RATES: Record<Currency, number> = {
  USD: 1,
  BRL: 5.4,
  EUR: 0.92,
  BTC: 1 / 68000,
  ETH: 1 / 3500,
  USDT: 1,
};

export class ExchangeRate {
  convert(money: Money, to: Currency): Money {
    const usd = money.amount / USD_RATES[money.currency];
    return new Money(usd * USD_RATES[to], to);
  }
}
