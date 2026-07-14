import { describe, expect, it } from "vitest";
import { AuditLog } from "../platform/audit.js";
import { generateTotpSecret, otpauthUri, totp, verifyTotp } from "../platform/totp.js";
import { validate } from "../platform/validation.js";
import { AuthService } from "../modules/auth/auth.service.js";

describe("2FA (TOTP RFC 6238)", () => {
  it("gera e verifica código; rejeita inválido", () => {
    const secret = generateTotpSecret();
    const code = totp(secret);
    expect(verifyTotp(secret, code)).toBe(true);
    expect(verifyTotp(secret, "000000")).toBe(false);
    expect(otpauthUri(secret, "a@b.com")).toContain("otpauth://totp/");
  });
  it("tolera janela de tempo (±1 passo)", () => {
    const secret = generateTotpSecret();
    const now = Date.now();
    expect(verifyTotp(secret, totp(secret, now - 30_000), now)).toBe(true);
  });
});

describe("Validação de entrada", () => {
  it("aceita válido e rejeita inválido", () => {
    const schema = { email: { type: "email", required: true }, amount: { type: "number", min: 1 } } as const;
    expect(validate(schema, { email: "a@b.com", amount: 10 })).toEqual({ email: "a@b.com", amount: 10 });
    expect(() => validate(schema, { email: "nao-email" })).toThrow();
    expect(() => validate(schema, { amount: 5 })).toThrow(); // email obrigatório faltando
    expect(() => validate(schema as never, { email: "a@b.com", amount: 0 })).toThrow(); // abaixo do mínimo
  });
});

describe("Auth endurecida", () => {
  it("fluxo 2FA: setup → enable → exige código no login", () => {
    const auth = new AuthService("seg", new AuditLog());
    const { context } = auth.signup("ceo@empresa.com", "senhaForte1");
    const { secret } = auth.setupTwoFactor(context);
    auth.enableTwoFactor(context, totp(secret));
    // login sem código deve falhar
    expect(() => auth.login("ceo@empresa.com", "senhaForte1")).toThrow();
    // login com código correto passa
    expect(auth.login("ceo@empresa.com", "senhaForte1", "1.1.1.1", totp(secret)).token).toBeTruthy();
  });

  it("bloqueia a conta após várias falhas (anti brute force)", () => {
    const auth = new AuthService("seg", new AuditLog());
    for (let i = 0; i < 5; i++) {
      try { auth.login("owner@demo.com", "errada", "2.2.2.2"); } catch { /* esperado */ }
    }
    // agora a conta está bloqueada mesmo com a senha correta
    expect(() => auth.login("owner@demo.com", "demo1234")).toThrow(/bloqueada/i);
  });

  it("exige senha com letras e números no signup", () => {
    const auth = new AuthService("seg", new AuditLog());
    expect(() => auth.signup("a@b.com", "somentetexto")).toThrow();
    expect(() => auth.signup("a@b.com", "12345678")).toThrow();
    expect(auth.signup("a@b.com", "boaSenha1").context.role).toBe("owner");
  });
});
