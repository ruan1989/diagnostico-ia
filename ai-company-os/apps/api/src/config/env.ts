export interface Env {
  port: number;
  llmProvider: "mock" | "anthropic" | "openai";
  anthropicApiKey?: string;
  anthropicModel: string;
  openaiApiKey?: string;
  openaiModel: string;
  jwtSecret: string;
  billingProvider: string;
  /** Moeda-base dos preços dos planos. */
  baseCurrency: string;
  /** Moeda em que o dono do sistema quer receber (liquidação). */
  payoutCurrency: string;
  /** Segredo para verificar assinaturas de webhook de pagamento. */
  webhookSecret: string;
}

export function loadEnv(): Env {
  const provider = (process.env.LLM_PROVIDER ?? "mock").toLowerCase();
  return {
    port: Number(process.env.API_PORT ?? 4000),
    llmProvider: (["mock", "anthropic", "openai"].includes(provider)
      ? provider
      : "mock") as Env["llmProvider"],
    anthropicApiKey: process.env.ANTHROPIC_API_KEY || undefined,
    anthropicModel: process.env.ANTHROPIC_MODEL ?? "claude-opus-4-8",
    openaiApiKey: process.env.OPENAI_API_KEY || undefined,
    openaiModel: process.env.OPENAI_MODEL ?? "gpt-4o",
    jwtSecret: process.env.JWT_SECRET ?? "dev-secret",
    billingProvider: process.env.BILLING_PROVIDER ?? "mock",
    baseCurrency: process.env.BASE_CURRENCY ?? "BRL",
    payoutCurrency: process.env.MERCHANT_PAYOUT_CURRENCY ?? "BRL",
    webhookSecret: process.env.WEBHOOK_SECRET ?? process.env.JWT_SECRET ?? "dev-webhook-secret",
  };
}
