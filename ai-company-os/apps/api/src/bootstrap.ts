import type { Currency } from "@aicos/shared";
import { loadEnv, type Env } from "./config/env.js";
import { Council } from "./core/agents/council.js";
import { AgentRegistry } from "./core/agents/registry.js";
import { Orchestrator } from "./core/ai-core/orchestrator.js";
import { LlmRouter } from "./core/llm/router.js";
import { MemoryStore } from "./core/memory/memory.js";
import { ToolRegistry } from "./core/tools/registry.js";
import { BillingService } from "./billing/billing.service.js";
import { AuthService } from "./modules/auth/auth.service.js";
import { CrmModule } from "./modules/crm/crm.module.js";
import { FinanceModule } from "./modules/finance/finance.module.js";
import { EventBus } from "./platform/event-bus.js";

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
}

/** Compõe o sistema inteiro. Ponto único de wiring — fácil de testar. */
export function buildContainer(env: Env = loadEnv()): Container {
  const events = new EventBus();
  const memory = new MemoryStore();
  const tools = new ToolRegistry();
  const agents = new AgentRegistry();
  const llm = new LlmRouter(env);

  // Módulos de negócio registram suas ferramentas no Tool Registry.
  const crm = new CrmModule(events);
  const finance = new FinanceModule(events);
  crm.register(tools);
  finance.register(tools);

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

  // Aprendizado proativo: reage a eventos de domínio (exemplo mínimo).
  events.on("finance.entry.recorded", (e) => {
    const entry = e.payload as { type: string; amount: number };
    if (entry.type === "expense" && entry.amount > 10000) {
      memory.remember({ tenantId: e.tenantId }, "long", `Despesa alta detectada: R$ ${entry.amount}.`);
    }
  });

  const orchestrator = new Orchestrator(llm, tools, memory);
  const auth = new AuthService(env.jwtSecret);
  const billing = new BillingService(
    env.baseCurrency as Currency,
    env.payoutCurrency as Currency,
  );

  return { env, events, memory, tools, agents, llm, orchestrator, auth, billing, crm, finance };
}
