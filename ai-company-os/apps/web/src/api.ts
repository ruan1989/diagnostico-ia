import type { ChatResponse } from "@aicos/shared";

const BASE = "/api";

export async function sendChat(message: string, token?: string): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { message?: string };
    throw new Error(err.message ?? `Erro ${res.status}`);
  }
  return (await res.json()) as ChatResponse;
}

export async function health(): Promise<{ status: string; llm: string; tools: number }> {
  const res = await fetch(`${BASE}/health`);
  return (await res.json()) as { status: string; llm: string; tools: number };
}
