// Contratos compartilhados entre API e Web.

export type Role = "owner" | "admin" | "manager" | "member" | "viewer";

export interface RequestContext {
  tenantId: string;
  userId: string;
  role: Role;
}

/** Uma mensagem no chat da tela única. */
export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

/** Um passo do plano que o AI Core executou (para transparência na UI). */
export interface ExecutedStep {
  agent: string;
  tool: string;
  input: Record<string, unknown>;
  ok: boolean;
  summary: string;
}

/** Resposta do endpoint /chat: texto + rastro do que a IA fez + dados ricos. */
export interface ChatResponse {
  reply: string;
  steps: ExecutedStep[];
  /** Payloads estruturados para a UI renderizar (tabelas, cards, gráficos). */
  data: Array<{ kind: string; title: string; payload: unknown }>;
}

/** Moedas suportadas (fiat + cripto/stablecoin). Adaptável ao mundo todo. */
export type Currency =
  | "BRL" | "USD" | "EUR" | "GBP" | "JPY" | "INR" | "MXN" | "ARS" | "NGN" | "ZAR"
  | "BTC" | "ETH" | "USDT" | "USDC";

export interface MoneyDTO {
  amount: number; // em unidade principal (ex.: reais, não centavos)
  currency: Currency;
}

/** Idiomas da interface e da IA. */
export type Lang = "pt" | "en";
