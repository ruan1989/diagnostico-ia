/**
 * Trilha de auditoria: registra eventos de segurança e ações sensíveis
 * (login, falha de login, pagamento, mudança de dados). Imutável por
 * convenção (append-only). Base para conformidade LGPD/GDPR e detecção de
 * intrusão. Em produção, persistir em store append-only/WORM.
 */
export interface AuditEvent {
  at: string;
  tenantId?: string;
  actor?: string;
  action: string; // ex.: "auth.login.success", "auth.login.fail", "billing.charge"
  ip?: string;
  meta?: Record<string, unknown>;
  severity: "info" | "warn" | "critical";
}

export class AuditLog {
  private readonly events: AuditEvent[] = [];

  record(event: Omit<AuditEvent, "at">): void {
    this.events.push({ ...event, at: new Date().toISOString() });
  }

  list(filter?: { tenantId?: string; action?: string }): AuditEvent[] {
    return this.events.filter(
      (e) =>
        (!filter?.tenantId || e.tenantId === filter.tenantId) &&
        (!filter?.action || e.action === filter.action),
    );
  }

  /** Sinais de possível ataque: muitas falhas de login recentes por IP. */
  suspiciousLoginBursts(windowMs = 60_000, threshold = 5): Record<string, number> {
    const since = Date.now() - windowMs;
    const byIp: Record<string, number> = {};
    for (const e of this.events) {
      if (e.action === "auth.login.fail" && Date.parse(e.at) >= since && e.ip) {
        byIp[e.ip] = (byIp[e.ip] ?? 0) + 1;
      }
    }
    return Object.fromEntries(Object.entries(byIp).filter(([, n]) => n >= threshold));
  }
}
