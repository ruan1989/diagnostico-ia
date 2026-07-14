import { AppError } from "./errors.js";

/**
 * Validação de entrada leve e sem dependências. Rejeita corpos malformados
 * (tipo errado, faltando, longo demais) antes de chegar na lógica — reduz a
 * superfície de ataque (injeção, DoS por payload, type confusion).
 */
export type FieldType = "string" | "number" | "boolean" | "email" | "enum";

export interface FieldSpec {
  type: FieldType;
  required?: boolean;
  max?: number;
  min?: number;
  values?: readonly string[];
}
export type Schema = Record<string, FieldSpec>;

export function validate<T = Record<string, unknown>>(schema: Schema, body: unknown): T {
  const src = (body ?? {}) as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  const errors: string[] = [];

  for (const [key, spec] of Object.entries(schema)) {
    const v = src[key];
    const empty = v === undefined || v === null || v === "";
    if (empty) {
      if (spec.required) errors.push(`'${key}' é obrigatório`);
      continue;
    }
    switch (spec.type) {
      case "email": {
        const s = String(v);
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s) || s.length > 254) errors.push(`'${key}' é um email inválido`);
        else out[key] = s;
        break;
      }
      case "number": {
        const n = Number(v);
        if (!Number.isFinite(n)) errors.push(`'${key}' deve ser numérico`);
        else if (spec.min !== undefined && n < spec.min) errors.push(`'${key}' menor que o mínimo`);
        else if (spec.max !== undefined && n > spec.max) errors.push(`'${key}' maior que o máximo`);
        else out[key] = n;
        break;
      }
      case "boolean":
        out[key] = v === true || v === "true";
        break;
      case "enum":
        if (!spec.values?.includes(String(v))) errors.push(`'${key}' fora dos valores permitidos`);
        else out[key] = String(v);
        break;
      default: {
        const s = String(v);
        if (spec.max && s.length > spec.max) errors.push(`'${key}' excede ${spec.max} caracteres`);
        else out[key] = s;
      }
    }
  }
  if (errors.length) throw new AppError(`Dados inválidos: ${errors.join("; ")}`, 400, "validation_error");
  return out as T;
}
