import type { RequestContext } from "@aicos/shared";
import { ForbiddenError, NotFoundError } from "../../platform/errors.js";
import type { Tool, ToolResult } from "./types.js";

export class ToolRegistry {
  private readonly tools = new Map<string, Tool>();

  register(tool: Tool): void {
    if (this.tools.has(tool.name)) {
      throw new Error(`Ferramenta duplicada: ${tool.name}`);
    }
    this.tools.set(tool.name, tool);
  }

  get(name: string): Tool | undefined {
    return this.tools.get(name);
  }

  /** Ferramentas que o papel do contexto pode executar. */
  availableFor(ctx: RequestContext): Tool[] {
    return [...this.tools.values()].filter((t) => this.isAllowed(t, ctx));
  }

  private isAllowed(tool: Tool, ctx: RequestContext): boolean {
    if (!tool.allowedRoles || tool.allowedRoles.length === 0) return true;
    return tool.allowedRoles.includes(ctx.role);
  }

  async execute(
    ctx: RequestContext,
    name: string,
    input: Record<string, unknown>,
  ): Promise<ToolResult> {
    const tool = this.tools.get(name);
    if (!tool) throw new NotFoundError(`Ferramenta desconhecida: ${name}`);
    if (!this.isAllowed(tool, ctx)) {
      throw new ForbiddenError(`Seu papel (${ctx.role}) não pode usar ${name}.`);
    }
    return tool.handler(ctx, input);
  }
}
