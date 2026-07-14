import type { Env } from "../../config/env.js";
import { AnthropicProvider } from "./providers/anthropic.js";
import { MockLlmProvider } from "./providers/mock.js";
import { OpenAiProvider } from "./providers/openai.js";
import type { CompletionRequest, CompletionResult, LlmProvider } from "./types.js";

/**
 * Roteia chamadas para o provedor de LLM configurado, com fallback automático
 * para o mock (o sistema nunca fica indisponível por falta de chave/rede).
 * A troca por custo/desempenho acontece aqui — hoje por configuração, amanhã
 * por política dinâmica (barato para tarefas simples, forte para complexas).
 */
export class LlmRouter implements LlmProvider {
  readonly name = "router";
  private readonly primary: LlmProvider;
  private readonly fallback = new MockLlmProvider();

  constructor(env: Env) {
    this.primary = LlmRouter.build(env);
  }

  private static build(env: Env): LlmProvider {
    if (env.llmProvider === "anthropic" && env.anthropicApiKey) {
      return new AnthropicProvider(env.anthropicApiKey, env.anthropicModel);
    }
    if (env.llmProvider === "openai" && env.openaiApiKey) {
      return new OpenAiProvider(env.openaiApiKey, env.openaiModel);
    }
    return new MockLlmProvider();
  }

  async complete(req: CompletionRequest): Promise<CompletionResult> {
    try {
      return await this.primary.complete(req);
    } catch (err) {
      // Degrada com elegância: se o provedor real falhar, usa o mock.
      console.warn(`[llm] provedor '${this.primary.name}' falhou, usando mock:`, (err as Error).message);
      return this.fallback.complete(req);
    }
  }

  get activeProvider(): string {
    return this.primary.name;
  }
}
