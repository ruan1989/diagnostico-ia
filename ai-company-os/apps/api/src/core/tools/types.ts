import type { RequestContext, Role } from "@aicos/shared";

/** Descrição de um parâmetro de ferramenta (subset de JSON Schema). */
export interface ParamSpec {
  type: "string" | "number" | "boolean";
  description: string;
  required?: boolean;
}

export interface ToolResult {
  ok: boolean;
  /** Resumo em linguagem natural do que aconteceu (a IA usa isto). */
  summary: string;
  /** Dado estruturado opcional para a UI renderizar (tabela, card, gráfico). */
  data?: { kind: string; title: string; payload: unknown };
}

/**
 * Uma capacidade que um módulo oferece à IA. O AI Core só interage com o
 * mundo através destas ferramentas.
 */
export interface Tool {
  name: string; // ex.: "crm.create_customer"
  description: string;
  params: Record<string, ParamSpec>;
  /** Papéis autorizados. Vazio = todos os papéis autenticados. */
  allowedRoles?: Role[];
  handler: (ctx: RequestContext, input: Record<string, unknown>) => Promise<ToolResult> | ToolResult;
}
