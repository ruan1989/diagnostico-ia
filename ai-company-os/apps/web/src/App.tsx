import { useEffect, useRef, useState } from "react";
import type { ChatResponse, ExecutedStep, Lang } from "@aicos/shared";
import {
  getAutomationsOn, getInsights, getOptions, getPlans, getQuote, getTasks, health, sendChat,
  setAutomationsOn, setTaskDone, type Insight, type Plan, type Quote, type Task,
} from "./api.js";
import { clearData, clearPin, hasPin, setPin, verifyPin } from "./engine/security.js";
import { suggestions, t } from "./i18n.js";

interface Turn {
  role: "user" | "assistant";
  text: string;
  steps?: ExecutedStep[];
  data?: ChatResponse["data"];
}

export function App() {
  const [lang, setLang] = useState<Lang>("pt");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [showPricing, setShowPricing] = useState(false);
  const [insights, setInsights] = useState<Insight[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [showInsights, setShowInsights] = useState(false);
  const [showSecurity, setShowSecurity] = useState(false);
  const [locked, setLocked] = useState(hasPin());
  const endRef = useRef<HTMLDivElement>(null);

  const refreshInsights = () => { getInsights(lang).then(setInsights).catch(() => {}); setTasks(getTasks()); };

  useEffect(() => {
    health()
      .then((h) => setStatus(`${t(lang, "online")} · IA: ${h.llm} · ${h.tools} ${t(lang, "tools")}`))
      .catch(() => setStatus(t(lang, "offline")));
    refreshInsights();
  }, [lang]);

  const alertCount = insights.filter((i) => i.severity === "critical" || i.severity === "warn").length + tasks.length;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  async function submit(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setTurns((prev) => [...prev, { role: "user", text: message }]);
    setBusy(true);
    try {
      const res = await sendChat(message, lang);
      setTurns((prev) => [...prev, { role: "assistant", text: res.reply, steps: res.steps, data: res.data }]);
    } catch (err) {
      setTurns((prev) => [...prev, { role: "assistant", text: `⚠️ ${(err as Error).message}` }]);
    } finally {
      setBusy(false);
      refreshInsights();
    }
  }

  if (locked) return <LockScreen lang={lang} onUnlock={() => setLocked(false)} />;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand"><span className="logo">◆</span> Company OS</div>
        <div className="topbar-right">
          <button className="ghost insights-btn" onClick={() => setShowInsights(true)}>
            💡 {t(lang, "insights")}{alertCount > 0 && <span className="badge">{alertCount}</span>}
          </button>
          <button className="ghost" onClick={() => setShowSecurity(true)}>🔒 {t(lang, "security")}</button>
          <button className="ghost" onClick={() => setShowPricing(true)}>{t(lang, "pricing")}</button>
          <div className="lang">
            <button className={lang === "pt" ? "on" : ""} onClick={() => setLang("pt")}>PT</button>
            <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>EN</button>
          </div>
          <div className="status">{status}</div>
        </div>
      </header>

      <main className="chat">
        {turns.length === 0 && (
          <div className="welcome">
            <h1>{t(lang, "tagline")}</h1>
            <p className="hero-sub">{t(lang, "heroSub")}</p>
            <div className="feature-grid">
              {[["f1t", "f1d"], ["f2t", "f2d"], ["f3t", "f3d"], ["f4t", "f4d"]].map(([tt, dd]) => (
                <div key={tt} className="feature">
                  <div className="feature-t">{t(lang, tt)}</div>
                  <div className="feature-d">{t(lang, dd)}</div>
                </div>
              ))}
            </div>
            <p className="intro">{t(lang, "intro")}</p>
            <div className="chips">
              {suggestions(lang).map((s) => (
                <button key={s} className="chip" onClick={() => submit(s)}>{s}</button>
              ))}
            </div>
            <p className="demo-note">{t(lang, "demoNote")}</p>
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
        {busy && <div className="turn assistant"><div className="bubble text muted">{t(lang, "thinking")}</div></div>}
        <div ref={endRef} />
      </main>

      <footer className="composer">
        <input
          value={input}
          disabled={busy}
          placeholder={t(lang, "placeholder")}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit(input)}
        />
        <button disabled={busy} onClick={() => submit(input)}>{t(lang, "send")}</button>
      </footer>

      {showPricing && <Pricing lang={lang} onClose={() => setShowPricing(false)} />}
      {showInsights && (
        <InsightsPanel
          lang={lang}
          insights={insights}
          tasks={tasks}
          onClose={() => setShowInsights(false)}
          onRun={(cmd) => { setShowInsights(false); submit(cmd); }}
          onRefresh={refreshInsights}
        />
      )}
      {showSecurity && <SecurityCenter lang={lang} onClose={() => setShowSecurity(false)} />}
    </div>
  );
}

function LockScreen({ lang, onUnlock }: { lang: Lang; onUnlock: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState(false);
  // Lê o valor direto do DOM (robusto contra corrida de estado do React).
  const tryUnlock = async () => {
    const pin = ref.current?.value ?? "";
    if (await verifyPin(pin)) onUnlock();
    else { setErr(true); if (ref.current) ref.current.value = ""; }
  };
  return (
    <div className="lock-screen">
      <form className="lock-box" onSubmit={(e) => { e.preventDefault(); void tryUnlock(); }}>
        <div className="lock-logo">◆</div>
        <h2>Company OS</h2>
        <p className="muted">{t(lang, "pinEnter")}</p>
        <input
          ref={ref} className="pin-input" type="password" inputMode="numeric" autoFocus
          onChange={() => setErr(false)}
        />
        {err && <div className="pin-err">{t(lang, "pinWrong")}</div>}
        <button type="submit" className="cta-btn" onClick={(e) => { e.preventDefault(); void tryUnlock(); }}>{t(lang, "unlock")}</button>
      </form>
    </div>
  );
}

interface Control { ok: boolean; label: string; scope: string }
function SecurityCenter({ lang, onClose }: { lang: Lang; onClose: () => void }) {
  const en = lang === "en";
  const [pinOn, setPinOn] = useState(hasPin());
  const [pin, setPinVal] = useState("");
  const controls: Control[] = [
    { ok: true, label: en ? "Password hashing (scrypt + salt)" : "Hash de senha (scrypt + salt)", scope: "backend" },
    { ok: true, label: en ? "AES-256-GCM field encryption" : "Criptografia de campo AES-256-GCM", scope: "backend" },
    { ok: true, label: en ? "Two-factor auth (TOTP)" : "Autenticação em 2 fatores (TOTP)", scope: "backend" },
    { ok: true, label: en ? "Account lockout (brute-force)" : "Bloqueio de conta (brute-force)", scope: "backend" },
    { ok: true, label: en ? "Signed sessions with expiry (HMAC)" : "Sessões assinadas com expiração (HMAC)", scope: "backend" },
    { ok: true, label: "RBAC", scope: "backend" },
    { ok: true, label: en ? "Rate limiting" : "Rate limiting", scope: "backend" },
    { ok: true, label: en ? "Input validation" : "Validação de entrada", scope: "backend" },
    { ok: true, label: en ? "Security headers (CSP/HSTS)" : "Cabeçalhos de segurança (CSP/HSTS)", scope: "backend" },
    { ok: true, label: en ? "Webhook signature (HMAC)" : "Assinatura de webhook (HMAC)", scope: "backend" },
    { ok: true, label: en ? "Audit trail" : "Trilha de auditoria", scope: "backend" },
    { ok: true, label: en ? "Secrets fail-fast in production" : "Segredos: falha rápida em produção", scope: "backend" },
    { ok: pinOn, label: en ? "PIN lock (this device)" : "Trava por PIN (este aparelho)", scope: "site" },
  ];
  const savePin = async () => { if (pin.length >= 4) { await setPin(pin); setPinOn(true); setPinVal(""); } };
  const removePin = () => { clearPin(); setPinOn(false); };
  const wipe = () => { clearData(); location.reload(); };
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>🔒 {t(lang, "security")}</h2>
          <button className="ghost" onClick={onClose}>{t(lang, "close")}</button>
        </div>
        <p className="muted">{t(lang, "securitySub")}</p>
        <div className="ctrl-list">
          {controls.map((c) => (
            <div key={c.label} className="ctrl">
              <span className={c.ok ? "ctrl-ok" : "ctrl-off"}>{c.ok ? "✅" : "⬜"}</span>
              <span className="ctrl-label">{c.label}</span>
              <span className="ctrl-scope">{c.scope}</span>
            </div>
          ))}
        </div>

        <div className="tasks-head"><h3>🔐 {t(lang, "pinLock")}</h3></div>
        {pinOn ? (
          <button className="insight-run" onClick={removePin}>{t(lang, "pinRemove")}</button>
        ) : (
          <div className="pin-row">
            <input className="pin-input small" type="password" inputMode="numeric" placeholder="••••" value={pin} onChange={(e) => setPinVal(e.target.value)} />
            <button className="insight-run" onClick={savePin}>{t(lang, "pinSet")}</button>
          </div>
        )}

        <div className="sec-note">{t(lang, "dataNote")}</div>
        <button className="danger-btn" onClick={wipe}>{t(lang, "clearData")}</button>
      </div>
    </div>
  );
}

function InsightsPanel({ lang, insights, tasks, onClose, onRun, onRefresh }: {
  lang: Lang; insights: Insight[]; tasks: Task[]; onClose: () => void; onRun: (cmd: string) => void; onRefresh: () => void;
}) {
  const [autoOn, setAutoOn] = useState(getAutomationsOn());
  const icon: Record<string, string> = { critical: "🔴", warn: "🟡", info: "🔵", success: "🟢" };
  const toggleAuto = () => { const v = !autoOn; setAutomationsOn(v); setAutoOn(v); onRefresh(); };
  const complete = (id: string) => { setTaskDone(id); onRefresh(); };
  const dueLabel = (iso: string) => new Date(iso).toLocaleDateString(lang === "en" ? "en-US" : "pt-BR");
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>💡 {t(lang, "insights")}</h2>
          <button className="ghost" onClick={onClose}>{t(lang, "close")}</button>
        </div>
        <p className="muted">{t(lang, "insightsSub")}</p>
        <div className="insights">
          {insights.map((i) => (
            <div key={i.id} className={`insight sev-${i.severity}`}>
              <div className="insight-title">{icon[i.severity]} {i.title}</div>
              <div className="insight-msg">{i.message}</div>
              {i.suggestion && (
                <button className="insight-run" onClick={() => onRun(i.suggestion!)}>
                  ▶ {t(lang, "resolve")}: <em>{i.suggestion}</em>
                </button>
              )}
            </div>
          ))}
        </div>

        <div className="tasks-head">
          <h3>⚙️ {t(lang, "tasks")}</h3>
          <label className="auto-toggle">
            <input type="checkbox" checked={autoOn} onChange={toggleAuto} /> {t(lang, "automations")}
          </label>
        </div>
        {tasks.length === 0 && <p className="muted small">{t(lang, "noTasks")}</p>}
        <div className="tasks">
          {tasks.map((tk) => (
            <div key={tk.id} className="task">
              <input type="checkbox" onChange={() => complete(tk.id)} />
              <div className="task-body">
                <div className="task-title">{tk.title}</div>
                <div className="task-due">📅 {dueLabel(tk.due)} · {tk.source}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const COUNTRIES = ["BR", "US", "GB", "DE", "IN", "MX", "JP", "NG"];

function Pricing({ lang, onClose }: { lang: Lang; onClose: () => void }) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [country, setCountry] = useState("BR");
  const [planId, setPlanId] = useState("starter");
  const [methods, setMethods] = useState<{ id: string; label: string }[]>([]);
  const [method, setMethod] = useState("pix");
  const [quote, setQuote] = useState<Quote | null>(null);

  useEffect(() => { getPlans().then((r) => setPlans(r.plans)); }, []);
  useEffect(() => {
    getOptions(country).then((o) => {
      setMethods(o.methods);
      if (!o.methods.find((m) => m.id === method)) setMethod(o.methods[0]?.id ?? "card");
    });
  }, [country]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (planId && method) getQuote({ planId, country, method }).then(setQuote).catch(() => setQuote(null));
  }, [planId, country, method]);

  const fmt = (a: number, c: string) =>
    ["BTC", "ETH", "USDT", "USDC"].includes(c) ? `${a} ${c}` : `${a.toLocaleString()} ${c}`;

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{t(lang, "pricing")}</h2>
          <button className="ghost" onClick={onClose}>{t(lang, "close")}</button>
        </div>
        <p className="muted">{t(lang, "globalNote")}</p>

        <div className="plans">
          {plans.map((p) => (
            <button
              key={p.id}
              className={`plan ${p.highlighted ? "hot" : ""} ${planId === p.id ? "sel" : ""}`}
              onClick={() => setPlanId(p.id)}
            >
              <div className="plan-name">{p.name}</div>
              <div className="plan-price">
                {p.basePriceMonthly === 0 ? "—" : `${t(lang, "from")} ${p.basePriceMonthly}`}{p.basePriceMonthly ? t(lang, "perMonth") : ""}
              </div>
              <ul>{p.features.map((f) => <li key={f}>{f}</li>)}</ul>
            </button>
          ))}
        </div>

        <div className="pay-row">
          <label>
            {t(lang, "chooseCountry")}
            <select value={country} onChange={(e) => setCountry(e.target.value)}>
              {COUNTRIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label>
            {t(lang, "method")}
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              {methods.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
        </div>

        {quote && (
          <div className="quote">
            <div className="quote-box">
              <div className="q-label">{t(lang, "youPay")}</div>
              <div className="q-val">{fmt(quote.payin.amount, quote.payin.currency)}</div>
            </div>
            <div className="q-arrow">→</div>
            <div className="quote-box owner">
              <div className="q-label">{t(lang, "merchantReceives")}</div>
              <div className="q-val">{fmt(quote.payout.amount, quote.payout.currency)}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function DataCard({ kind, title, payload }: { kind: string; title: string; payload: unknown }) {
  return (
    <div className="card">
      <div className="card-title">{title}</div>
      {kind === "kpi" && <KpiView payload={payload as Record<string, number>} />}
      {kind === "deliberation" && <Deliberation payload={payload as DeliberationPayload} />}
      {kind === "insights" && (
        <div className="insights">
          {(payload as Insight[]).map((i) => (
            <div key={i.id} className={`insight sev-${i.severity}`}>
              <div className="insight-title">{{ critical: "🔴", warn: "🟡", info: "🔵", success: "🟢" }[i.severity]} {i.title}</div>
              <div className="insight-msg">{i.message}</div>
            </div>
          ))}
        </div>
      )}
      {(kind === "table" || kind === "customer" || kind === "proposal" || kind === "entry" || kind === "lead" || kind === "pipeline") && (
        <pre className="json">{JSON.stringify(payload, null, 2)}</pre>
      )}
    </div>
  );
}

function KpiView({ payload }: { payload: Record<string, number> }) {
  const labels: Record<string, string> = { income: "Receita", expense: "Despesa", profit: "Lucro", balance: "Saldo" };
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
          <li key={i}><strong>{o.agent}</strong> <em>({o.vote})</em>: {o.argument}</li>
        ))}
      </ul>
      <div className="muted">{payload.rationale}</div>
    </div>
  );
}
