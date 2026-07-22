import type { Currency } from "@aicos/shared";
import { loadEnv, type Env } from "./config/env.js";
import { Council } from "./core/agents/council.js";
import { AgentRegistry } from "./core/agents/registry.js";
import { Orchestrator } from "./core/ai-core/orchestrator.js";
import { ProactiveEngine } from "./core/proactive/proactive.js";
import { LlmRouter } from "./core/llm/router.js";
import { MemoryStore } from "./core/memory/memory.js";
import { ToolRegistry } from "./core/tools/registry.js";
import { BillingService } from "./billing/billing.service.js";
import { AuthService } from "./modules/auth/auth.service.js";
import { CrmModule } from "./modules/crm/crm.module.js";
import { FinanceModule } from "./modules/finance/finance.module.js";
import { FunnelModule } from "./modules/funnel/funnel.module.js";
import { AuditLog } from "./platform/audit.js";
import { EventBus } from "./platform/event-bus.js";
import { RateLimiter } from "./platform/rate-limit.js";

export interface Container {
  env: Env;
  events: EventBus;
  memory: MemoryStore;
  tools: ToolRegistry;
  agents: AgentRegistry;
  llm: LlmRouter;
  orchestrator: Orchestrator;
  auth: AuthService;
  billing: BillingService;
  crm: CrmModule;
  finance: FinanceModule;
  funnel: FunnelModule;
  proactive: ProactiveEngine;
  audit: AuditLog;
  limiters: { auth: RateLimiter; chat: RateLimiter; api: RateLimiter };
}

/** Compõe o sistema inteiro. Ponto único de wiring — fácil de testar. */
export function buildContainer(env: Env = loadEnv()): Container {
  const events = new EventBus();
  const memory = new MemoryStore();
  const tools = new ToolRegistry();
  const agents = new AgentRegistry();
  const llm = new LlmRouter(env);
  const audit = new AuditLog();

  // Módulos de negócio registram suas ferramentas no Tool Registry.
  const crm = new CrmModule(events);
  const finance = new FinanceModule(events);
  const funnel = new FunnelModule(events);
  crm.register(tools);
  finance.register(tools);
  funnel.register(tools);

  // Inteligência Proativa: analisa e antecipa (exposta como ferramenta da IA).
  const proactive = new ProactiveEngine(crm, finance, funnel);
  tools.register({
    name: "insights.review",
    description: "Faz uma análise proativa da empresa e antecipa o que precisa de atenção.",
    params: {},
    handler: (ctx) => {
      const list = proactive.review(ctx, "pt");
      return {
        ok: true,
        summary: `Análise proativa: ${list.slice(0, 3).map((i) => i.title).join("; ")}.`,
        data: { kind: "insights", title: "Inteligência Proativa", payload: list },
      };
    },
  });

  // Conselho de Agentes exposto como ferramenta. Só usa o LLM quando há um
  // provedor real ativo; com o mock, gera a deliberação a partir das personas.
  const council = new Council(llm.activeProvider === "mock" ? undefined : llm);
  tools.register({
    name: "council.deliberate",
    description: "Reúne o conselho de agentes para debater e decidir um tema complexo.",
    params: { topic: { type: "string", description: "O tema a deliberar", required: true } },
    handler: async (_ctx, input) => {
      const result = await council.deliberate(String(input.topic ?? ""));
      return {
        ok: true,
        summary: `Conselho decidiu: ${result.decision}`,
        data: { kind: "deliberation", title: "Conselho de Agentes", payload: result },
      };
    },
  });

  // Fronteira entre módulos via eventos: novo lead no funil vira cliente no CRM.
  events.on("funnel.lead.created", (e) => {
    const lead = e.payload as { name: string; contact: string };
    crm.createCustomer({ tenantId: e.tenantId, userId: "system", role: "owner" }, lead.name, lead.contact);
  });

  // Aprendizado proativo: reage a eventos de domínio.
  events.on("finance.entry.recorded", (e) => {
    const entry = e.payload as { type: string; amount: number };
    if (entry.type === "expense" && entry.amount > 10000) {
      memory.remember({ tenantId: e.tenantId }, "long", `Despesa alta detectada: R$ ${entry.amount}.`);
    }
  });

  const orchestrator = new Orchestrator(llm, tools, memory);
  const auth = new AuthService(env.jwtSecret, audit);
  const billing = new BillingService(
    env.baseCurrency as Currency,
    env.payoutCurrency as Currency,
    env.webhookSecret,
  );

  const limiters = {
    auth: new RateLimiter(10, 60_000), // 10 tentativas de login por minuto/IP
    chat: new RateLimiter(60, 60_000), // 60 mensagens por minuto/usuário
    api: new RateLimiter(300, 60_000), // 300 req por minuto/IP (global)
  };

  return { env, events, memory, tools, agents, llm, orchestrator, auth, billing, crm, finance, funnel, proactive, audit, limiters };
}
