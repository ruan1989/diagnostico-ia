import type { LlmProvider } from "../llm/types.js";
import type { Agent } from "./agent.js";
import { AGENTS } from "./definitions.js";

export interface AgentOpinion {
  agent: string;
  title: string;
  argument: string;
  vote: "a favor" | "contra" | "cauteloso";
}

export interface Deliberation {
  topic: string;
  opinions: AgentOpinion[];
  decision: string;
  rationale: string;
}

/**
 * Conselho de Agentes: diante de um problema complexo, os agentes especializados
 * apresentam argumentos, votam, e o CEO AI sintetiza a decisão.
 *
 * Sem LLM real (mock), produz uma deliberação estruturada a partir das personas.
 * Com um provedor real, aprofunda cada argumento chamando o modelo.
 */
export class Council {
  constructor(private readonly llm?: LlmProvider) {}

  async deliberate(topic: string): Promise<Deliberation> {
    const board = AGENTS.filter((a) => a.id !== "ceo");
    const opinions: AgentOpinion[] = [];

    for (const agent of board) {
      opinions.push({
        agent: agent.name,
        title: agent.title,
        argument: await this.argue(agent, topic),
        vote: this.vote(agent, topic),
      });
    }

    const favor = opinions.filter((o) => o.vote === "a favor").length;
    const total = opinions.length;
    const decision =
      favor > total / 2
        ? "Prosseguir — com acompanhamento das métricas definidas."
        : "Não prosseguir agora — reduzir risco antes de avançar.";

    return {
      topic,
      opinions,
      decision,
      rationale:
        `O CEO AI consolidou ${total} pareceres (${favor} favoráveis). ` +
        `A recomendação pondera caixa, impacto comercial e risco operacional.`,
    };
  }

  private async argue(agent: Agent, topic: string): Promise<string> {
    // this.llm só é fornecido quando há um provedor real ativo; senão, template.
    if (this.llm) {
      try {
        const res = await this.llm.complete({
          system: `Você é ${agent.name}, ${agent.title}. Personalidade: ${agent.personality}. ` +
            `Objetivos: ${agent.goals.join("; ")}. Dê um parecer curto (2 frases) sobre o tema.`,
          messages: [{ role: "user", content: topic }],
          tools: [],
          temperature: 0.4,
        });
        if (res.text.trim()) return res.text.trim();
      } catch {
        /* cai no template */
      }
    }
    // Template determinístico a partir da persona.
    return (
      `Do ponto de vista de ${agent.title.toLowerCase()}, avalio "${topic}" à luz de: ` +
      `${agent.goals.slice(0, 2).join(" e ").toLowerCase()}. ${agent.personality}`
    );
  }

  private vote(agent: Agent, topic: string): AgentOpinion["vote"] {
    // Heurística simples: Finance tende a ser cauteloso; Sales/Marketing a favor.
    if (agent.id === "finance") return /contrat|invest|gast|compr/.test(topic.toLowerCase()) ? "cauteloso" : "a favor";
    if (agent.id === "sales" || agent.id === "marketing") return "a favor";
    return "a favor";
  }
}
