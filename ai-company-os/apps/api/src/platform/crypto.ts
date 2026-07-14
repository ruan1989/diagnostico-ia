import {
  createHmac,
  randomBytes,
  scryptSync,
  timingSafeEqual,
  createCipheriv,
  createDecipheriv,
} from "node:crypto";

/**
 * Primitivas de segurança autocontidas (sem dependências externas):
 *  - hash de senha com scrypt + salt (resistente a brute force/rainbow)
 *  - criptografia AES-256-GCM para dados sensíveis (confidencialidade + integridade)
 *  - HMAC para assinar tokens e verificar webhooks
 *  - comparação em tempo constante (anti timing-attack)
 */

// ── Senhas ──────────────────────────────────────────────────────────
export function hashPassword(password: string): string {
  const salt = randomBytes(16);
  const derived = scryptSync(password, salt, 32);
  return `scrypt$${salt.toString("hex")}$${derived.toString("hex")}`;
}

export function verifyPassword(password: string, stored: string): boolean {
  const [scheme, saltHex, hashHex] = stored.split("$");
  if (scheme !== "scrypt" || !saltHex || !hashHex) return false;
  const derived = scryptSync(password, Buffer.from(saltHex, "hex"), 32);
  const expected = Buffer.from(hashHex, "hex");
  return derived.length === expected.length && timingSafeEqual(derived, expected);
}

// ── Criptografia de campo (AES-256-GCM) ─────────────────────────────
function keyFrom(secret: string): Buffer {
  // Deriva uma chave de 32 bytes do segredo do ambiente.
  return scryptSync(secret, "aicos-field-encryption", 32);
}

export function encryptField(plaintext: string, secret: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", keyFrom(secret), iv);
  const enc = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `gcm$${iv.toString("hex")}$${tag.toString("hex")}$${enc.toString("hex")}`;
}

export function decryptField(ciphertext: string, secret: string): string {
  const [scheme, ivHex, tagHex, dataHex] = ciphertext.split("$");
  if (scheme !== "gcm") return ciphertext; // não criptografado (compatibilidade)
  const decipher = createDecipheriv("aes-256-gcm", keyFrom(secret), Buffer.from(ivHex, "hex"));
  decipher.setAuthTag(Buffer.from(tagHex, "hex"));
  return Buffer.concat([decipher.update(Buffer.from(dataHex, "hex")), decipher.final()]).toString("utf8");
}

// ── HMAC / tokens ───────────────────────────────────────────────────
export function hmacSign(payload: string, secret: string): string {
  return createHmac("sha256", secret).update(payload).digest("base64url");
}

export function hmacVerify(payload: string, signature: string, secret: string): boolean {
  const expected = hmacSign(payload, secret);
  const a = Buffer.from(signature);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function randomToken(bytes = 24): string {
  return randomBytes(bytes).toString("base64url");
}
