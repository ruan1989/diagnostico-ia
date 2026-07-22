// IA REAL: chama o Claude direto do navegador com a chave do PRÓPRIO usuário
// (guardada só no localStorage dele). Usa tool-use para executar as funções
// exatas do Company OS. Sem chave, o app usa o motor heurístico (engineChat).
//
// Nota de segurança: a chave é do usuário e nunca sai do navegador dele. Nós
// (o site) não temos acesso a ela. Em produção, o certo é um backend que
// guarda a chave num cofre — aqui é "traga sua chave" para a demo ser IA real.
import type { ChatResponse, ExecutedStep, Lang } from "@aicos/shared";
import { agentForToolName, executeTool, postActionNudge, TOOL_SPECS } from "./engine.js";

const AI_KEY = "companyos_anthropic_key";
const AI_MODEL = "companyos_anthropic_model";
const GOOGLE_KEY = "companyos_google_key";
const DEFAULT_MODEL = "claude-haiku-4-5-20251001"; // rápido e barato p/ roteamento

const ls = () => { try { return window.localStorage; } catch { return null; } };
export const hasAiKey = () => !!ls()?.getItem(AI_KEY);
export const setAiKey = (k: string) => ls()?.setItem(AI_KEY, k.trim());
export const clearAiKey = () => ls()?.removeItem(AI_KEY);
export const getAiModel = () => ls()?.getItem(AI_MODEL) || DEFAULT_MODEL;
export const setAiModel = (m: string) => ls()?.setItem(AI_MODEL, m.trim());
export const hasGoogleKey = () => !!ls()?.getItem(GOOGLE_KEY);
export const getGoogleKey = () => ls()?.getItem(GOOGLE_KEY) || undefined;
export const setGoogleKey = (k: string) => ls()?.setItem(GOOGLE_KEY, k.trim());
export const clearGoogleKey = () => ls()?.removeItem(GOOGLE_KEY);

interface Block { type: string; [k: string]: unknown }

const SYSTEM = (lang: Lang) =>
  `Você é o Company OS — o sistema operacional de uma empresa, operado por conversa. ` +
  `Interprete o pedido do usuário e EXECUTE usando as ferramentas disponíveis (não invente dados; ` +
  `use as ferramentas para ler/escrever). Seja objetivo e confirme com números reais. ` +
  (lang === "en" ? "Respond in English." : "Responda em português.");

/** Conversa usando o Claude real com tool-use, executando as funções do OS. */
export async function aiChat(message: string, lang: Lang): Promise<ChatResponse> {
  const key = ls()?.getItem(AI_KEY);
  if (!key) throw new Error("Sem chave de IA configurada.");
  const model = getAiModel();
  const tools = TOOL_SPECS.map((t) => ({ name: t.name, description: t.description, input_schema: t.input_schema }));
  const messages: Array<{ role: string; content: unknown }> = [{ role: "user", content: message }];
  const steps: ExecutedStep[] = [];
  const data: ChatResponse["data"] = [];
  let reply = "";

  for (let i = 0; i < 6; i++) {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "anthropic-dangerous-direct-browser-access": "true",
      },
      body: JSON.stringify({ model, max_tokens: 1024, system: SYSTEM(lang), tools, messages }),
    });
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(`Anthropic ${res.status}: ${txt.slice(0, 200)}`);
    }
    const json = (await res.json()) as { content: Block[]; stop_reason?: string };
    const blocks = json.content ?? [];
    reply = blocks.filter((b) => b.type === "text").map((b) => String(b.text)).join("\n") || reply;
    const toolUses = blocks.filter((b) => b.type === "tool_use");
    if (toolUses.length === 0) break;

    messages.push({ role: "assistant", content: blocks });
    const results: Block[] = [];
    for (const tu of toolUses) {
      const spec = TOOL_SPECS.find((s) => s.name === tu.name);
      const input = (tu.input as Record<string, unknown>) ?? {};
      let ok = false, summary = "Ferramenta desconhecida.";
      if (spec) {
        const r = executeTool(spec.tool, input, lang);
        ok = r.ok; summary = r.summary;
        if (r.data) data.push(r.data);
        steps.push({ agent: agentForToolName(spec.tool), tool: spec.tool, input, ok, summary });
      }
      results.push({ type: "tool_result", tool_use_id: tu.id, content: summary });
    }
    messages.push({ role: "user", content: results });
  }

  // Proatividade + automações (a IA age além do pedido).
  const { autoCreated, urgent } = postActionNudge(lang);
  if (autoCreated > 0) reply += lang === "en" ? `\n\n⚙️ Automation: created ${autoCreated} task(s).` : `\n\n⚙️ Automação: criei ${autoCreated} tarefa(s) para você.`;
  if (urgent) reply += (lang === "en" ? `\n\n💡 Heads-up: ${urgent.title}. ${urgent.message}` : `\n\n💡 Atenção: ${urgent.title}. ${urgent.message}`);
  if (!reply) reply = steps.length ? steps.map((s) => `• ${s.summary}`).join("\n") : "…";
  return { reply, steps, data };
}
