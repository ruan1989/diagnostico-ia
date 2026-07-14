import type { Lang } from "@aicos/shared";

type Dict = Record<string, string>;

const PT: Dict = {
  tagline: "Uma tela. Toda a empresa.",
  intro: "Administre CRM, financeiro e mais — apenas conversando. Experimente:",
  placeholder: "Converse com a sua empresa…",
  send: "Enviar",
  thinking: "pensando…",
  online: "online",
  offline: "API offline — rode `npm run dev`",
  tools: "ferramentas",
  pricing: "Planos & Pagamento",
  from: "a partir de",
  perMonth: "/mês",
  chooseCountry: "País do comprador",
  method: "Método",
  youPay: "Você paga",
  merchantReceives: "O dono recebe",
  close: "Fechar",
  globalNote: "Pague na sua moeda local; o dono do sistema recebe na moeda que preferir.",
};

const EN: Dict = {
  tagline: "One screen. The whole company.",
  intro: "Run CRM, finance and more — just by chatting. Try:",
  placeholder: "Talk to your company…",
  send: "Send",
  thinking: "thinking…",
  online: "online",
  offline: "API offline — run `npm run dev`",
  tools: "tools",
  pricing: "Plans & Payment",
  from: "from",
  perMonth: "/mo",
  chooseCountry: "Buyer country",
  method: "Method",
  youPay: "You pay",
  merchantReceives: "Owner receives",
  close: "Close",
  globalNote: "Pay in your local currency; the owner is paid out in the currency they choose.",
};

const SUGGESTIONS: Record<Lang, string[]> = {
  pt: [
    "cadastre o cliente Maria Souza, email maria@acme.com",
    "crie uma proposta de 4200 para Maria Souza",
    "lance uma receita de 5000 recebida da Maria",
    "quanto lucrei este mês?",
    "chame o conselho: devo contratar um vendedor?",
  ],
  en: [
    "register the customer Maria Souza, email maria@acme.com",
    "create a proposal of 4200 for Maria Souza",
    "record income of 5000 received from Maria",
    "how much profit this month?",
    "call the council: should I hire a salesperson?",
  ],
};

export function t(lang: Lang, key: string): string {
  return (lang === "en" ? EN : PT)[key] ?? key;
}

export function suggestions(lang: Lang): string[] {
  return SUGGESTIONS[lang];
}
