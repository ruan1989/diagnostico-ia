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

export interface Task { id: string; title: string; due: string; source: string; done: boolean; key: string }
export interface Prospect {
  id: string; name: string; category: string; location: string;
  phone?: string; website?: string; rating?: number; reviews?: number;
  score: number; reason: string; outreach: string; source: string;
}

interface State {
  customers: Customer[];
  proposals: Proposal[];
  entries: Entry[];
  leads: Lead[];
  memory: string[];
  tasks: Task[];
  prospects: Prospect[];
  automationsOn: boolean;
}

const KEY = "companyos_state_v1";
const EMPTY: State = { customers: [], proposals: [], entries: [], leads: [], memory: [], tasks: [], prospects: [], automationsOn: true };

function load(): State {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...EMPTY, ...(JSON.parse(raw) as Partial<State>) };
  } catch { /* ignore */ }
  return { ...EMPTY };
}

let state: State = load();
function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* ignore */ } }

export function resetState() {
  state = { ...EMPTY };
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

// ── Sales Autopilot: prospecção de leads (Google Places + fallback) ────────
const PROSPECT_ADJ = ["Central", "Prime", "Express", "Vila", "Aurora", "Nova", "Boa", "Real", "Bella", "Estrela", "Ponto", "Bom", "Mundo", "Casa", "Grande"];
const PROSPECT_SUFFIX = ["& Cia", "Ltda", "ME", "Group", "Hub", "Studio", "Center", "Place", "House", ""];

/** Rascunho de abordagem personalizado e COMPLIANT (opt-in, sem spam em massa). */
function draftOutreach(name: string, category: string): string {
  return `Olá, ${name}! Vi que vocês atuam com ${category}. Ajudamos negócios como o seu a organizar CRM, financeiro e atendimento numa tela só, com IA. Posso te enviar uma demonstração de 2 minutos? (responda apenas se fizer sentido)`;
}

/** Gera prospects realistas (fallback sem chave do Google). */
function generateProspects(businessType: string, location: string, n = 6): Prospect[] {
  const out: Prospect[] = [];
  for (let i = 0; i < n; i++) {
    const name = `${PROSPECT_ADJ[Math.floor(Math.random() * PROSPECT_ADJ.length)]} ${businessType} ${PROSPECT_SUFFIX[Math.floor(Math.random() * PROSPECT_SUFFIX.length)]}`.trim();
    const rating = Math.round((3.4 + Math.random() * 1.6) * 10) / 10;
    const reviews = Math.floor(20 + Math.random() * 400);
    const score = Math.min(98, Math.round(40 + rating * 8 + Math.min(reviews, 300) / 12));
    out.push({
      id: uid(), name, category: businessType, location,
      phone: `+55 11 9${Math.floor(1000 + Math.random() * 8999)}-${Math.floor(1000 + Math.random() * 8999)}`,
      website: `${name.toLowerCase().replace(/[^a-z]+/g, "")}.com.br`,
      rating, reviews, score,
      reason: `Nota ${rating} com ${reviews} avaliações — porte e reputação indicam orçamento para automatizar operações.`,
      outreach: draftOutreach(name, businessType), source: "amostra",
    });
  }
  return out.sort((a, b) => b.score - a.score);
}

/** Busca real no Google Places (Text Search) quando há chave. */
export async function placesSearch(businessType: string, location: string, apiKey: string): Promise<Prospect[]> {
  const res = await fetch("https://places.googleapis.com/v1/places:searchText", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "X-Goog-Api-Key": apiKey,
      "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.nationalPhoneNumber,places.websiteUri",
    },
    body: JSON.stringify({ textQuery: `${businessType} em ${location}`, languageCode: "pt-BR", maxResultCount: 10 }),
  });
  if (!res.ok) throw new Error(`Google Places ${res.status}: ${await res.text()}`);
  const json = (await res.json()) as { places?: Array<Record<string, unknown>> };
  return (json.places ?? []).map((p) => {
    const name = ((p.displayName as { text?: string })?.text) ?? "Empresa";
    const rating = (p.rating as number) ?? 0;
    const reviews = (p.userRatingCount as number) ?? 0;
    const score = Math.min(98, Math.round(40 + rating * 8 + Math.min(reviews, 300) / 12));
    return {
      id: uid(), name, category: businessType, location: (p.formattedAddress as string) ?? location,
      phone: (p.nationalPhoneNumber as string) ?? undefined, website: (p.websiteUri as string) ?? undefined,
      rating, reviews, score,
      reason: rating ? `Nota ${rating} com ${reviews} avaliações — reputação e volume indicam potencial de compra.` : "Empresa encontrada no Google — potencial a qualificar.",
      outreach: draftOutreach(name, businessType), source: "google",
    } as Prospect;
  }).sort((a, b) => b.score - a.score);
}

/** Prospecção com fallback: usa Google se houver chave, senão amostra realista. */
export async function runProspecting(businessType: string, location: string, googleKey?: string): Promise<Prospect[]> {
  let list: Prospect[];
  try {
    list = googleKey ? await placesSearch(businessType, location, googleKey) : generateProspects(businessType, location);
  } catch {
    list = generateProspects(businessType, location);
  }
  state.prospects = list; save();
  return list;
}

export function importProspectAsLead(id: string): Lead | undefined {
  const p = state.prospects.find((x) => x.id === id);
  if (!p) return;
  const lead: Lead = {
    id: uid(), name: p.name, contact: p.phone ?? p.website ?? "", need: `Prospecção ${p.category} — ${p.reason}`,
    area: "sales", stage: p.score >= 60 ? "qualified" : "new", score: p.score, createdAt: new Date().toISOString(),
  };
  state.leads.push(lead); save();
  return lead;
}
export function getProspects(): Prospect[] { return state.prospects; }

function tool(name: string, input: Record<string, unknown>, lang: Lang = "pt"): ToolResult {
  switch (name) {
    case "sales.find_leads": {
      const type = String(input.businessType ?? input.query ?? "empresas");
      const location = String(input.location ?? "Brasil");
      const list = generateProspects(type, location);
      state.prospects = list; save();
      return {
        ok: true,
        summary: `Encontrei ${list.length} potenciais clientes de "${type}" em ${location} (amostra). Abra o Sales Autopilot para ver, pontuar e importar. Com sua chave do Google, a busca é real.`,
        data: { kind: "prospects", title: "Sales Autopilot — leads encontrados", payload: list },
      };
    }
    case "insights.review": {
      const list = proactiveInsights(lang);
      const top = list.slice(0, 3).map((i) => `• ${i.title}: ${i.message}`).join("\n");
      return { ok: true, summary: (lang === "en" ? "Proactive review:\n" : "Análise proativa:\n") + top, data: { kind: "insights", title: lang === "en" ? "Proactive Intelligence" : "Inteligência Proativa", payload: list } };
    }
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

// ── Inteligência Proativa: pensa à frente do cliente ───────────────────────
export interface Insight {
  id: string;
  severity: "critical" | "warn" | "info" | "success";
  title: string;
  message: string;
  suggestion?: string; // comando pronto para o usuário executar por 1 toque
}

const SEV_ORDER: Record<Insight["severity"], number> = { critical: 0, warn: 1, info: 2, success: 3 };
const brl = (n: number) => `R$ ${n.toLocaleString("pt-BR")}`;

export function proactiveInsights(lang: Lang = "pt"): Insight[] {
  const en = lang === "en";
  const out: Insight[] = [];
  const now = new Date();
  const inMonth = (iso: string) => { const d = new Date(iso); return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear(); };
  const income = state.entries.filter((e) => e.type === "income" && inMonth(e.date)).reduce((s, e) => s + e.amount, 0);
  const expense = state.entries.filter((e) => e.type === "expense" && inMonth(e.date)).reduce((s, e) => s + e.amount, 0);

  // 1. Caixa negativo (crítico)
  if (expense > income && expense > 0) {
    out.push({ id: "cash_negative", severity: "critical",
      title: en ? "Expenses above revenue" : "Despesas acima da receita",
      message: en ? `This month expenses (${brl(expense)}) exceed revenue (${brl(income)}). Review costs.` : `Neste mês as despesas (${brl(expense)}) superam a receita (${brl(income)}). Reveja os custos.`,
      suggestion: en ? "show cash flow" : "qual meu fluxo de caixa?" });
  }

  // 2. Leads quentes sem proposta
  const proposalNames = new Set(state.proposals.map((p) => p.customerName.toLowerCase()));
  const hotNoProposal = state.leads.filter((l) => l.score >= 60 && !proposalNames.has(l.name.toLowerCase()));
  if (hotNoProposal.length) {
    const lead = hotNoProposal[0];
    out.push({ id: "hot_lead_no_proposal", severity: "warn",
      title: en ? `${hotNoProposal.length} hot lead(s) without a proposal` : `${hotNoProposal.length} lead(s) quente(s) sem proposta`,
      message: en ? `"${lead.name}" (score ${lead.score}) is qualified but has no proposal. Send one before it cools down.` : `"${lead.name}" (score ${lead.score}) está qualificado mas sem proposta. Envie uma antes de esfriar.`,
      suggestion: en ? `create a proposal of 2000 for ${lead.name}` : `crie uma proposta de 2000 para ${lead.name}` });
  }

  // 3. Propostas aguardando follow-up
  const pending = state.proposals.filter((p) => p.status === "sent");
  if (pending.length) {
    out.push({ id: "proposals_pending", severity: "warn",
      title: en ? `${pending.length} proposal(s) awaiting a reply` : `${pending.length} proposta(s) aguardando resposta`,
      message: en ? `Total ${brl(pending.reduce((s, p) => s + p.amount, 0))} in the pipeline. Follow up to close.` : `Total de ${brl(pending.reduce((s, p) => s + p.amount, 0))} no funil. Faça follow-up para fechar.`,
      suggestion: en ? "list customers" : "liste meus clientes" });
  }

  // 4. Clientes sem receita registrada
  if (state.customers.length > 0 && income === 0) {
    out.push({ id: "customers_no_revenue", severity: "info",
      title: en ? "Customers but no revenue yet" : "Clientes, mas sem receita ainda",
      message: en ? "You have customers but no income recorded this month. Record your sales to track profit." : "Você tem clientes mas nenhuma receita lançada no mês. Registre suas vendas para acompanhar o lucro.",
      suggestion: en ? "record income of 3000 from a sale" : "lance uma receita de 3000 de uma venda" });
  }

  // 5. Cadastros incompletos (sem email → bloqueia automações)
  const noEmail = state.customers.filter((c) => !c.email);
  if (noEmail.length) {
    out.push({ id: "customers_no_email", severity: "info",
      title: en ? `${noEmail.length} customer(s) without email` : `${noEmail.length} cliente(s) sem email`,
      message: en ? "Complete their contact info to enable follow-up automations." : "Complete o contato para habilitar automações de follow-up." });
  }

  // 6. Projeção de fechamento do mês (pensa à frente)
  if (income > 0) {
    const day = now.getDate();
    const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
    const projected = Math.round((income / day) * daysInMonth);
    out.push({ id: "forecast", severity: "info",
      title: en ? "Month-end revenue forecast" : "Projeção de receita do mês",
      message: en ? `At the current pace, you should close the month around ${brl(projected)}.` : `No ritmo atual, você deve fechar o mês em torno de ${brl(projected)}.` });
  }

  // 7. Concentração de receita (risco) — via propostas
  if (state.proposals.length >= 2) {
    const total = state.proposals.reduce((s, p) => s + p.amount, 0);
    const top = Math.max(...state.proposals.map((p) => p.amount));
    if (total > 0 && top / total > 0.6) {
      out.push({ id: "revenue_concentration", severity: "warn",
        title: en ? "Revenue concentration risk" : "Risco de concentração de receita",
        message: en ? "One deal represents most of your pipeline. Diversify to reduce risk." : "Uma única proposta representa a maior parte do funil. Diversifique para reduzir risco." });
    }
  }

  if (out.length === 0) {
    out.push({ id: "all_good", severity: "success",
      title: en ? "All under control" : "Tudo sob controle",
      message: en ? "No risks detected. Start by registering a customer or recording a sale." : "Nenhum risco detectado. Comece cadastrando um cliente ou lançando uma venda." });
  }
  return out.sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity]);
}

// ── Motor de Automação: a IA EXECUTA procedimentos sozinha ─────────────────
function addTask(key: string, title: string, dueDays: number): boolean {
  if (!state.automationsOn) return false;
  if (state.tasks.some((t) => t.key === key)) return false;
  state.tasks.push({ id: uid(), key, title, due: new Date(Date.now() + dueDays * 864e5).toISOString(), source: "automação", done: false });
  return true;
}
/** Após qualquer ação, cria automaticamente as tarefas/follow-ups necessários. */
export function runAutomations(): number {
  if (!state.automationsOn) return 0;
  let n = 0;
  for (const p of state.proposals.filter((p) => p.status === "sent"))
    if (addTask(`followup_${p.id}`, `Follow-up com ${p.customerName} — proposta de ${brl(p.amount)}`, 3)) n++;
  const names = new Set(state.proposals.map((p) => p.customerName.toLowerCase()));
  for (const l of state.leads.filter((l) => l.score >= 60 && !names.has(l.name.toLowerCase())))
    if (addTask(`proposal_${l.id}`, `Enviar proposta para ${l.name} (lead quente, score ${l.score})`, 1)) n++;
  const now = new Date();
  const inMonth = (iso: string) => { const d = new Date(iso); return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear(); };
  const inc = state.entries.filter((e) => e.type === "income" && inMonth(e.date)).reduce((s, e) => s + e.amount, 0);
  const exp = state.entries.filter((e) => e.type === "expense" && inMonth(e.date)).reduce((s, e) => s + e.amount, 0);
  if (exp > inc && exp > 0) if (addTask("review_costs", "Revisar custos — caixa negativo este mês", 1)) n++;
  if (n) save();
  return n;
}
export function getTasks(): Task[] { return state.tasks.filter((t) => !t.done).sort((a, b) => a.due.localeCompare(b.due)); }
export function setTaskDone(id: string) { const t = state.tasks.find((x) => x.id === id); if (t) { t.done = true; save(); } }
export function getAutomationsOn(): boolean { return state.automationsOn; }
export function setAutomationsOn(on: boolean) { state.automationsOn = on; save(); }

// ── Planejador com comandos compostos ("faça A e B e C" numa frase só) ─────
function plan(text: string): Array<{ name: string; input: Record<string, unknown> }> {
  // Divide em orações por conectores (não por vírgula, que faz parte de 1 comando).
  const clauses = text.split(/\s+(?:e|and|então|then|depois)\s+|;/i).map((s) => s.trim()).filter(Boolean);
  const parts = clauses.length > 1 ? clauses : [text];
  const calls: Array<{ name: string; input: Record<string, unknown> }> = [];
  const seen = new Set<string>();
  for (const part of parts) {
    for (const c of planClause(part)) {
      const k = c.name + JSON.stringify(c.input);
      if (!seen.has(k)) { seen.add(k); calls.push(c); }
    }
  }
  return calls.length ? calls : planClause(text);
}

function planClause(text: string): Array<{ name: string; input: Record<string, unknown> }> {
  const t = text.toLowerCase();
  if (/encontr|prospect|busq|ache clientes|gere leads|find (me )?(leads|customers|clients|prospects)|new customers|novos clientes/.test(t)) {
    const location = extractAfter(text, /\b(em|in|na cidade de|na|no)\s+/i) ?? "Brasil";
    const businessType = text
      .replace(/.*?(encontr\w*|prospect\w*|busq\w*|ache|find(\sme)?|gere leads|novos clientes de)\s*/i, "")
      .replace(/\b(em|in|na|no)\s+.*/i, "").replace(/clientes|leads|potenciais|de/gi, "").trim() || "empresas";
    return [{ name: "sales.find_leads", input: { businessType, location } }];
  }
  if (/análise|analise|insight|o que (eu )?(devo|faço|fazer)|sugest|revis|diagnóstic|diagnostic|what should i|review|analy[sz]e/.test(t))
    return [{ name: "insights.review", input: {} }];
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
    const res = tool(call.name, call.input, lang);
    steps.push({ agent: agentFor(call.name), tool: call.name, input: call.input, ok: res.ok, summary: res.summary });
    if (res.data) data.push(res.data);
    if (res.ok) { state.memory.push(res.summary); save(); }
  }
  let reply: string;
  if (steps.length === 0) {
    reply = lang === "en"
      ? "Got it. I can register customers, create proposals, record income/expenses, show profit and cash flow, qualify leads, convene the agent council, or run a proactive review (ask \"what should I do?\"). What would you like?"
      : "Entendi. Posso cadastrar clientes, criar propostas, lançar receitas/despesas, mostrar lucro e fluxo de caixa, qualificar leads, reunir o conselho ou fazer uma análise proativa (pergunte \"o que devo fazer?\"). O que deseja?";
  } else {
    const head = lang === "en" ? "Done. Here's what I did:" : "Pronto. Aqui está o que fiz:";
    reply = `${head}\n${steps.map((s) => `• ${s.summary}`).join("\n")}`;
    // Automação: a IA executa procedimentos (cria follow-ups/tarefas) sozinha.
    const autoCreated = runAutomations();
    if (autoCreated > 0)
      reply += lang === "en"
        ? `\n\n⚙️ Automation: created ${autoCreated} task(s) for you.`
        : `\n\n⚙️ Automação: criei ${autoCreated} tarefa(s) automática(s) para você.`;
    // Proatividade: após agir, antecipa o próximo passo mais urgente.
    const didReview = calls.some((c) => c.name === "insights.review");
    if (!didReview) {
      const urgent = proactiveInsights(lang).find((i) => i.severity === "critical" || i.severity === "warn");
      if (urgent) reply += (lang === "en" ? `\n\n💡 Heads-up: ${urgent.title}. ${urgent.message}` : `\n\n💡 Atenção: ${urgent.title}. ${urgent.message}`);
    }
  }
  return { reply, steps, data };
}

export function engineHealth() {
  return { status: "ok", llm: "browser (mock)", tools: 10, baseCurrency: "BRL" as Currency, payoutCurrency: "BRL" as Currency };
}

// ── Interface para a IA REAL (Claude tool-use no navegador) ────────────────
export type ToolResultPublic = ToolResult;
export function executeTool(internalName: string, input: Record<string, unknown>, lang: Lang = "pt"): ToolResult {
  return tool(internalName, input, lang);
}
export function agentForToolName(name: string): string { return agentFor(name); }
/** Após ações do LLM, roda automações + devolve o insight urgente (proatividade). */
export function postActionNudge(lang: Lang): { autoCreated: number; urgent?: Insight } {
  const autoCreated = runAutomations();
  const urgent = proactiveInsights(lang).find((i) => i.severity === "critical" || i.severity === "warn");
  return { autoCreated, urgent };
}

/** Definições das ferramentas para o Claude (nomes sem ponto p/ a API). */
export const TOOL_SPECS: Array<{ name: string; tool: string; description: string; input_schema: Record<string, unknown> }> = [
  { name: "crm_create_customer", tool: "crm.create_customer", description: "Cadastra um cliente/lead no CRM.", input_schema: { type: "object", properties: { name: { type: "string" }, email: { type: "string" } }, required: ["name"] } },
  { name: "crm_list_customers", tool: "crm.list_customers", description: "Lista os clientes do CRM.", input_schema: { type: "object", properties: {} } },
  { name: "crm_create_proposal", tool: "crm.create_proposal", description: "Cria e envia uma proposta para um cliente.", input_schema: { type: "object", properties: { customer: { type: "string" }, amount: { type: "number" } }, required: ["customer", "amount"] } },
  { name: "finance_record_entry", tool: "finance.record_entry", description: "Registra receita ou despesa.", input_schema: { type: "object", properties: { type: { type: "string", enum: ["income", "expense"] }, amount: { type: "number" }, description: { type: "string" } }, required: ["type", "amount"] } },
  { name: "finance_profit", tool: "finance.profit", description: "Lucro do mês (receitas - despesas).", input_schema: { type: "object", properties: {} } },
  { name: "finance_cashflow", tool: "finance.cashflow", description: "Fluxo de caixa acumulado.", input_schema: { type: "object", properties: {} } },
  { name: "funnel_qualify", tool: "funnel.qualify", description: "Qualifica um lead e roteia para a área.", input_schema: { type: "object", properties: { name: { type: "string" }, contact: { type: "string" }, need: { type: "string" } }, required: ["name", "need"] } },
  { name: "sales_find_leads", tool: "sales.find_leads", description: "Prospecta potenciais clientes por tipo de negócio e cidade (Sales Autopilot).", input_schema: { type: "object", properties: { businessType: { type: "string" }, location: { type: "string" } }, required: ["businessType"] } },
  { name: "insights_review", tool: "insights.review", description: "Análise proativa: antecipa riscos e próximos passos.", input_schema: { type: "object", properties: {} } },
  { name: "council_deliberate", tool: "council.deliberate", description: "Reúne o conselho de agentes para decidir um tema.", input_schema: { type: "object", properties: { topic: { type: "string" } }, required: ["topic"] } },
];
