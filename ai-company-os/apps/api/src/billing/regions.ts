import type { Currency } from "@aicos/shared";

/** Métodos/trilhos de pagamento suportados no mundo. */
export type PaymentMethod =
  | "card"
  | "pix"           // Brasil (instantâneo)
  | "boleto"        // Brasil
  | "sepa"          // Zona do Euro
  | "faster_payments" // Reino Unido
  | "ach"           // EUA
  | "fednow"        // EUA (instantâneo)
  | "upi"           // Índia
  | "spei"          // México
  | "oxxo"          // México (dinheiro)
  | "ideal"         // Holanda
  | "paypal"        // global
  | "wallet"        // carteiras (Apple/Google Pay)
  | "crypto";       // BTC/ETH/USDT/USDC (global)

export interface RegionProfile {
  country: string;   // ISO-3166 alpha-2
  name: string;
  currency: Currency;
  methods: PaymentMethod[];
}

/**
 * Perfil local por país: qual moeda o comprador vê e quais métodos locais
 * oferecemos. Cartão, PayPal e cripto são globais e sempre disponíveis.
 */
export const REGIONS: RegionProfile[] = [
  { country: "BR", name: "Brasil", currency: "BRL", methods: ["pix", "boleto", "card", "paypal", "crypto"] },
  { country: "US", name: "United States", currency: "USD", methods: ["card", "ach", "fednow", "paypal", "wallet", "crypto"] },
  { country: "GB", name: "United Kingdom", currency: "GBP", methods: ["card", "faster_payments", "paypal", "wallet", "crypto"] },
  { country: "DE", name: "Deutschland", currency: "EUR", methods: ["card", "sepa", "paypal", "crypto"] },
  { country: "NL", name: "Nederland", currency: "EUR", methods: ["card", "ideal", "sepa", "paypal", "crypto"] },
  { country: "IN", name: "India", currency: "INR", methods: ["upi", "card", "paypal", "crypto"] },
  { country: "MX", name: "México", currency: "MXN", methods: ["spei", "oxxo", "card", "paypal", "crypto"] },
  { country: "AR", name: "Argentina", currency: "ARS", methods: ["card", "paypal", "crypto"] },
  { country: "NG", name: "Nigeria", currency: "NGN", methods: ["card", "paypal", "crypto"] },
  { country: "ZA", name: "South Africa", currency: "ZAR", methods: ["card", "paypal", "crypto"] },
  { country: "JP", name: "日本", currency: "JPY", methods: ["card", "wallet", "paypal", "crypto"] },
];

const GLOBAL_FALLBACK: RegionProfile = {
  country: "XX",
  name: "Global",
  currency: "USD",
  methods: ["card", "paypal", "crypto"],
};

export function regionFor(country?: string): RegionProfile {
  if (!country) return GLOBAL_FALLBACK;
  return REGIONS.find((r) => r.country === country.toUpperCase()) ?? GLOBAL_FALLBACK;
}

/** Nome amigável de cada método (para exibir na UI). */
export const METHOD_LABELS: Record<PaymentMethod, string> = {
  card: "Cartão",
  pix: "PIX",
  boleto: "Boleto",
  sepa: "SEPA",
  faster_payments: "Faster Payments",
  ach: "ACH",
  fednow: "FedNow",
  upi: "UPI",
  spei: "SPEI",
  oxxo: "OXXO",
  ideal: "iDEAL",
  paypal: "PayPal",
  wallet: "Carteira digital",
  crypto: "Cripto",
};
