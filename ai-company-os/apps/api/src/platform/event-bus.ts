/**
 * Barramento de eventos in-process. Publica eventos de domínio que alimentam
 * automações e o aprendizado proativo. Pronto para trocar por Kafka/RabbitMQ.
 */
export interface DomainEvent<T = unknown> {
  type: string; // ex.: "crm.lead.created", "finance.entry.recorded"
  tenantId: string;
  payload: T;
  at: string;
}

type Handler = (event: DomainEvent) => void | Promise<void>;

export class EventBus {
  private readonly handlers = new Map<string, Handler[]>();
  private readonly log: DomainEvent[] = [];

  on(type: string, handler: Handler): void {
    const list = this.handlers.get(type) ?? [];
    list.push(handler);
    this.handlers.set(type, list);
  }

  async publish<T>(type: string, tenantId: string, payload: T): Promise<void> {
    const event: DomainEvent<T> = { type, tenantId, payload, at: new Date().toISOString() };
    this.log.push(event);
    for (const handler of this.handlers.get(type) ?? []) {
      await handler(event);
    }
    for (const handler of this.handlers.get("*") ?? []) {
      await handler(event);
    }
  }

  history(tenantId: string): DomainEvent[] {
    return this.log.filter((e) => e.tenantId === tenantId);
  }
}
