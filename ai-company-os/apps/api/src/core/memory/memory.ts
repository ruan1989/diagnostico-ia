interface MemoryCtx {
  tenantId: string;
  userId?: string;
}

export type MemoryLayer = "short" | "long" | "user" | "semantic";

export interface MemoryRecord {
  layer: MemoryLayer;
  tenantId: string;
  userId?: string;
  content: string;
  at: string;
}

/**
 * Memória em camadas. Hoje in-memory; a camada "semantic" tem interface pronta
 * para um backend vetorial (Qdrant/pgvector) — a busca atual é por palavra-chave.
 *
 * - short:    contexto da conversa atual (janela curta).
 * - long:     fatos duradouros da empresa.
 * - user:     preferências/estilo por usuário.
 * - semantic: base de conhecimento pesquisável.
 */
export class MemoryStore {
  private readonly records: MemoryRecord[] = [];

  remember(
    ctx: MemoryCtx,
    layer: MemoryLayer,
    content: string,
  ): void {
    this.records.push({
      layer,
      tenantId: ctx.tenantId,
      userId: layer === "user" ? ctx.userId : undefined,
      content,
      at: new Date().toISOString(),
    });
  }

  /** Últimas N lembranças da conversa (memória de curto prazo). */
  recentShortTerm(tenantId: string, limit = 8): MemoryRecord[] {
    return this.records
      .filter((r) => r.tenantId === tenantId && r.layer === "short")
      .slice(-limit);
  }

  /** Recupera contexto relevante para um texto (long + semantic + user). */
  recall(ctx: MemoryCtx, query: string, limit = 5): MemoryRecord[] {
    const terms = query.toLowerCase().split(/\s+/).filter((w) => w.length > 3);
    const scored = this.records
      .filter(
        (r) =>
          r.tenantId === ctx.tenantId &&
          r.layer !== "short" &&
          (!r.userId || r.userId === ctx.userId),
      )
      .map((r) => {
        const text = r.content.toLowerCase();
        const score = terms.reduce((s, term) => (text.includes(term) ? s + 1 : s), 0);
        return { r, score };
      })
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
    return scored.map((x) => x.r);
  }

  all(tenantId: string): MemoryRecord[] {
    return this.records.filter((r) => r.tenantId === tenantId);
  }
}
