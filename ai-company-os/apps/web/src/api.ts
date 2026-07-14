import type { ChatResponse, Currency, Lang } from "@aicos/shared";
import { engineChat, engineHealth } from "./engine/engine.js";
import { engineOptions, enginePlans, engineQuote } from "./engine/billing.js";

// Se VITE_API_URL estiver definido, usa o backend real (HTTP). Caso contrário,
// roda 100% no navegador (motor client-side) — é assim no GitHub Pages.
const API = (import.meta.env.VITE_API_URL as string | undefined) || "";
const useBackend = API.length > 0;

export async function sendChat(message: string, lang: Lang, token?: string): Promise<ChatResponse> {
  if (!useBackend) return engineChat(message, lang);
  const res = await fetch(`${API}/chat`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ message, lang }),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { message?: string };
    throw new Error(err.message ?? `Erro ${res.status}`);
  }
  return (await res.json()) as ChatResponse;
}

export interface Health { status: string; llm: string; tools: number; baseCurrency: Currency; payoutCurrency: Currency }
export async function health(): Promise<Health> {
  if (!useBackend) return engineHealth();
  const res = await fetch(`${API}/health`);
  return (await res.json()) as Health;
}

export interface Plan { id: string; name: string; basePriceMonthly: number; features: string[]; highlighted?: boolean }
export async function getPlans(): Promise<{ baseCurrency: Currency; plans: Plan[] }> {
  if (!useBackend) return enginePlans();
  const res = await fetch(`${API}/billing/plans`);
  return (await res.json()) as { baseCurrency: Currency; plans: Plan[] };
}

export interface PaymentOptions { country: string; countryName: string; localCurrency: Currency; methods: { id: string; label: string }[] }
export async function getOptions(country: string): Promise<PaymentOptions> {
  if (!useBackend) return engineOptions(country);
  const res = await fetch(`${API}/billing/options?country=${country}`);
  return (await res.json()) as PaymentOptions;
}

export interface Quote {
  plan: Plan; cycle: string; method: string; methodLabel: string; country: string;
  payin: { amount: number; currency: Currency }; payout: { amount: number; currency: Currency }; fxRate: number;
}
export async function getQuote(body: { planId: string; country: string; method: string }): Promise<Quote> {
  if (!useBackend) return engineQuote(body.planId, body.country, body.method) as Quote;
  const res = await fetch(`${API}/billing/quote`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  return (await res.json()) as Quote;
}
