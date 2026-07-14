// ─────────────────────────────────────────────────────────────────────────
// Motor client-side do Company OS.
// Roda TODO o núcleo (IA mock bilíngue, agentes, CRM, Financeiro, Funil,
// Conselho e cotação de pagamento) dentro do navegador, com persistência em
// localStorage. É o que torna o site publicado 100% funcional sem backend.
// Espelha fielmente a lógica do apps/api em modo mock.
// ─────────────────────────────────────────────────────────────────────────
import type { ChatResponse, Currency, ExecutedStep, Lang } from "@aicos/shared";

const uid = (): string =>
  (globalThis.crypto?.randomUUID?.() ?? `id_${Math.random().toString(36).slice(2)}`);

// ── Estado persistente ─────────────────────────────────────────────────────
interface Customer { id: string; name: string; email: string; status: string; createdAt: string }
interface Proposal { id: string; customerId: string; customerName: string; amount: number; currency: string; status: string; createdAt: string }
interface Entry { id: string; type: "income" | "expense"; amount: number; currency: string; description: string; date: string }
interface Lead { id: string; name: string; contact: string; need: string; area: string; stage: string; score: number; createdAt: string }

interface State {
  customers: Customer[];
  proposals: Proposal[];
  entries: Entry[];
  leads: Lead[];
  memory: string[];
}

const KEY = "companyos_state_v1";

function load(): State {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as State;
  } catch { /* ignore */ }
  return { customers: [], proposals: [], entries: [], leads: [], memory: [] };
}

let state: State = load();
function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* ignore */ } }

export function resetState() {
  state = { customers: [], proposals: [], entries: [], leads: [], memory: [] };
  save();
}

// ── Agentes ────────────────────────────────────────────────────────────────
const AGENTS = [
  { id: "ceo", name: "CEO AI", title: "Diretor Executivo", goals: ["Maximizar valor", "Coordenar agentes"], prefixes: ["council."] },
  { id: "finance", name: "Finance AI", title: "Diretor Financeiro", goals: ["Proteger o caixa", "Maximizar lucro"], prefixes: ["finance."] },
  { id: "sales", name: "Sales AI", title: "Gerente Comercial", goals: ["Aumentar conversão", "Cuidar do funil"], prefixes: ["crm.", "funnel."] },
  { id: "marketing", name: "Marketing AI", title: "Analista de Marketing", goals: ["Gerar demanda", "Reduzir CAC"], prefixes: ["marketing."] },
  { id: "hr", name: "HR AI", title: "Gerente de RH", goals: ["Contratar bem", "Reter talentos"], prefixes: ["hr."] },
  { id: "analytics", name: "Analytics AI", title: "Analista de BI", goals: ["Explicar números", "Achar padrões"], prefixes: ["bi."] },
];
function agentFor(tool: string): string {
  return AGENTS.find((a) => a.prefixes.some((p) => tool.startsWith(p)))?.name ?? "Company AI";
}

// ── Utilidades de parsing (PT/EN) ──────────────────────────────────────────
function extractEmail(t: string) { return t.match(/[\w.+-]+@[\w-]+\.[\w.-]+/)?.[0]; }
function extractName(t: string) {
  const c = t.replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, "");
  return c.match(/\b([A-ZÀ-Ý][a-zà-ÿ]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ]+){0,2})\b/)?.[1];
}
function extractAfter(t: string, marker: RegExp) {
  const i = t.search(marker); if (i < 0) return undefined;
  return t.slice(i).replace(marker, "").trim().split(/[,.;\n]/)[0].trim().slice(0, 60) || undefined;
}
function extractAmount(t: string): number | undefined {
  const m = t.match(/R?\$?\s*([\d.,]+)/); if (!m) return undefined;
  let raw = m[1]; const dot = raw.includes("."), comma = raw.includes(",");
  if (dot && comma) raw = raw.replace(/\./g, "").replace(",", ".");
  else if (comma) raw = raw.replace(",", ".");
  else if (dot) raw = raw.replace(/\./g, "");
  const v = Number(raw); return Number.isFinite(v) && v > 0 ? v : undefined;
}

// ── Ferramentas (executores) ───────────────────────────────────────────────
type ToolResult = { ok: boolean; summary: string; data?: { kind: string; title: string; payload: unknown } };

function findCustomer(name: string) {
  return state.customers.find((c) => c.name.toLowerCase().includes(name.toLowerCase()));
}
function createCustomer(name: string, email: string): Customer {
  const existing = state.customers.find((c) => c.name.toLowerCase() === name.toLowerCase());
  if (existing) return existing;
  const c: Customer = { id: uid(), name, email, status: "lead", createdAt: new Date().toISOString() };
  state.customers.push(c); save(); return c;
}

const AREA_KEYWORDS: Array<[string, RegExp]> = [
  ["sales", /(preç|preco|orçament|orcament|compr|contrat|plano|vend|demo|price|pricing|quote|buy|purchase|subscribe)/],
  ["support", /(erro|bug|problema|não funciona|nao funciona|ajuda|suporte|support|help|issue|broken)/],
  ["finance", /(fatura|cobrança|cobranca|reembolso|nota fiscal|invoice|billing|refund)/],
  ["hr", /(vaga|currículo|curriculo|emprego|trabalhar|carreira|job|career|hiring|resume)/],
  ["marketing", /(parceria|imprensa|afiliad|partnership|press|affiliate|collab)/],
];
function classifyArea(t: string): string {
  const s = t.toLowerCase();
  for (const [area, re] of AREA_KEYWORDS) if (re.test(s)) return area;
  return "general";
}
function scoreLead(t: string): number {
  const s = t.toLowerCase(); let n = 40;
  if (/(preç|orçament|plano|budget|price|pricing|quote|comprar|buy)/.test(s)) n += 20;
  if (/(hoje|urgent|agora|asap|now|esta semana|this week)/.test(s)) n += 15;
  if (/(empresa|equipe|funcionári|time|company|team|employees)/.test(s)) n += 15;
  return Math.min(100, n);
}

function tool(name: string, input: Record<string, unknown>): ToolResult {
  switch (name) {
    case "crm.create_customer": {
      const c = createCustomer(String(input.name ?? "Novo Cliente"), String(input.email ?? ""));
      return { ok: true, summary: `Cliente "${c.name}" cadastrado no CRM${c.email ? ` (${c.email})` : ""}.`, data: { kind: "customer", title: "Cliente cadastrado", payload: c } };
    }
    case "crm.list_customers":
      return { ok: true, summary: `Você tem ${state.customers.length} cliente(s) no CRM.`, data: { kind: "table", title: "Clientes", payload: state.customers } };
    case "crm.create_proposal": {
      let cust = findCustomer(String(input.customer ?? ""));
      if (!cust) cust = createCustomer(String(input.customer ?? "Cliente"), "");
      const p: Proposal = { id: uid(), customerId: cust.id, customerName: cust.name, amount: Number(input.amount ?? 0), currency: "BRL", status: "sent", createdAt: new Date().toISOString() };
      state.proposals.push(p); save();
      return { ok: true, summary: `Proposta de R$ ${p.amount.toLocaleString("pt-BR")} enviada para ${p.customerName}.`, data: { kind: "proposal", title: "Proposta enviada", payload: p } };
    }
    case "finance.record_entry": {
      const type = input.type === "expense" ? "expense" : "income";
      const e: Entry = { id: uid(), type, amount: Number(input.amount ?? 0), currency: "BRL", description: String(input.description ?? ""), date: new Date().toISOString() };
      state.entries.push(e); save();
      const label = type === "income" ? "Receita" : "Despesa";
      return { ok: true, summary: `${label} de R$ ${e.amount.toLocaleString("pt-BR")} registrada${e.description ? ` (${e.description})` : ""}.`, data: { kind: "entry", title: `${label} registrada`, payload: e } };
    }
    case "finance.profit": {
      const now = new Date();
      const month = (e: Entry) => { const d = new Date(e.date); return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear(); };
      const income = state.entries.filter((e) => e.type === "income" && month(e)).reduce((s, e) => s + e.amount, 0);
      const expense = state.entries.filter((e) => e.type === "expense" && month(e)).reduce((s, e) => s + e.amount, 0);
      const payload = { income, expense, profit: income - expense };
      return { ok: true, summary: `Neste mês: receita R$ ${income.toLocaleString("pt-BR")}, despesa R$ ${expense.toLocaleString("pt-BR")}, lucro R$ ${(income - expense).toLocaleString("pt-BR")}.`, data: { kind: "kpi", title: "Lucro do mês", payload } };
    }
    case "finance.cashflow": {
      const income = state.entries.filter((e) => e.type === "income").reduce((s, e) => s + e.amount, 0);
      const expense = state.entries.filter((e) => e.type === "expense").reduce((s, e) => s + e.amount, 0);
      const payload = { balance: income - expense, income, expense };
      return { ok: true, summary: `Saldo em caixa: R$ ${(income - expense).toLocaleString("pt-BR")} (receitas R$ ${income.toLocaleString("pt-BR")}, despesas R$ ${expense.toLocaleString("pt-BR")}).`, data: { kind: "kpi", title: "Fluxo de caixa", payload } };
    }
    case "funnel.qualify": {
      const need = String(input.need ?? "");
      const area = classifyArea(need), score = scoreLead(need);
      const lead: Lead = { id: uid(), name: String(input.name ?? "Prospecto"), contact: String(input.contact ?? ""), need, area, stage: score >= 60 ? "qualified" : "new", score, createdAt: new Date().toISOString() };
      state.leads.push(lead); save();
      return { ok: true, summary: `Lead "${lead.name}" roteado para ${agentFor("funnel.")} (área: ${area}, score ${score}, estágio ${lead.stage}).`, data: { kind: "lead", title: "Lead qualificado", payload: lead } };
    }
    case "funnel.pipeline": {
      const base: Record<string, number> = { new: 0, qualified: 0, proposal: 0, won: 0, lost: 0 };
      for (const l of state.leads) base[l.stage] = (base[l.stage] ?? 0) + 1;
      return { ok: true, summary: `Funil: ${base.new} novos, ${base.qualified} qualificados, ${base.proposal} em proposta.`, data: { kind: "pipeline", title: "Funil de vendas", payload: base } };
    }
    case "council.deliberate": {
      const topic = String(input.topic ?? "");
      const opinions = AGENTS.filter((a) => a.id !== "ceo").map((a) => ({
        agent: a.name, title: a.title,
        argument: `Do ponto de vista de ${a.title.toLowerCase()}, avalio "${topic}" à luz de: ${a.goals.join(" e ").toLowerCase()}.`,
        vote: a.id === "finance" && /contrat|invest|gast|compr/.test(topic.toLowerCase()) ? "cauteloso" : "a favor",
      }));
      const favor = opinions.filter((o) => o.vote === "a favor").length;
      const decision = favor > opinions.length / 2 ? "Prosseguir — com acompanhamento das métricas." : "Não prosseguir agora — reduzir risco antes.";
      const payload = { topic, opinions, decision, rationale: `O CEO AI consolidou ${opinions.length} pareceres (${favor} favoráveis).` };
      return { ok: true, summary: `Conselho decidiu: ${decision}`, data: { kind: "deliberation", title: "Conselho de Agentes", payload } };
    }
    default:
      return { ok: false, summary: `Ferramenta desconhecida: ${name}` };
  }
}

// ── Planejador (interpreta linguagem natural → chamadas de ferramenta) ──────
function plan(text: string): Array<{ name: string; input: Record<string, unknown> }> {
  const t = text.toLowerCase();
  if (/\bconselho\b|delibere|debata|\bcouncil\b|deliberate/.test(t))
    return [{ name: "council.deliberate", input: { topic: text.replace(/.*(conselho|council)[:,]?\s*/i, "").trim() || text } }];
  if (/qualifi|lead|prospect/.test(t))
    return [{ name: "funnel.qualify", input: { name: extractName(text) ?? "Prospecto", need: text } }];
  if (/funil|pipeline/.test(t)) return [{ name: "funnel.pipeline", input: {} }];
  if (/cadastr\w+|nov[oa] cliente|adicion\w+ cliente|register|add customer|new customer|create.*customer/.test(t))
    return [{ name: "crm.create_customer", input: { name: extractName(text) ?? "Novo Cliente", email: extractEmail(text) ?? "" } }];
  if (/proposta|orçament|orcament|proposal|quote/.test(t))
    return [{ name: "crm.create_proposal", input: { customer: extractAfter(text, /(para|for|to)\s+/i) ?? extractName(text) ?? "", amount: extractAmount(text) ?? 0 } }];
  if (/lanc\w+|lança|registr\w+|receb\w+|paguei|gast\w+|despesa|receita|record|income|revenue|expense|paid|spent/.test(t)) {
    const isExpense = /despesa|paguei|gast\w+|pagar|expense|paid|spent|cost/.test(t) && !/receita|receb|income|revenue/.test(t);
    return [{ name: "finance.record_entry", input: { type: isExpense ? "expense" : "income", amount: extractAmount(text) ?? 0, description: extractAfter(text, /(de|da|do|com|from|for|with)\s+/i) ?? text.slice(0, 60) } }];
  }
  if (/lucr\w+|profit|ganhei|faturei|earn/.test(t)) return [{ name: "finance.profit", input: {} }];
  if (/fluxo de caixa|caixa|saldo|cash ?flow|balance/.test(t)) return [{ name: "finance.cashflow", input: {} }];
  if (/(lista|listar|mostrar|meus)\s+.*clientes|clientes\b|list.*customers|customers\b/.test(t)) return [{ name: "crm.list_customers", input: {} }];
  return [];
}

// ── API pública do motor ───────────────────────────────────────────────────
export function engineChat(message: string, lang: Lang): ChatResponse {
  const calls = plan(message);
  const steps: ExecutedStep[] = [];
  const data: ChatResponse["data"] = [];
  for (const call of calls) {
    const res = tool(call.name, call.input);
    steps.push({ agent: agentFor(call.name), tool: call.name, input: call.input, ok: res.ok, summary: res.summary });
    if (res.data) data.push(res.data);
    if (res.ok) { state.memory.push(res.summary); save(); }
  }
  let reply: string;
  if (steps.length === 0) {
    reply = lang === "en"
      ? "Got it. I can register customers, create proposals, record income/expenses, show your profit and cash flow, qualify leads, or convene the agent council. What would you like?"
      : "Entendi. Posso cadastrar clientes, criar propostas, lançar receitas/despesas, mostrar lucro e fluxo de caixa, qualificar leads ou reunir o conselho de agentes. O que deseja?";
  } else {
    const head = lang === "en" ? "Done. Here's what I did:" : "Pronto. Aqui está o que fiz:";
    reply = `${head}\n${steps.map((s) => `• ${s.summary}`).join("\n")}`;
  }
  return { reply, steps, data };
}

export function engineHealth() {
  return { status: "ok", llm: "browser (mock)", tools: 9, baseCurrency: "BRL" as Currency, payoutCurrency: "BRL" as Currency };
}
