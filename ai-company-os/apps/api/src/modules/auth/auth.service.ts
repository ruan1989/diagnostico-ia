import { randomUUID } from "node:crypto";
import type { RequestContext, Role } from "@aicos/shared";
import { AppError, ForbiddenError } from "../../platform/errors.js";
import { hashPassword, hmacSign, hmacVerify, verifyPassword } from "../../platform/crypto.js";
import type { AuditLog } from "../../platform/audit.js";

interface User {
  id: string;
  tenantId: string;
  email: string;
  passwordHash: string;
  role: Role;
}

interface TokenPayload extends RequestContext {
  exp: number; // epoch ms
}

const SESSION_TTL_MS = 24 * 60 * 60 * 1000; // 24h

/**
 * Autenticação multi-tenant com hash de senha (scrypt), sessões assinadas com
 * expiração e auditoria. Sem dependências externas. Isola cada empresa por
 * tenantId; RBAC define o que cada papel pode fazer.
 */
export class AuthService {
  private readonly users: User[] = [];

  constructor(
    private readonly secret: string,
    private readonly audit: AuditLog,
  ) {
    // Usuários de demonstração (senha "demo"). Em produção viriam do banco.
    this.seed("demo", "owner@demo.com", "demo", "owner");
    this.seed("demo", "member@demo.com", "demo", "member");
  }

  private seed(tenantId: string, email: string, password: string, role: Role): User {
    const user: User = { id: `u_${randomUUID().slice(0, 8)}`, tenantId, email, passwordHash: hashPassword(password), role };
    this.users.push(user);
    return user;
  }

  /** Cadastro de uma nova empresa (cria tenant + usuário owner). */
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

  login(email: string, password: string, ip?: string): { token: string; context: RequestContext } {
    const user = this.users.find((u) => u.email.toLowerCase() === email.toLowerCase());
    if (!user || !verifyPassword(password, user.passwordHash)) {
      this.audit.record({ action: "auth.login.fail", severity: "warn", ip, meta: { email } });
      throw new AppError("Credenciais inválidas.", 401, "invalid_credentials");
    }
    this.audit.record({ tenantId: user.tenantId, actor: user.id, action: "auth.login.success", severity: "info", ip });
    const context: RequestContext = { tenantId: user.tenantId, userId: user.id, role: user.role };
    return { token: this.sign(context), context };
  }

  /** Resolve o contexto a partir do token. Sem token → contexto de demo. */
  resolve(token?: string): RequestContext {
    if (!token) return { tenantId: "demo", userId: "u_demo", role: "owner" };
    const payload = this.verify(token);
    if (!payload) throw new AppError("Token inválido ou expirado.", 401, "invalid_token");
    return { tenantId: payload.tenantId, userId: payload.userId, role: payload.role };
  }

  /** Autorização por papel (RBAC) — usado por rotas administrativas. */
  requireRole(ctx: RequestContext, roles: Role[]): void {
    if (!roles.includes(ctx.role)) {
      throw new ForbiddenError(`Requer papel: ${roles.join(" ou ")}.`);
    }
  }

  private validateCredentials(email: string, password: string): void {
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new AppError("Email inválido.");
    if (password.length < 8) throw new AppError("A senha deve ter ao menos 8 caracteres.");
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
      if (typeof payload.exp !== "number" || payload.exp < Date.now()) return null; // expirado
      return payload;
    } catch {
      return null;
    }
  }
}
