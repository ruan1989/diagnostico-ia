import { randomUUID } from "node:crypto";

/**
 * Repositório genérico, isolado por tenant. É a única fronteira de persistência.
 * A implementação padrão é in-memory; trocar por Postgres é implementar esta
 * mesma interface — nenhum módulo de negócio muda.
 */
export interface Entity {
  id: string;
  tenantId: string;
}

export interface Repository<T extends Entity> {
  create(tenantId: string, data: Omit<T, "id" | "tenantId">): T;
  get(tenantId: string, id: string): T | undefined;
  list(tenantId: string, predicate?: (item: T) => boolean): T[];
  update(tenantId: string, id: string, patch: Partial<T>): T | undefined;
  remove(tenantId: string, id: string): boolean;
}

export class InMemoryRepository<T extends Entity> implements Repository<T> {
  private readonly store = new Map<string, T>();

  create(tenantId: string, data: Omit<T, "id" | "tenantId">): T {
    const entity = { ...data, id: randomUUID(), tenantId } as T;
    this.store.set(entity.id, entity);
    return entity;
  }

  get(tenantId: string, id: string): T | undefined {
    const item = this.store.get(id);
    return item && item.tenantId === tenantId ? item : undefined;
  }

  list(tenantId: string, predicate?: (item: T) => boolean): T[] {
    const items = [...this.store.values()].filter((i) => i.tenantId === tenantId);
    return predicate ? items.filter(predicate) : items;
  }

  update(tenantId: string, id: string, patch: Partial<T>): T | undefined {
    const current = this.get(tenantId, id);
    if (!current) return undefined;
    const next = { ...current, ...patch, id: current.id, tenantId } as T;
    this.store.set(id, next);
    return next;
  }

  remove(tenantId: string, id: string): boolean {
    const current = this.get(tenantId, id);
    if (!current) return false;
    return this.store.delete(id);
  }
}
