import type {
  CompletionRequest,
  CompletionResult,
  LlmMessage,
  LlmProvider,
  LlmToolCall,
} from "../types.js";

/** Provedor OpenAI via Chat Completions com function calling. */
export class OpenAiProvider implements LlmProvider {
  readonly name = "openai";

  constructor(
    private readonly apiKey: string,
    private readonly model = "gpt-4o",
  ) {}

  async complete(req: CompletionRequest): Promise<CompletionResult> {
    const body = {
      model: this.model,
      temperature: req.temperature ?? 0.2,
      messages: [
        { role: "system", content: req.system },
        ...req.messages.map(mapMessage),
      ],
      tools: req.tools.map((t) => ({
        type: "function",
        function: {
          name: t.name,
          description: t.description,
          parameters: {
            type: "object",
            properties: Object.fromEntries(
              Object.entries(t.params).map(([k, p]) => [k, { type: p.type, description: p.description }]),
            ),
            required: Object.entries(t.params)
              .filter(([, p]) => p.required)
              .map(([k]) => k),
          },
        },
      })),
    };

    const res = await fetch("https://api.openai.com/v1/chat/completions", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${this.apiKey}`,
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new Error(`OpenAI API ${res.status}: ${await res.text()}`);
    }
    const json = (await res.json()) as {
      choices: Array<{ message: { content?: string; tool_calls?: Array<{ function: { name: string; arguments: string } }> } }>;
    };
    const message = json.choices?.[0]?.message;
    const toolCalls: LlmToolCall[] = (message?.tool_calls ?? []).map((c) => ({
      name: c.function.name,
      input: safeParse(c.function.arguments),
    }));
    return { text: message?.content ?? "", toolCalls };
  }
}

function mapMessage(m: LlmMessage): { role: string; content: string } {
  if (m.role === "tool") {
    return { role: "user", content: `[resultado de ${m.toolName}]: ${m.content}` };
  }
  return { role: m.role, content: m.content };
}

function safeParse(raw: string): Record<string, unknown> {
  try {
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return {};
  }
}
