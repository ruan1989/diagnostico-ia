import type { ChatResponse, Currency, Lang } from "@aicos/shared";

const BASE = "/api";

export async function sendChat(message: string, lang: Lang, token?: string): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message, lang }),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { message?: string };
    throw new Error(err.message ?? `Erro ${res.status}`);
  }
  return (await res.json()) as ChatResponse;
}

export interface Health {
  status: string;
  llm: string;
  tools: number;
  baseCurrency: Currency;
  payoutCurrency: Currency;
}

export async function health(): Promise<Health> {
  const res = await fetch(`${BASE}/health`);
  return (await res.json()) as Health;
}

export interface Plan {
  id: string;
  name: string;
  basePriceMonthly: number;
  features: string[];
  highlighted?: boolean;
}

export async function getPlans(): Promise<{ baseCurrency: Currency; plans: Plan[] }> {
  const res = await fetch(`${BASE}/billing/plans`);
  return (await res.json()) as { baseCurrency: Currency; plans: Plan[] };
}

export interface PaymentOptions {
  country: string;
  countryName: string;
  localCurrency: Currency;
  methods: { id: string; label: string }[];
}

export async function getOptions(country: string): Promise<PaymentOptions> {
  const res = await fetch(`${BASE}/billing/options?country=${country}`);
  return (await res.json()) as PaymentOptions;
}

export interface Quote {
  plan: Plan;
  cycle: string;
  method: string;
  methodLabel: string;
  country: string;
  payin: { amount: number; currency: Currency };
  payout: { amount: number; currency: Currency };
  fxRate: number;
}

export async function getQuote(body: {
  planId: string;
  country: string;
  method: string;
}): Promise<Quote> {
  const res = await fetch(`${BASE}/billing/quote`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await res.json()) as Quote;
}
