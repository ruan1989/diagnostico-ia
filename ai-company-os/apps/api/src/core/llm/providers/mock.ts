import type {
  CompletionRequest,
  CompletionResult,
  LlmProvider,
  LlmToolCall,
} from "../types.js";

/**
 * Provedor determinístico que NÃO precisa de chave de API. Ele faz o sistema
 * inteiro ser demonstrável offline: interpreta o pedido do usuário (heurística
 * em PT-BR) e emite chamadas de ferramenta reais. Não é um LLM — é um
 * "planejador de brinquedo" fiel o suficiente para exercitar todo o AI Core.
 */
export class MockLlmProvider implements LlmProvider {
  readonly name = "mock";

  async complete(req: CompletionRequest): Promise<CompletionResult> {
    const last = req.messages[req.messages.length - 1];

    // Se o último item já é resultado de ferramenta, encerramos com um resumo.
    const toolResults = req.messages.filter((m) => m.role === "tool");
    if (last?.role === "tool") {
      const summary = toolResults.map((m) => `• ${m.content}`).join("\n");
      return { text: `Pronto. Aqui está o que fiz:\n${summary}`, toolCalls: [] };
    }

    const userText = [...req.messages].reverse().find((m) => m.role === "user")?.content ?? "";
    const available = new Set(req.tools.map((t) => t.name));
    const calls = this.plan(userText, available);

    if (calls.length === 0) {
      return {
        text:
          "Entendi. Posso cadastrar clientes, criar propostas, lançar receitas/despesas, " +
          "mostrar seu lucro e fluxo de caixa, ou reunir o conselho de agentes. O que deseja?",
        toolCalls: [],
      };
    }
    return { text: "", toolCalls: calls };
  }

  /** Traduz uma frase em PT-BR num conjunto de chamadas de ferramenta. */
  private plan(text: string, available: Set<string>): LlmToolCall[] {
    const t = text.toLowerCase();
    const calls: LlmToolCall[] = [];
    const has = (name: string) => available.has(name);

    // Conselho de agentes
    if ((/\bconselho\b|delibere|debata|reúna os agentes/.test(t)) && has("council.deliberate")) {
      const topic = text.replace(/.*conselho[:,]?\s*/i, "").trim() || text;
      return [{ name: "council.deliberate", input: { topic } }];
    }

    // Cadastro de cliente
    if (/cadastr\w+|nov[oa] cliente|adicion\w+ cliente/.test(t) && has("crm.create_customer")) {
      const email = this.extractEmail(text);
      const name = this.extractPersonName(text) ?? "Novo Cliente";
      calls.push({ name: "crm.create_customer", input: { name, email: email ?? "" } });
      return calls;
    }

    // Proposta / orçamento
    if (/proposta|orçament|orcament/.test(t) && has("crm.create_proposal")) {
      const amount = this.extractAmount(text) ?? 0;
      const customer = this.extractAfter(text, /para\s+/i) ?? this.extractPersonName(text) ?? "";
      return [{ name: "crm.create_proposal", input: { customer, amount } }];
    }

    // Lançamento financeiro
    if (/lanc\w+|lança|registr\w+|receb\w+|paguei|gast\w+|despesa|receita/.test(t) && has("finance.record_entry")) {
      const amount = this.extractAmount(text) ?? 0;
      const isExpense = /despesa|paguei|gast\w+|pagar|conta/.test(t) && !/receita|receb/.test(t);
      const description = this.extractAfter(text, /(de|da|do|com)\s+/i) ?? text.slice(0, 60);
      return [
        {
          name: "finance.record_entry",
          input: { type: isExpense ? "expense" : "income", amount, description },
        },
      ];
    }

    // Lucro
    if (/lucr\w+|quanto (eu )?(ganhei|lucrei|faturei)/.test(t) && has("finance.profit")) {
      return [{ name: "finance.profit", input: {} }];
    }

    // Fluxo de caixa
    if (/fluxo de caixa|caixa|saldo/.test(t) && has("finance.cashflow")) {
      return [{ name: "finance.cashflow", input: {} }];
    }

    // Listar clientes
    if (/(lista|listar|mostrar|meus)\s+.*clientes|clientes\b/.test(t) && has("crm.list_customers")) {
      return [{ name: "crm.list_customers", input: {} }];
    }

    return calls;
  }

  private extractEmail(text: string): string | undefined {
    return text.match(/[\w.+-]+@[\w-]+\.[\w.-]+/)?.[0];
  }

  /** Extrai um nome próprio simples (uma ou duas palavras capitalizadas). */
  private extractPersonName(text: string): string | undefined {
    const cleaned = text.replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, "");
    const match = cleaned.match(/\b([A-ZÀ-Ý][a-zà-ÿ]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ]+){0,2})\b/);
    return match?.[1];
  }

  private extractAfter(text: string, marker: RegExp): string | undefined {
    const idx = text.search(marker);
    if (idx < 0) return undefined;
    const rest = text.slice(idx).replace(marker, "").trim();
    // Pega até uma vírgula ou fim; limita tamanho.
    return rest.split(/[,.;\n]/)[0].trim().slice(0, 60) || undefined;
  }

  /** Interpreta valores em PT-BR: "R$ 4.200,50", "4.200", "4200,5", "4200". */
  private extractAmount(text: string): number | undefined {
    const m = text.match(/R?\$?\s*([\d.,]+)/);
    if (!m) return undefined;
    let raw = m[1];
    const hasDot = raw.includes(".");
    const hasComma = raw.includes(",");
    if (hasDot && hasComma) {
      raw = raw.replace(/\./g, "").replace(",", "."); // ponto = milhar, vírgula = decimal
    } else if (hasComma) {
      raw = raw.replace(",", ".");
    } else if (hasDot) {
      // "4.200" -> milhar; "4.2" improvável em BRL, tratamos ponto como milhar.
      raw = raw.replace(/\./g, "");
    }
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : undefined;
  }
}
