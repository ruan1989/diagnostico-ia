import { useEffect, useRef, useState } from "react";
import type { ChatResponse, ExecutedStep } from "@aicos/shared";
import { health, sendChat } from "./api.js";

interface Turn {
  role: "user" | "assistant";
  text: string;
  steps?: ExecutedStep[];
  data?: ChatResponse["data"];
}

const SUGGESTIONS = [
  "cadastre o cliente Maria Souza, email maria@acme.com",
  "crie uma proposta de 4200 para Maria Souza",
  "lance uma receita de 5000 recebida da Maria",
  "lance uma despesa de 1800 com fornecedor",
  "quanto lucrei este mês?",
  "qual meu fluxo de caixa?",
  "chame o conselho: devo contratar um vendedor?",
];

export function App() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    health()
      .then((h) => setStatus(`online · IA: ${h.llm} · ${h.tools} ferramentas`))
      .catch(() => setStatus("API offline — rode `npm run dev`"));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  async function submit(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setTurns((t) => [...t, { role: "user", text: message }]);
    setBusy(true);
    try {
      const res = await sendChat(message);
      setTurns((t) => [...t, { role: "assistant", text: res.reply, steps: res.steps, data: res.data }]);
    } catch (err) {
      setTurns((t) => [...t, { role: "assistant", text: `⚠️ ${(err as Error).message}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◆</span> AI Company OS
        </div>
        <div className="status">{status}</div>
      </header>

      <main className="chat">
        {turns.length === 0 && (
          <div className="welcome">
            <h1>Uma tela. Toda a empresa.</h1>
            <p>Administre CRM, financeiro e mais — apenas conversando. Experimente:</p>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => submit(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn, i) => (
          <div key={i} className={`turn ${turn.role}`}>
            <div className="bubble">
              {turn.role === "assistant" && turn.steps && turn.steps.length > 0 && (
                <div className="steps">
                  {turn.steps.map((s, j) => (
                    <span key={j} className={`step ${s.ok ? "ok" : "fail"}`} title={s.summary}>
                      {s.agent} · {s.tool}
                    </span>
                  ))}
                </div>
              )}
              <div className="text">{turn.text}</div>
              {turn.data && turn.data.length > 0 && (
                <div className="cards">
                  {turn.data.map((d, k) => (
                    <DataCard key={k} kind={d.kind} title={d.title} payload={d.payload} />
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && <div className="turn assistant"><div className="bubble text muted">pensando…</div></div>}
        <div ref={endRef} />
      </main>

      <footer className="composer">
        <input
          value={input}
          disabled={busy}
          placeholder="Converse com a sua empresa…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit(input)}
        />
        <button disabled={busy} onClick={() => submit(input)}>
          Enviar
        </button>
      </footer>
    </div>
  );
}

function DataCard({ kind, title, payload }: { kind: string; title: string; payload: unknown }) {
  return (
    <div className="card">
      <div className="card-title">{title}</div>
      {kind === "kpi" && <KpiView payload={payload as Record<string, number>} />}
      {kind === "deliberation" && <Deliberation payload={payload as DeliberationPayload} />}
      {(kind === "table" || kind === "customer" || kind === "proposal" || kind === "entry") && (
        <pre className="json">{JSON.stringify(payload, null, 2)}</pre>
      )}
    </div>
  );
}

function KpiView({ payload }: { payload: Record<string, number> }) {
  const labels: Record<string, string> = {
    income: "Receita",
    expense: "Despesa",
    profit: "Lucro",
    balance: "Saldo",
  };
  return (
    <div className="kpis">
      {Object.entries(payload).map(([k, v]) => (
        <div key={k} className="kpi">
          <div className="kpi-label">{labels[k] ?? k}</div>
          <div className="kpi-value">R$ {Number(v).toLocaleString("pt-BR")}</div>
        </div>
      ))}
    </div>
  );
}

interface DeliberationPayload {
  topic: string;
  decision: string;
  rationale: string;
  opinions: { agent: string; title: string; argument: string; vote: string }[];
}

function Deliberation({ payload }: { payload: DeliberationPayload }) {
  return (
    <div className="delib">
      <div className="decision">Decisão: {payload.decision}</div>
      <ul>
        {payload.opinions.map((o, i) => (
          <li key={i}>
            <strong>{o.agent}</strong> <em>({o.vote})</em>: {o.argument}
          </li>
        ))}
      </ul>
      <div className="muted">{payload.rationale}</div>
    </div>
  );
}
