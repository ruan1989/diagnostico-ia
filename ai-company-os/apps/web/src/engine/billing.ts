// Motor de cobrança global rodando no navegador: planos a partir de 250,
// conversão comprador→merchant e métodos por país. Espelha apps/api/billing.
import type { Currency } from "@aicos/shared";

const USD_RATES: Record<string, number> = {
  USD: 1, BRL: 5.4, EUR: 0.92, GBP: 0.79, JPY: 156, INR: 84, MXN: 18, ARS: 950, NGN: 1500, ZAR: 18,
  USDT: 1, USDC: 1, BTC: 1 / 68000, ETH: 1 / 3500,
};
const CRYPTO = ["BTC", "ETH", "USDT", "USDC"];
const DECIMALS: Record<string, number> = { JPY: 0, BTC: 8, ETH: 6 };

function round(v: number, cur: string) { const d = DECIMALS[cur] ?? 2; return Math.round(v * 10 ** d) / 10 ** d; }
function convert(amount: number, from: string, to: string) { return round((amount / USD_RATES[from]) * USD_RATES[to], to); }

export interface Plan { id: string; name: string; basePriceMonthly: number; features: string[]; highlighted?: boolean }
export const PLANS: Plan[] = [
  { id: "trial", name: "Trial", basePriceMonthly: 0, features: ["14 dias grátis", "1 usuário", "CRM + IA com cota"] },
  { id: "starter", name: "Starter", basePriceMonthly: 250, features: ["CRM + Financeiro", "3 usuários", "Agentes essenciais"] },
  { id: "business", name: "Business", basePriceMonthly: 650, features: ["Todos os módulos", "Automações", "Conselho de Agentes", "10 usuários"], highlighted: true },
  { id: "enterprise", name: "Enterprise", basePriceMonthly: 1900, features: ["SSO + Auditoria", "SLA", "IA dedicada", "Usuários ilimitados"] },
  { id: "white_label", name: "White-Label", basePriceMonthly: 4900, features: ["Marca própria", "Marketplace", "Revenda"] },
];

const METHOD_LABELS: Record<string, string> = {
  card: "Cartão", pix: "PIX", boleto: "Boleto", sepa: "SEPA", faster_payments: "Faster Payments",
  ach: "ACH", fednow: "FedNow", upi: "UPI", spei: "SPEI", oxxo: "OXXO", ideal: "iDEAL",
  paypal: "PayPal", wallet: "Carteira digital", crypto: "Cripto",
};
interface Region { country: string; name: string; currency: string; methods: string[] }
const REGIONS: Region[] = [
  { country: "BR", name: "Brasil", currency: "BRL", methods: ["pix", "boleto", "card", "paypal", "crypto"] },
  { country: "US", name: "United States", currency: "USD", methods: ["card", "ach", "fednow", "paypal", "wallet", "crypto"] },
  { country: "GB", name: "United Kingdom", currency: "GBP", methods: ["card", "faster_payments", "paypal", "wallet", "crypto"] },
  { country: "DE", name: "Deutschland", currency: "EUR", methods: ["card", "sepa", "paypal", "crypto"] },
  { country: "IN", name: "India", currency: "INR", methods: ["upi", "card", "paypal", "crypto"] },
  { country: "MX", name: "México", currency: "MXN", methods: ["spei", "oxxo", "card", "paypal", "crypto"] },
  { country: "JP", name: "日本", currency: "JPY", methods: ["card", "wallet", "paypal", "crypto"] },
  { country: "NG", name: "Nigeria", currency: "NGN", methods: ["card", "paypal", "crypto"] },
];
const GLOBAL: Region = { country: "XX", name: "Global", currency: "USD", methods: ["card", "paypal", "crypto"] };
function regionFor(country?: string) { return REGIONS.find((r) => r.country === country?.toUpperCase()) ?? GLOBAL; }

export function enginePlans() { return { baseCurrency: "BRL" as Currency, plans: PLANS }; }

export function engineOptions(country: string) {
  const r = regionFor(country);
  return { country: r.country, countryName: r.name, localCurrency: r.currency as Currency, methods: r.methods.map((m) => ({ id: m, label: METHOD_LABELS[m] })) };
}

export function engineQuote(planId: string, country: string, method: string, cycle: "monthly" | "annual" = "monthly") {
  const plan = PLANS.find((p) => p.id === planId) ?? PLANS[1];
  const months = cycle === "annual" ? 10 : 1;
  const base = plan.basePriceMonthly * months;
  const payinCur = method === "crypto" ? "USDT" : regionFor(country).currency;
  const payoutCur = "BRL"; // moeda que o dono recebe (config no backend real)
  return {
    plan, cycle, method, methodLabel: METHOD_LABELS[method] ?? method, country: regionFor(country).country,
    payin: { amount: convert(base, "BRL", payinCur), currency: payinCur as Currency },
    payout: { amount: convert(base, "BRL", payoutCur), currency: payoutCur as Currency },
    fxRate: USD_RATES[payoutCur] / USD_RATES[payinCur],
  };
}

export const isCrypto = (c: string) => CRYPTO.includes(c);
