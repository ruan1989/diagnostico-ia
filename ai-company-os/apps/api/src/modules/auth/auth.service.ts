import { createHmac, timingSafeEqual } from "node:crypto";
import type { RequestContext, Role } from "@aicos/shared";
import { AppError } from "../../platform/errors.js";

interface User {
  id: string;
  tenantId: string;
  email: string;
  password: string; // demo apenas; em produção use hash + salt.
  role: Role;
}

/**
 * Autenticação multi-tenant simples e autocontida. Emite um token assinado
 * (HMAC) sem dependências externas. Em produção troque por JWT + hash de senha.
 */
export class AuthService {
  private readonly users: User[] = [
    { id: "u_owner", tenantId: "demo", email: "owner@demo.com", password: "demo", role: "owner" },
    { id: "u_member", tenantId: "demo", email: "member@demo.com", password: "demo", role: "member" },
  ];

  constructor(private readonly secret: string) {}

  login(email: string, password: string): { token: string; context: RequestContext } {
    const user = this.users.find((u) => u.email === email && u.password === password);
    if (!user) throw new AppError("Credenciais inválidas.", 401, "invalid_credentials");
    const context: RequestContext = { tenantId: user.tenantId, userId: user.id, role: user.role };
    return { token: this.sign(context), context };
  }

  /** Resolve o contexto a partir do token; se ausente, cai no tenant de demo. */
  resolve(token?: string): RequestContext {
    if (!token) {
      return { tenantId: "demo", userId: "u_owner", role: "owner" };
    }
    const context = this.verify(token);
    if (!context) throw new AppError("Token inválido.", 401, "invalid_token");
    return context;
  }

  private sign(ctx: RequestContext): string {
    const payload = Buffer.from(JSON.stringify(ctx)).toString("base64url");
    const sig = createHmac("sha256", this.secret).update(payload).digest("base64url");
    return `${payload}.${sig}`;
  }

  private verify(token: string): RequestContext | null {
    const [payload, sig] = token.split(".");
    if (!payload || !sig) return null;
    const expected = createHmac("sha256", this.secret).update(payload).digest("base64url");
    const a = Buffer.from(sig);
    const b = Buffer.from(expected);
    if (a.length !== b.length || !timingSafeEqual(a, b)) return null;
    try {
      return JSON.parse(Buffer.from(payload, "base64url").toString()) as RequestContext;
    } catch {
      return null;
    }
  }
}
