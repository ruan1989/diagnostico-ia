import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

/**
 * TOTP (RFC 6238) para autenticação de dois fatores — compatível com Google
 * Authenticator, Authy, 1Password. Implementação própria com node:crypto,
 * sem dependências externas.
 */
const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function base32Encode(buf: Buffer): string {
  let bits = 0, value = 0, out = "";
  for (const byte of buf) {
    value = (value << 8) | byte; bits += 8;
    while (bits >= 5) { out += B32[(value >>> (bits - 5)) & 31]; bits -= 5; }
  }
  if (bits > 0) out += B32[(value << (5 - bits)) & 31];
  return out;
}
function base32Decode(str: string): Buffer {
  let bits = 0, value = 0; const out: number[] = [];
  for (const c of str.replace(/=+$/, "").toUpperCase()) {
    const idx = B32.indexOf(c); if (idx < 0) continue;
    value = (value << 5) | idx; bits += 5;
    if (bits >= 8) { out.push((value >>> (bits - 8)) & 0xff); bits -= 8; }
  }
  return Buffer.from(out);
}

export function generateTotpSecret(): string {
  return base32Encode(randomBytes(20));
}

function hotp(secret: string, counter: number): string {
  const key = base32Decode(secret);
  const buf = Buffer.alloc(8);
  buf.writeBigUInt64BE(BigInt(counter));
  const hmac = createHmac("sha1", key).update(buf).digest();
  const offset = hmac[hmac.length - 1] & 0xf;
  const code = ((hmac[offset] & 0x7f) << 24) | ((hmac[offset + 1] & 0xff) << 16) | ((hmac[offset + 2] & 0xff) << 8) | (hmac[offset + 3] & 0xff);
  return (code % 1_000_000).toString().padStart(6, "0");
}

export function totp(secret: string, at: number = Date.now()): string {
  return hotp(secret, Math.floor(at / 30_000));
}

/** Verifica o código com janela de ±1 passo (tolera relógio dessincronizado). */
export function verifyTotp(secret: string, code: string, at: number = Date.now(), window = 1): boolean {
  const counter = Math.floor(at / 30_000);
  const clean = code.replace(/\s/g, "");
  for (let i = -window; i <= window; i++) {
    const expected = hotp(secret, counter + i);
    const a = Buffer.from(expected), b = Buffer.from(clean);
    if (a.length === b.length && timingSafeEqual(a, b)) return true;
  }
  return false;
}

/** URI otpauth:// para gerar o QR Code no app autenticador. */
export function otpauthUri(secret: string, account: string, issuer = "Company OS"): string {
  const label = encodeURIComponent(`${issuer}:${account}`);
  return `otpauth://totp/${label}?secret=${secret}&issuer=${encodeURIComponent(issuer)}&period=30&digits=6`;
}
