import type { ChatResponse, ExecutedStep, Lang, RequestContext } from "@aicos/shared";
import { AppError } from "../../platform/errors.js";
import { agentForTool } from "../agents/definitions.js";
import type { LlmProvider, LlmMessage, LlmToolSpec } from "../llm/types.js";
import type { MemoryStore } from "../memory/memory.js";
import type { ToolRegistry } from "../tools/registry.js";

const MAX_STEPS = 5;

const SYSTEM_PROMPT = `Você é o AI Company OS — o sistema operacional de uma empresa,
operado por conversa. O usuário administra a empresa inteira falando com você.
Você interpreta o pedido, planeja, delega ao agente especializado e EXECUTA usando
as ferramentas disponíveis. Seja objetivo e confirme o que fez com números reais.
Nunca invente dados: use as ferramentas para ler ou escrever informação.`;

/**
 * O cérebro. Recebe uma mensagem em linguagem natural e conduz o ciclo completo:
 * interpretar → planejar → delegar → executar (tool calling) → aprender (memória).
 */
export class Orchestrator {
  constructor(
    private readonly llm: LlmProvider,
    private readonly tools: ToolRegistry,
    private readonly memory: MemoryStore,
  ) {}

  async handle(ctx: RequestContext, userMessage: string, lang: Lang = "pt"): Promise<ChatResponse> {
    const specs = this.toSpecs(ctx);
    const messages: LlmMessage[] = [...this.history(ctx), { role: "user", content: userMessage }];
    const steps: ExecutedStep[] = [];
    const data: NonNullable<ChatResponse["data"]> = [];

    const system = this.buildSystem(ctx, userMessage, lang);
    let reply = "";

    for (let i = 0; i < MAX_STEPS; i++) {
      const result = await this.llm.complete({ system, messages, tools: specs, temperature: 0.2 });

      if (result.toolCalls.length === 0) {
        reply = result.text;
        break;
      }

      for (const call of result.toolCalls) {
        const agent = agentForTool(call.name);
        let ok = true;
        let summary: string;
        try {
          const res = await this.tools.execute(ctx, call.name, call.input);
          ok = res.ok;
          summary = res.summary;
          if (res.data) data.push(res.data);
        } catch (err) {
          ok = false;
          summary = err instanceof AppError ? err.message : `Falha ao executar ${call.name}.`;
        }
        steps.push({ agent: agent.name, tool: call.name, input: call.input, ok, summary });
        messages.push({ role: "assistant", content: `Chamando ${call.name}…` });
        messages.push({ role: "tool", toolName: call.name, content: summary });
      }
    }

    if (!reply) {
      reply = steps.length
        ? `Feito:\n${steps.map((s) => `• ${s.summary}`).join("\n")}`
        : "Não consegui concluir o pedido. Pode reformular?";
    }

    this.learn(ctx, userMessage, reply, steps);
    return { reply, steps, data };
  }

  private toSpecs(ctx: RequestContext): LlmToolSpec[] {
    return this.tools.availableFor(ctx).map((t) => ({
      name: t.name,
      description: t.description,
      params: t.params,
    }));
  }

  private buildSystem(ctx: RequestContext, query: string, lang: Lang): string {
    const recalled = this.memory.recall(ctx, query);
    const facts = recalled.length
      ? `\n\nContexto conhecido da empresa:\n${recalled.map((r) => `- ${r.content}`).join("\n")}`
      : "";
    const langLine =
      lang === "en"
        ? "\n\nRespond to the user in English."
        : "\n\nResponda ao usuário em português.";
    return `${SYSTEM_PROMPT}\n\nEmpresa (tenant): ${ctx.tenantId}. Usuário: ${ctx.userId} (papel: ${ctx.role}).${facts}${langLine}`;
  }

  private history(ctx: RequestContext): LlmMessage[] {
    return this.memory.recentShortTerm(ctx.tenantId).flatMap((r) => {
      const [role, ...rest] = r.content.split("::");
      const content = rest.join("::");
      if (role === "user") return [{ role: "user", content } as LlmMessage];
      if (role === "assistant") return [{ role: "assistant", content } as LlmMessage];
      return [];
    });
  }

  private learn(ctx: RequestContext, userMessage: string, reply: string, steps: ExecutedStep[]): void {
    this.memory.remember(ctx, "short", `user::${userMessage}`);
    this.memory.remember(ctx, "short", `assistant::${reply}`);
    // Ações bem-sucedidas viram fatos duradouros da empresa (aprendizado).
    for (const step of steps.filter((s) => s.ok)) {
      this.memory.remember(ctx, "long", step.summary);
    }
  }
}
