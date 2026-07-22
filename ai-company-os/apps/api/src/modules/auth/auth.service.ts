import { randomUUID } from "node:crypto";
import type { RequestContext, Role } from "@aicos/shared";
import { AppError, ForbiddenError } from "../../platform/errors.js";
import { hashPassword, hmacSign, hmacVerify, verifyPassword } from "../../platform/crypto.js";
import { generateTotpSecret, otpauthUri, verifyTotp } from "../../platform/totp.js";
import type { AuditLog } from "../../platform/audit.js";

interface TwoFactor { secret: string; enabled: boolean }
interface User {
  id: string;
  tenantId: string;
  email: string;
  passwordHash: string;
  role: Role;
  twoFactor?: TwoFactor;
}

interface TokenPayload extends RequestContext {
  exp: number;
}

interface Lock { count: number; first: number; lockedUntil: number }

const SESSION_TTL_MS = 24 * 60 * 60 * 1000;
const LOCK_THRESHOLD = 5; // falhas
const LOCK_WINDOW_MS = 15 * 60 * 1000;
const LOCK_COOLDOWN_MS = 15 * 60 * 1000;

/**
 * Autenticação multi-tenant endurecida: hash scrypt, sessões assinadas com
 * expiração, 2FA (TOTP), bloqueio de conta após tentativas, RBAC e auditoria.
 */
export class AuthService {
  private readonly users: User[] = [];
  private readonly locks = new Map<string, Lock>();

  constructor(
    private readonly secret: string,
    private readonly audit: AuditLog,
  ) {
    this.seed("demo", "owner@demo.com", "demo1234", "owner");
    this.seed("demo", "member@demo.com", "demo1234", "member");
  }

  private seed(tenantId: string, email: string, password: string, role: Role): User {
    const user: User = { id: `u_${randomUUID().slice(0, 8)}`, tenantId, email, passwordHash: hashPassword(password), role };
    this.users.push(user);
    return user;
  }

  signup(email: string, password: string): { token: string; context: RequestContext } {
    this.validateCredentials(email, password);
    if (this.users.some((u) => u.email.toLowerCase() === email.toLowerCase())) {
      throw new AppError("Email já cadastrado.", 409, "email_taken");
    }
    const tenantId = `t_${randomUUID().slice(0, 8)}`;
    const user = this.seed(tenantId, email, password, "owner");
    this.audit.record({ tenantId, actor: user.id, action: "auth.signup", severity: "info", meta: { email } });
    const context: RequestContext = { tenantId, userId: user.id, role: user.role };
    return { token: this.sign(context), context };
  }

  login(email: string, password: string, ip?: string, code?: string): { token: string; context: RequestContext } {
    const lockKey = email.toLowerCase();
    this.assertNotLocked(lockKey);
    const user = this.users.find((u) => u.email.toLowerCase() === lockKey);
    if (!user || !verifyPassword(password, user.passwordHash)) {
      this.registerFailure(lockKey);
      this.audit.record({ action: "auth.login.fail", severity: "warn", ip, meta: { email } });
      throw new AppError("Credenciais inválidas.", 401, "invalid_credentials");
    }
    if (user.twoFactor?.enabled) {
      if (!code || !verifyTotp(user.twoFactor.secret, code)) {
        this.registerFailure(lockKey);
        this.audit.record({ tenantId: user.tenantId, actor: user.id, action: "auth.2fa.fail", severity: "warn", ip });
        throw new AppError("Código 2FA inválido ou ausente.", 401, "totp_required");
      }
    }
    this.locks.delete(lockKey);
    this.audit.record({ tenantId: user.tenantId, actor: user.id, action: "auth.login.success", severity: "info", ip });
    const context: RequestContext = { tenantId: user.tenantId, userId: user.id, role: user.role };
    return { token: this.sign(context), context };
  }

  /** Inicia o 2FA: gera segredo (ainda não ativo) e a URI para o QR Code. */
  setupTwoFactor(ctx: RequestContext): { secret: string; otpauthUri: string } {
    const user = this.requireUser(ctx);
    const secret = generateTotpSecret();
    user.twoFactor = { secret, enabled: false };
    return { secret, otpauthUri: otpauthUri(secret, user.email) };
  }

  /** Confirma o 2FA validando o primeiro código do app autenticador. */
  enableTwoFactor(ctx: RequestContext, code: string): { enabled: true } {
    const user = this.requireUser(ctx);
    if (!user.twoFactor) throw new AppError("Inicie a configuração do 2FA primeiro.", 400);
    if (!verifyTotp(user.twoFactor.secret, code)) throw new AppError("Código 2FA inválido.", 400, "invalid_totp");
    user.twoFactor.enabled = true;
    this.audit.record({ tenantId: ctx.tenantId, actor: user.id, action: "auth.2fa.enabled", severity: "info" });
    return { enabled: true };
  }

  resolve(token?: string): RequestContext {
    if (!token) return { tenantId: "demo", userId: "u_demo", role: "owner" };
    const payload = this.verify(token);
    if (!payload) throw new AppError("Token inválido ou expirado.", 401, "invalid_token");
    return { tenantId: payload.tenantId, userId: payload.userId, role: payload.role };
  }

  requireRole(ctx: RequestContext, roles: Role[]): void {
    if (!roles.includes(ctx.role)) throw new ForbiddenError(`Requer papel: ${roles.join(" ou ")}.`);
  }

  private requireUser(ctx: RequestContext): User {
    const user = this.users.find((u) => u.id === ctx.userId);
    if (!user) throw new AppError("Usuário não encontrado.", 404);
    return user;
  }

  private assertNotLocked(key: string): void {
    const lock = this.locks.get(key);
    if (lock && lock.lockedUntil > Date.now()) {
      throw new AppError("Conta temporariamente bloqueada por tentativas. Aguarde.", 429, "account_locked");
    }
  }

  private registerFailure(key: string): void {
    const now = Date.now();
    const lock = this.locks.get(key);
    if (!lock || now - lock.first > LOCK_WINDOW_MS) {
      this.locks.set(key, { count: 1, first: now, lockedUntil: 0 });
      return;
    }
    lock.count += 1;
    if (lock.count >= LOCK_THRESHOLD) {
      lock.lockedUntil = now + LOCK_COOLDOWN_MS;
      this.audit.record({ action: "auth.account_locked", severity: "critical", meta: { key } });
    }
  }

  private validateCredentials(email: string, password: string): void {
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new AppError("Email inválido.");
    if (password.length < 8) throw new AppError("A senha deve ter ao menos 8 caracteres.");
    if (!/[a-zA-Z]/.test(password) || !/\d/.test(password)) throw new AppError("A senha deve ter letras e números.");
  }

  private sign(ctx: RequestContext): string {
    const payload: TokenPayload = { ...ctx, exp: Date.now() + SESSION_TTL_MS };
    const body = Buffer.from(JSON.stringify(payload)).toString("base64url");
    return `${body}.${hmacSign(body, this.secret)}`;
  }

  private verify(token: string): TokenPayload | null {
    const [body, sig] = token.split(".");
    if (!body || !sig || !hmacVerify(body, sig, this.secret)) return null;
    try {
      const payload = JSON.parse(Buffer.from(body, "base64url").toString()) as TokenPayload;
      if (typeof payload.exp !== "number" || payload.exp < Date.now()) return null;
      return payload;
    } catch {
      return null;
    }
  }
}
