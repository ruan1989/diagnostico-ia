import { describe, expect, it } from "vitest";
import { buildContainer } from "../bootstrap.js";
import { AuditLog } from "../platform/audit.js";
import {
  decryptField,
  encryptField,
  hashPassword,
  hmacSign,
  hmacVerify,
  verifyPassword,
} from "../platform/crypto.js";
import { RateLimiter } from "../platform/rate-limit.js";
import { AuthService } from "../modules/auth/auth.service.js";

describe("Criptografia e senhas", () => {
  it("hash de senha: verifica correta e rejeita errada", () => {
    const h = hashPassword("s3nh4-super");
    expect(h.startsWith("scrypt$")).toBe(true);
    expect(verifyPassword("s3nh4-super", h)).toBe(true);
    expect(verifyPassword("errada", h)).toBe(false);
  });

  it("AES-256-GCM: round-trip preserva o dado e detecta adulteração", () => {
    const secret = "chave-mestra";
    const enc = encryptField("cliente@empresa.com", secret);
    expect(enc).not.toContain("cliente@empresa.com");
    expect(decryptField(enc, secret)).toBe("cliente@empresa.com");
    const tampered = enc.slice(0, -2) + "00";
    expect(() => decryptField(tampered, secret)).toThrow();
  });

  it("HMAC assina e verifica; rejeita assinatura inválida", () => {
    const sig = hmacSign("payload", "seg");
    expect(hmacVerify("payload", sig, "seg")).toBe(true);
    expect(hmacVerify("payload", sig, "outro")).toBe(false);
    expect(hmacVerify("adulterado", sig, "seg")).toBe(false);
  });
});

describe("Rate limiting (anti brute force / flood)", () => {
  it("bloqueia após estourar o limite na janela", () => {
    const rl = new RateLimiter(3, 60_000);
    expect(rl.allow("ip").ok).toBe(true);
    expect(rl.allow("ip").ok).toBe(true);
    expect(rl.allow("ip").ok).toBe(true);
    expect(rl.allow("ip").ok).toBe(false);
    // chave diferente não é afetada
    expect(rl.allow("outro-ip").ok).toBe(true);
  });
});

describe("Auth: signup, login, sessão e RBAC", () => {
  it("faz signup e resolve o contexto pelo token", () => {
    const auth = new AuthService("seg", new AuditLog());
    const { token, context } = auth.signup("novo@empresa.com", "senhaForte1");
    expect(context.role).toBe("owner");
    expect(auth.resolve(token).tenantId).toBe(context.tenantId);
  });

  it("rejeita senha curta e email duplicado", () => {
    const auth = new AuthService("seg", new AuditLog());
    expect(() => auth.signup("a@b.com", "123")).toThrow();
    auth.signup("dup@b.com", "senhaForte1");
    expect(() => auth.signup("dup@b.com", "senhaForte1")).toThrow();
  });

  it("login inválido é auditado e rejeitado", () => {
    const audit = new AuditLog();
    const auth = new AuthService("seg", audit);
    expect(() => auth.login("owner@demo.com", "errada", "1.2.3.4")).toThrow();
    expect(audit.list({ action: "auth.login.fail" }).length).toBe(1);
  });

  it("token adulterado é rejeitado", () => {
    const auth = new AuthService("seg", new AuditLog());
    const { token } = auth.login("owner@demo.com", "demo1234");
    expect(() => auth.resolve(token.slice(0, -3) + "xxx")).toThrow();
  });

  it("RBAC: member não acessa rota de admin", () => {
    const auth = new AuthService("seg", new AuditLog());
    expect(() => auth.requireRole({ tenantId: "t", userId: "u", role: "member" }, ["owner", "admin"])).toThrow();
    expect(() => auth.requireRole({ tenantId: "t", userId: "u", role: "owner" }, ["owner", "admin"])).not.toThrow();
  });

  it("detecta rajada suspeita de falhas de login por IP", () => {
    const audit = new AuditLog();
    const auth = new AuthService("seg", audit);
    for (let i = 0; i < 6; i++) {
      try { auth.login("owner@demo.com", "errada", "9.9.9.9"); } catch { /* esperado */ }
    }
    expect(Object.keys(audit.suspiciousLoginBursts())).toContain("9.9.9.9");
  });
});

describe("Container: limiters e auditoria conectados", () => {
  it("expõe limiters e audit", () => {
    const c = buildContainer();
    expect(c.limiters.auth).toBeInstanceOf(RateLimiter);
    expect(c.audit).toBeInstanceOf(AuditLog);
  });
});
