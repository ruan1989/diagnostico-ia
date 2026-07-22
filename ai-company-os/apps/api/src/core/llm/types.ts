export interface LlmMessage {
  role: "user" | "assistant" | "tool";
  content: string;
  /** Presente quando role === "tool": qual ferramenta produziu este conteúdo. */
  toolName?: string;
}

/** Spec de ferramenta apresentada ao LLM (nome + descrição + params). */
export interface LlmToolSpec {
  name: string;
  description: string;
  params: Record<string, { type: string; description: string; required?: boolean }>;
}

export interface CompletionRequest {
  system: string;
  messages: LlmMessage[];
  tools: LlmToolSpec[];
  temperature?: number;
}

export interface LlmToolCall {
  name: string;
  input: Record<string, unknown>;
}

export interface CompletionResult {
  /** Texto em linguagem natural (resposta final ou raciocínio). */
  text: string;
  /** Chamadas de ferramenta que o modelo decidiu fazer. Vazio = fim do loop. */
  toolCalls: LlmToolCall[];
}

export interface LlmProvider {
  readonly name: string;
  complete(req: CompletionRequest): Promise<CompletionResult>;
}
