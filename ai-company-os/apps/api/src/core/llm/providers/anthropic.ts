import type {
  CompletionRequest,
  CompletionResult,
  LlmMessage,
  LlmProvider,
  LlmToolCall,
} from "../types.js";

/** Provedor Anthropic (Claude) via Messages API com tool use nativo. */
export class AnthropicProvider implements LlmProvider {
  readonly name = "anthropic";

  constructor(
    private readonly apiKey: string,
    private readonly model = "claude-opus-4-8",
  ) {}

  async complete(req: CompletionRequest): Promise<CompletionResult> {
    const body = {
      model: this.model,
      max_tokens: 1024,
      system: req.system,
      temperature: req.temperature ?? 0.2,
      tools: req.tools.map((t) => ({
        name: t.name,
        description: t.description,
        input_schema: {
          type: "object",
          properties: Object.fromEntries(
            Object.entries(t.params).map(([k, p]) => [k, { type: p.type, description: p.description }]),
          ),
          required: Object.entries(t.params)
            .filter(([, p]) => p.required)
            .map(([k]) => k),
        },
      })),
      messages: mapMessages(req.messages),
    };

    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": this.apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new Error(`Anthropic API ${res.status}: ${await res.text()}`);
    }
    const json = (await res.json()) as { content: Array<Record<string, unknown>> };
    const toolCalls: LlmToolCall[] = [];
    let text = "";
    for (const block of json.content ?? []) {
      if (block.type === "text") text += String(block.text ?? "");
      if (block.type === "tool_use") {
        toolCalls.push({ name: String(block.name), input: (block.input as Record<string, unknown>) ?? {} });
      }
    }
    return { text, toolCalls };
  }
}

function mapMessages(messages: LlmMessage[]): Array<{ role: string; content: string }> {
  return messages.map((m) => {
    if (m.role === "tool") {
      return { role: "user", content: `[resultado de ${m.toolName}]: ${m.content}` };
    }
    return { role: m.role, content: m.content };
  });
}
