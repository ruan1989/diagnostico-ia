/**
 * Rate limiter in-memory (janela deslizante por chave). Protege contra
 * brute force de login, abuso da IA e flood de requisições. Em produção,
 * trocar o store por Redis para funcionar entre instâncias.
 */
interface Bucket {
  count: number;
  resetAt: number;
}

export class RateLimiter {
  private readonly buckets = new Map<string, Bucket>();

  constructor(
    private readonly limit: number,
    private readonly windowMs: number,
  ) {}

  /** Retorna true se permitido; false se estourou o limite. */
  allow(key: string): { ok: boolean; remaining: number; retryAfterMs: number } {
    const now = Date.now();
    const bucket = this.buckets.get(key);
    if (!bucket || bucket.resetAt <= now) {
      this.buckets.set(key, { count: 1, resetAt: now + this.windowMs });
      return { ok: true, remaining: this.limit - 1, retryAfterMs: 0 };
    }
    if (bucket.count >= this.limit) {
      return { ok: false, remaining: 0, retryAfterMs: bucket.resetAt - now };
    }
    bucket.count += 1;
    return { ok: true, remaining: this.limit - bucket.count, retryAfterMs: 0 };
  }

  /** Limpeza periódica de buckets expirados (evita crescer indefinidamente). */
  sweep(): void {
    const now = Date.now();
    for (const [key, bucket] of this.buckets) {
      if (bucket.resetAt <= now) this.buckets.delete(key);
    }
  }
}
