import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { Container } from "../bootstrap.js";

export function clientIp(req: FastifyRequest): string {
  const fwd = req.headers["x-forwarded-for"];
  if (typeof fwd === "string") return fwd.split(",")[0].trim();
  return req.ip;
}

/**
 * Defesa em profundidade no HTTP:
 *  - cabeçalhos de segurança (anti-clickjacking, anti-MIME-sniffing, HSTS, CSP)
 *  - rate limit global por IP (anti-flood / anti-DoS de camada 7)
 *  - limite de tamanho de corpo (evita payloads gigantes)
 */
export function registerSecurity(app: FastifyInstance, c: Container): void {
  app.addHook("onRequest", async (req: FastifyRequest, reply: FastifyReply) => {
    reply.header("X-Content-Type-Options", "nosniff");
    reply.header("X-Frame-Options", "DENY");
    reply.header("Referrer-Policy", "no-referrer");
    reply.header("X-XSS-Protection", "0");
    reply.header("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload");
    reply.header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'");
    reply.header("Permissions-Policy", "geolocation=(), microphone=(), camera=()");

    // Health é isento para não atrapalhar probes de infraestrutura.
    if (req.url === "/health") return;

    const ip = clientIp(req);
    const global = c.limiters.api.allow(`ip:${ip}`);
    if (!global.ok) {
      c.audit.record({ action: "security.rate_limited", severity: "warn", ip, meta: { scope: "api", url: req.url } });
      reply.header("Retry-After", Math.ceil(global.retryAfterMs / 1000));
      return reply.status(429).send({ error: "rate_limited", message: "Muitas requisições. Tente novamente em instantes." });
    }
  });
}
