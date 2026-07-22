// Segurança client-side do site: trava por PIN (leve, para dispositivo
// compartilhado) e limpeza de dados. Honestidade: um PIN em localStorage não é
// proteção forte (quem tem o aparelho pode contornar) — é uma trava de cortesia.
const PIN_KEY = "companyos_pin_v1";
const STATE_KEY = "companyos_state_v1";

async function sha256(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function hasPin(): boolean {
  return !!localStorage.getItem(PIN_KEY);
}

export async function setPin(pin: string): Promise<void> {
  const salt = crypto.randomUUID();
  localStorage.setItem(PIN_KEY, `${salt}$${await sha256(salt + pin)}`);
}

export async function verifyPin(pin: string): Promise<boolean> {
  const raw = localStorage.getItem(PIN_KEY);
  if (!raw) return true;
  const [salt, hash] = raw.split("$");
  return (await sha256(salt + pin)) === hash;
}

export function clearPin(): void {
  localStorage.removeItem(PIN_KEY);
}

export function clearData(): void {
  localStorage.removeItem(STATE_KEY);
}
