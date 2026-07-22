/** Um agente especializado: persona + objetivos + escopo de ferramentas. */
export interface Agent {
  id: string;
  name: string;
  title: string; // ex.: "Diretor Financeiro"
  personality: string;
  goals: string[];
  /** Prefixos de ferramentas que este agente domina, ex.: ["finance."]. */
  toolPrefixes: string[];
}
