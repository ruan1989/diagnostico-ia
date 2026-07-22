import type { Agent } from "./agent.js";
import { AGENTS } from "./definitions.js";

export class AgentRegistry {
  private readonly agents = new Map<string, Agent>();

  constructor(seed: Agent[] = AGENTS) {
    for (const a of seed) this.agents.set(a.id, a);
  }

  add(agent: Agent): void {
    this.agents.set(agent.id, agent);
  }

  get(id: string): Agent | undefined {
    return this.agents.get(id);
  }

  list(): Agent[] {
    return [...this.agents.values()];
  }
}
