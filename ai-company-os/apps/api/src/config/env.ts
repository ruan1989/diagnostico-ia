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
  /** Ambiente de execução. */
  nodeEnv: string;
  /** Origens permitidas no CORS (produção). Vazio = libera (apenas dev). */
  allowedOrigins: string[];
}

const DEFAULT_SECRETS = new Set(["dev-secret", "change-me-in-production", "dev-webhook-secret", "change-me-too"]);

/**
 * Falha rápido em produção se segredos padrão forem detectados — impede subir
 * com credenciais fracas por engano.
 */
export function assertProductionSafe(env: Env): void {
  if (env.nodeEnv !== "production") return;
  const weak: string[] = [];
  if (DEFAULT_SECRETS.has(env.jwtSecret)) weak.push("JWT_SECRET");
  if (DEFAULT_SECRETS.has(env.webhookSecret)) weak.push("WEBHOOK_SECRET");
  if (weak.length) {
    throw new Error(`Segredos padrão em produção: ${weak.join(", ")}. Defina valores fortes antes de subir.`);
  }
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
    nodeEnv: process.env.NODE_ENV ?? "development",
    allowedOrigins: (process.env.ALLOWED_ORIGINS ?? "").split(",").map((s) => s.trim()).filter(Boolean),
  };
}
