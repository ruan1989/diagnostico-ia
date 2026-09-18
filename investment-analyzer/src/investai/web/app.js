/* Painel InvestAI — sem dependências externas.
 *
 * O token da API fica em sessionStorage (não localStorage): fechar a aba
 * descarta. Nenhum segredo de API trafega de volta do servidor.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const CHAVE_TOKEN = "investai_token";

let ultimoScan = null;
let configApp = null;

/* ------------------------------------------------------------- utilidades */
function token() {
  return ($("token").value || "").trim();
}

function guardarToken() {
  try { sessionStorage.setItem(CHAVE_TOKEN, token()); } catch (_) { /* ignora */ }
}

function restaurarToken() {
  try {
    const t = sessionStorage.getItem(CHAVE_TOKEN);
    if (t) $("token").value = t;
  } catch (_) { /* sessionStorage pode estar bloqueado */ }
}

async function api(caminho, opcoes = {}) {
  const cabecalhos = { "Content-Type": "application/json" };
  if (token()) cabecalhos["X-API-Token"] = token();
  const resp = await fetch(caminho, { ...opcoes, headers: cabecalhos });
  let corpo = null;
  try { corpo = await resp.json(); } catch (_) { corpo = null; }
  if (!resp.ok) {
    const detalhe = (corpo && (corpo.detail || corpo.mensagem)) || `HTTP ${resp.status}`;
    throw new Error(typeof detalhe === "string" ? detalhe : JSON.stringify(detalhe));
  }
  return corpo;
}

const fmtUsd = (v) => (v === null || v === undefined || isNaN(v) ? "—"
  : (v < 0 ? "-" : "") + "US$ " + Math.abs(v).toLocaleString("pt-BR",
      { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

const fmtBrl = (v) => (v === null || v === undefined || isNaN(v) ? "—"
  : "R$ " + Number(v).toLocaleString("pt-BR",
      { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

const fmtPct = (v, d = 1) => (v === null || v === undefined || isNaN(v) ? "—"
  : Number(v).toFixed(d) + "%");

function fmtPreco(v) {
  if (v === null || v === undefined || isNaN(v)) return "—";
  const n = Number(v);
  const casas = n >= 1000 ? 2 : n >= 1 ? 4 : 6;
  return n.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
}

function fmtCompacto(v) {
  if (!v) return "—";
  const n = Number(v);
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " bi";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + " mi";
  if (n >= 1e3) return (n / 1e3).toFixed(0) + " mil";
  return n.toFixed(0);
}

const fmtData = (ms) => (!ms ? "—"
  : new Date(Number(ms)).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }));

/** Escapa texto antes de inserir no DOM. Detalhes de fator e nomes de fundo
 *  vêm de arquivo de configuração do usuário — não confiar cegamente. */
function esc(s) {
  const d = document.createElement("div");
  d.textContent = s === null || s === undefined ? "" : String(s);
  return d.innerHTML;
}

function mostrarMsg(id, texto, ok = true) {
  const el = $(id);
  el.textContent = texto;
  el.className = "msg visivel " + (ok ? "msg-ok" : "msg-erro");
  if (ok) setTimeout(() => { el.className = "msg"; }, 9000);
}

const REGIME_LABEL = {
  tendencia_alta: "tendência de alta",
  tendencia_baixa: "tendência de baixa",
  lateral: "lateral",
  volatil_sem_direcao: "volátil s/ direção",
};

/* ------------------------------------------------------------------ abas */
document.querySelectorAll("nav button").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((x) => x.classList.remove("ativo"));
    document.querySelectorAll(".aba").forEach((x) => x.classList.remove("ativa"));
    b.classList.add("ativo");
    $("aba-" + b.dataset.aba).classList.add("ativa");
    if (b.dataset.aba === "renda") carregarFiis();
    if (b.dataset.aba === "historico") carregarHistorico();
    if (b.dataset.aba === "operacao") carregarStatus();
  });
});

/* --------------------------------------------------------- cartão de sinal */
function cardSinal(s) {
  const lado = s.side === "long" ? "compra" : "venda";
  const alvos = (s.take_profits || []).map((t, i) =>
    `<div class="plano-item"><div class="plano-label">Alvo ${i + 1}</div>
      <div class="plano-valor pos">${fmtPreco(t)}</div></div>`).join("");

  const fatores = (s.fatores || []).map((f) => {
    const largura = Math.min(Math.abs(f.valor) * 50, 50);
    const lateral = f.valor >= 0 ? `left:50%;width:${largura}%` : `right:50%;width:${largura}%`;
    return `<div class="fator">
        <div class="fator-nome">${esc(f.nome.replace(/_/g, " "))}</div>
        <div class="fator-barra"><i class="fator-fill ${f.valor >= 0 ? "pos" : "neg"}" style="${lateral}"></i></div>
        <div class="fator-valor ${f.valor >= 0 ? "pos" : "neg"}">${f.valor >= 0 ? "+" : ""}${f.valor.toFixed(2)}</div>
        <div class="fator-detalhe">${esc(f.detalhe)}</div>
      </div>`;
  }).join("");

  const h = s.historico;
  const histTexto = h && h.trades
    ? `${h.trades} operações medidas · acerto ${fmtPct(h.win_rate * 100)} ·
       PF ${h.profit_factor.toFixed(2)} · expectativa ${h.expectancy_r >= 0 ? "+" : ""}${h.expectancy_r.toFixed(2)}R ·
       pior sequência ${h.consecutive_losses} perdas`
    : "sem histórico medido para este par e direção";

  const notas = (s.notas || []).length
    ? `<div class="notas">${s.notas.map((n) => `<div class="nota">⚠ ${esc(n)}</div>`).join("")}</div>`
    : "";

  return `<div class="card sinal" data-grade="${esc(s.grade)}">
    <div class="sinal-topo">
      <div class="sinal-symbol">${esc(s.symbol)}</div>
      <span class="chip chip-${s.side}">${lado}</span>
      <span class="chip chip-${esc(s.grade)}">grade ${esc(s.grade)}</span>
      <span class="chip chip-regime">${esc(REGIME_LABEL[s.regime] || s.regime)}</span>
      <div class="spacer"></div>
      <div class="mono" style="font-size:15px;font-weight:700">${s.score.toFixed(1)}</div>
    </div>

    <div class="plano">
      <div class="plano-item"><div class="plano-label">Entrada</div>
        <div class="plano-valor">${fmtPreco(s.entry)}</div></div>
      <div class="plano-item"><div class="plano-label">Stop (−1R)</div>
        <div class="plano-valor neg">${fmtPreco(s.stop_loss)}</div></div>
      <div class="plano-item"><div class="plano-label">Risco no stop</div>
        <div class="plano-valor">${fmtPct(s.stop_distance_pct, 2)}</div></div>
      ${alvos}
    </div>

    <div class="grid" style="grid-template-columns:1fr 1fr;gap:8px;margin:10px 0">
      <div class="plano-item"><div class="plano-label">Probabilidade estimada</div>
        <div class="plano-valor">${fmtPct(s.prob_acerto_estimada * 100)}</div></div>
      <div class="plano-item"><div class="plano-label">Retorno esperado</div>
        <div class="plano-valor ${s.retorno_esperado_r >= 0 ? "pos" : "neg"}">
          ${s.retorno_esperado_r >= 0 ? "+" : ""}${s.retorno_esperado_r.toFixed(2)}R</div></div>
    </div>

    <div class="card-titulo" style="margin-top:6px">Histórico que sustenta a estimativa</div>
    <div class="dim" style="font-size:11.5px">${esc(histTexto)}</div>

    <div class="card-titulo" style="margin-top:12px">Decomposição do score</div>
    <div class="fatores">${fatores}</div>

    <div class="invalidacao">${esc(s.invalidacao)}</div>
    ${notas}
  </div>`;
}

/* ----------------------------------------------------------------- scan */
async function varrer(comHistorico = true) {
  const btn = comHistorico ? $("btn-scan") : $("btn-scan-rapido");
  const rotulo = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<i class="spin"></i>varrendo…';
  $("lista-operaveis").innerHTML = '<div class="carregando"><i class="spin"></i>analisando o universo…</div>';
  try {
    const dados = await api(`/api/scan?com_historico=${comHistorico}`);
    ultimoScan = dados;
    renderScan(dados);
  } catch (e) {
    $("lista-operaveis").innerHTML = `<div class="vazio neg">Falha na varredura: ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = rotulo;
  }
}

function renderScan(d) {
  $("kpi-operaveis").textContent = d.total_operavel;
  $("kpi-observacao").textContent = (d.observacao || []).length;
  $("kpi-analisados").textContent = d.total_analisado;
  $("kpi-duracao").textContent = `varredura em ${d.duracao_s}s`;

  const melhor = (d.operaveis || [])[0];
  const kpiExp = $("kpi-expectativa");
  if (melhor) {
    kpiExp.textContent = (melhor.retorno_esperado_r >= 0 ? "+" : "") +
      melhor.retorno_esperado_r.toFixed(2) + "R";
    kpiExp.className = "kpi-valor " + (melhor.retorno_esperado_r >= 0 ? "pos" : "neg");
  } else {
    kpiExp.textContent = "—";
    kpiExp.className = "kpi-valor dim";
  }

  $("cont-operaveis").textContent = (d.operaveis || []).length;
  $("cont-observacao").textContent = (d.observacao || []).length;

  $("lista-operaveis").innerHTML = (d.operaveis || []).length
    ? d.operaveis.map(cardSinal).join("")
    : `<div class="vazio">Nenhuma oportunidade passou nos dois portões agora.
        Isso é um resultado válido — significa que o sistema não encontrou
        evidência suficiente para arriscar capital. Veja a seção de
        observação abaixo.</div>`;

  $("lista-observacao").innerHTML = (d.observacao || []).length
    ? d.observacao.slice(0, 12).map(cardSinal).join("")
    : '<div class="vazio">—</div>';

  renderMatriz(d.analises || []);
}

function renderMatriz(analises) {
  const tb = $("tbody-matriz");
  if (!analises.length) {
    tb.innerHTML = '<tr><td colspan="11" class="vazio">sem dados</td></tr>';
    return;
  }
  tb.innerHTML = analises.map((a) => {
    if (a.erro) {
      return `<tr><td class="mono">${esc(a.symbol)}</td>
        <td colspan="10" class="faint">${esc(a.erro)}</td></tr>`;
    }
    const sl = a.sinal_long, ss = a.sinal_short;
    const melhor = a.melhor;
    const barra = (v) => `<span class="barra-score"><i style="width:${Math.max(0, Math.min(100, v))}%"></i></span>`;
    const chipMelhor = melhor
      ? `<span class="chip chip-${esc(melhor.grade)}">${esc(melhor.side === "long" ? "compra" : "venda")} · ${esc(melhor.grade)}</span>`
      : "—";
    return `<tr>
      <td class="mono"><strong>${esc(a.symbol)}</strong></td>
      <td class="num">${fmtPreco(a.preco)}</td>
      <td class="faint" style="text-align:right">${esc(REGIME_LABEL[a.regime] || a.regime)}</td>
      <td class="num">${a.rsi.toFixed(0)}</td>
      <td class="num">${a.adx.toFixed(0)}</td>
      <td class="num">${fmtPct(a.atr_pct, 2)}</td>
      <td class="num faint">${fmtCompacto(a.volume_24h_usd)}</td>
      <td class="num ${a.funding_rate >= 0 ? "dim" : "warn"}">${(a.funding_rate * 100).toFixed(4)}%</td>
      <td class="num">${sl ? barra(sl.score) + sl.score.toFixed(1) : "—"}</td>
      <td class="num">${ss ? barra(ss.score) + ss.score.toFixed(1) : "—"}</td>
      <td>${chipMelhor}</td>
    </tr>`;
  }).join("");
}

/* ------------------------------------------------------------------ FIIs */
async function carregarFiis() {
  const tb = $("tbody-fiis");
  tb.innerHTML = '<tr><td colspan="11" class="carregando"><i class="spin"></i>carregando…</td></tr>';
  try {
    const d = await api("/api/fiis");
    const f = d.fonte || {};
    const desatualizado = f.desatualizado;
    $("aviso-fii").className = "aviso" + (desatualizado ? " aviso-critico" : "");
    $("fii-fonte-texto").innerHTML = desatualizado
      ? `Os fundamentais vêm de <code>data/fiis_snapshot.json</code>, atualizado
         em <strong>${esc(f.atualizado_em || "data desconhecida")}</strong>
         (${f.idade_dias === null || f.idade_dias === undefined ? "?" : f.idade_dias} dias atrás).
         <strong>Esses números estão desatualizados e servem só para exercitar o
         sistema.</strong> Antes de qualquer aporte, substitua cada campo pelos
         dados do relatório gerencial do fundo e dos informes na B3.`
      : `Snapshot de <strong>${esc(f.atualizado_em)}</strong> (${f.idade_dias} dias).
         Confirme sempre no relatório gerencial antes de aportar.`;

    $("cont-fiis").textContent = d.total;
    tb.innerHTML = d.fundos.map((x) => {
      const cls = x.score >= 75 ? "pos" : x.score >= 62 ? "" : x.score >= 50 ? "warn" : "neg";
      return `<tr>
        <td class="mono"><strong>${esc(x.ticker)}</strong>
          <div class="faint" style="font-size:10.5px">${esc(x.nome)}</div></td>
        <td class="faint" style="text-align:right">${esc(x.segmento)}</td>
        <td class="num ${cls}"><strong>${x.score.toFixed(1)}</strong></td>
        <td class="${cls}" style="text-align:right;font-size:12px">${esc(x.classificacao)}</td>
        <td class="num">${fmtBrl(x.preco)}</td>
        <td class="num">${fmtPct(x.dy_12m, 2)}</td>
        <td class="num">${x.p_vp.toFixed(2)}</td>
        <td class="num">${x.vacancia_pct === null ? '<span class="faint">n/a</span>' : fmtPct(x.vacancia_pct)}</td>
        <td class="num faint">${fmtCompacto(x.liquidez_diaria)}</td>
        <td class="num">${fmtBrl(x.renda_mensal_por_1k)}</td>
        <td style="text-align:right">${x.alertas.length
          ? `<span class="chip chip-c" title="${esc(x.alertas.join(" | "))}">${x.alertas.length} alerta${x.alertas.length > 1 ? "s" : ""}</span>`
          : '<span class="faint">—</span>'}</td>
      </tr>`;
    }).join("");

    const comAlerta = d.fundos.filter((x) => x.alertas.length);
    $("detalhe-fii").innerHTML = comAlerta.length
      ? `<div class="secao-titulo">Por que cada alerta foi levantado</div>
         <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(320px,1fr))">
         ${comAlerta.map((x) => `<div class="card">
            <div class="sinal-topo"><div class="sinal-symbol">${esc(x.ticker)}</div>
              <span class="chip chip-regime">${x.score.toFixed(1)}</span></div>
            ${x.alertas.map((a) => `<div class="nota">⚠ ${esc(a)}</div>`).join("")}
          </div>`).join("")}</div>`
      : "";
  } catch (e) {
    tb.innerHTML = `<tr><td colspan="11" class="vazio neg">${esc(e.message)}</td></tr>`;
  }
}

async function montarCarteira() {
  const btn = $("btn-carteira");
  btn.disabled = true;
  try {
    const d = await api("/api/fiis/carteira", {
      method: "POST",
      body: JSON.stringify({
        capital: Number($("fii-capital").value),
        max_por_fundo_pct: Number($("fii-maxfundo").value),
        min_score: Number($("fii-minscore").value),
        max_fundos: Number($("fii-maxfundos").value),
      }),
    });
    if (!d.itens.length) {
      $("resultado-carteira").innerHTML =
        `<div class="aviso" style="margin-top:14px"><strong>Nenhum fundo elegível.</strong>
          ${esc(d.aviso || "")}</div>`;
      return;
    }
    $("resultado-carteira").innerHTML = `
      <div class="grid grid-kpi" style="margin-top:14px">
        <div class="card"><div class="card-titulo">Investido</div>
          <div class="kpi-valor">${fmtBrl(d.investido)}</div>
          <div class="kpi-sub">sobra em caixa ${fmtBrl(d.sobra_caixa)}</div></div>
        <div class="card"><div class="card-titulo">Renda mensal estimada</div>
          <div class="kpi-valor pos">${fmtBrl(d.renda_mensal_estimada)}</div>
          <div class="kpi-sub">${fmtBrl(d.renda_anual_estimada)} por ano</div></div>
        <div class="card"><div class="card-titulo">DY médio ponderado</div>
          <div class="kpi-valor">${fmtPct(d.dy_medio_ponderado, 2)}</div>
          <div class="kpi-sub">com base nos últimos 12 meses</div></div>
      </div>
      <div class="tabela-wrap" style="margin-top:14px">
        <table><thead><tr>
          <th>Ticker</th><th>Segmento</th><th>Peso</th><th>Cotas</th>
          <th>Investido</th><th>Renda/mês</th><th>DY</th><th>P/VP</th><th>Score</th>
        </tr></thead><tbody>
        ${d.itens.map((i) => `<tr>
          <td class="mono"><strong>${esc(i.ticker)}</strong></td>
          <td class="faint" style="text-align:right">${esc(i.segmento)}</td>
          <td class="num">${fmtPct(i.peso_pct, 2)}</td>
          <td class="num">${i.cotas}</td>
          <td class="num">${fmtBrl(i.valor_investido)}</td>
          <td class="num pos">${fmtBrl(i.renda_mensal_estimada)}</td>
          <td class="num">${fmtPct(i.dy_12m, 2)}</td>
          <td class="num">${i.p_vp.toFixed(2)}</td>
          <td class="num">${i.score}</td>
        </tr>`).join("")}
        </tbody></table>
      </div>
      <div class="aviso" style="margin-top:14px">${esc(d.aviso)}</div>`;
  } catch (e) {
    mostrarMsg("msg-bitget", e.message, false);
  } finally {
    btn.disabled = false;
  }
}

/* ---------------------------------------------------------------- status */
async function carregarStatus() {
  try {
    const s = await api("/api/status");
    const m = s.motor, r = s.risco, c = s.conexao;

    const bm = $("badge-modo");
    bm.textContent = m.modo === "live" ? "dinheiro real" : "simulação";
    bm.className = "badge " + (m.modo === "live" ? "badge-live" : "badge-paper");

    const bmo = $("badge-motor");
    bmo.innerHTML = `<i class="dot ${m.rodando ? "dot-pulse" : ""}"></i>` +
      (m.rodando ? "motor ativo" : "motor parado");
    bmo.className = "badge " + (m.rodando ? "badge-on" : "badge-off");

    const bc = $("badge-conexao");
    bc.innerHTML = '<i class="dot"></i>' + (c.conectado ? `chave ${esc(c.api_key)}` : "sem chave");
    bc.className = "badge " + (c.conectado && !c.erro ? "badge-on" : c.erro ? "badge-warn" : "badge-off");

    $("badge-kill").style.display = r.kill_switch ? "inline-flex" : "none";
    $("badge-kill").textContent = "kill switch: " + (r.motivo_kill || "ativo");

    const lim = s.limites || {};
    $("painel-risco").innerHTML = `
      <div class="grid" style="grid-template-columns:1fr 1fr;gap:8px">
        <div class="plano-item"><div class="plano-label">Capital</div>
          <div class="plano-valor">${fmtUsd(r.capital_atual)}</div></div>
        <div class="plano-item"><div class="plano-label">Drawdown</div>
          <div class="plano-valor ${r.drawdown_pct > lim.drawdown_max_pct * 0.6 ? "neg" : ""}">
            ${fmtPct(r.drawdown_pct, 2)} <span class="faint">/ ${fmtPct(lim.drawdown_max_pct, 0)}</span></div></div>
        <div class="plano-item"><div class="plano-label">Resultado do dia</div>
          <div class="plano-valor ${r.pnl_dia >= 0 ? "pos" : "neg"}">${fmtUsd(r.pnl_dia)}</div></div>
        <div class="plano-item"><div class="plano-label">Perdas seguidas</div>
          <div class="plano-valor ${r.perdas_consecutivas >= lim.perdas_consecutivas_max ? "neg" : ""}">
            ${r.perdas_consecutivas} <span class="faint">/ ${lim.perdas_consecutivas_max}</span></div></div>
        <div class="plano-item"><div class="plano-label">Risco por operação</div>
          <div class="plano-valor">${fmtPct(lim.risco_por_trade_pct, 2)}</div></div>
        <div class="plano-item"><div class="plano-label">Ordens enviadas / bloqueadas</div>
          <div class="plano-valor">${m.ordens_enviadas} <span class="faint">/ ${m.ordens_bloqueadas}</span></div></div>
      </div>`;

    $("cont-posicoes").textContent = s.posicoes_abertas;
    const tb = $("tbody-posicoes");
    tb.innerHTML = s.posicoes.length ? s.posicoes.map((p) => `<tr>
        <td class="mono"><strong>${esc(p.symbol)}</strong></td>
        <td style="text-align:right"><span class="chip chip-${p.side}">${p.side === "long" ? "compra" : "venda"}</span></td>
        <td class="num">${p.size.toFixed(6)}</td>
        <td class="num">${fmtPreco(p.entry)}</td>
        <td class="num neg">${fmtPreco(p.stop_loss)}${p.trailing_ativo ? ' <span class="chip chip-a">BE</span>' : ""}</td>
        <td class="num faint">${(p.take_profits || []).map(fmtPreco).join(" · ")}</td>
        <td class="num">${fmtUsd(p.notional_usd)}</td>
        <td class="num">${fmtUsd(p.risk_usd)}</td>
        <td class="num">${p.tps_atingidos}</td>
        <td class="faint" style="text-align:right">${esc(p.modo)}</td>
        <td><button class="btn btn-perigo" data-fechar="${esc(p.symbol)}">Fechar</button></td>
      </tr>`).join("")
      : '<tr><td colspan="11" class="vazio">nenhuma posição aberta</td></tr>';

    tb.querySelectorAll("[data-fechar]").forEach((b) => {
      b.addEventListener("click", () => fecharPosicao(b.dataset.fechar));
    });
  } catch (e) {
    console.warn("status indisponível:", e.message);
  }
}

/* ------------------------------------------------------------- histórico */
async function carregarHistorico() {
  try {
    const d = await api("/api/trades?limite=200");
    const r = d.resumo;
    $("h-trades").textContent = r.trades;
    $("h-winrate").textContent = r.trades ? fmtPct(r.win_rate * 100) : "—";
    const pnl = $("h-pnl");
    pnl.textContent = fmtUsd(r.pnl_usd);
    pnl.className = "kpi-valor " + (r.pnl_usd >= 0 ? "pos" : "neg");
    $("h-taxas").textContent = `taxas pagas ${fmtUsd(r.taxas_usd)}`;
    $("h-pf").textContent = r.trades ? r.profit_factor.toFixed(2) : "—";
    $("h-exp").textContent = r.trades
      ? `expectativa ${r.expectancy_r >= 0 ? "+" : ""}${r.expectancy_r.toFixed(3)}R` : "—";

    desenharEquity(d.curva_capital || []);

    $("tbody-trades").innerHTML = d.trades.length ? d.trades.map((t) => `<tr>
        <td class="mono"><strong>${esc(t.symbol)}</strong></td>
        <td style="text-align:right"><span class="chip chip-${t.side}">${t.side === "long" ? "compra" : "venda"}</span></td>
        <td class="num">${fmtPreco(t.entry)}</td>
        <td class="num">${fmtPreco(t.exit)}</td>
        <td class="num ${t.pnl_usd >= 0 ? "pos" : "neg"}">${fmtUsd(t.pnl_usd)}</td>
        <td class="num ${t.pnl_r >= 0 ? "pos" : "neg"}">${t.pnl_r >= 0 ? "+" : ""}${t.pnl_r.toFixed(2)}R</td>
        <td class="faint" style="text-align:right">${esc(t.motivo_saida)}</td>
        <td class="num faint">${fmtUsd(t.fees_usd)}</td>
        <td class="faint" style="text-align:right">${fmtData(t.closed_at)}</td>
        <td class="faint" style="text-align:right">${esc(t.modo)}</td>
      </tr>`).join("")
      : '<tr><td colspan="10" class="vazio">nenhuma operação registrada</td></tr>';
  } catch (e) {
    console.warn(e);
  }

  try {
    const ev = await api("/api/eventos?limite=60");
    $("lista-eventos").innerHTML = ev.eventos.length
      ? ev.eventos.map((e) => `<div class="evento">
          <div class="evento-ts">${fmtData(e.ts)}</div>
          <div class="nivel-${esc(e.nivel)}">${esc(e.nivel)}</div>
          <div><strong class="faint">${esc(e.origem)}</strong> ${esc(e.mensagem)}</div>
        </div>`).join("")
      : '<div class="vazio">nenhum evento</div>';
  } catch (e) {
    console.warn(e);
  }
}

function desenharEquity(curva) {
  const svg = $("svg-equity");
  if (curva.length < 2) {
    svg.innerHTML = '<text x="400" y="30" fill="#5d6b7f" font-size="11" text-anchor="middle">' +
      "sem operações suficientes para traçar a curva</text>";
    return;
  }
  const vals = curva.map((p) => p.equity);
  const min = Math.min(...vals), max = Math.max(...vals);
  const amp = max - min || 1;
  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * 800;
    const y = 48 - ((v - min) / amp) * 44;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const sobe = vals[vals.length - 1] >= vals[0];
  const cor = sobe ? "#2ecc8f" : "#ff5c6c";
  svg.innerHTML = `
    <polyline points="${pts}" fill="none" stroke="${cor}" stroke-width="1.8"
      vector-effect="non-scaling-stroke"/>
    <polyline points="0,52 ${pts} 800,52" fill="${cor}" opacity="0.1" stroke="none"/>`;
}

/* ------------------------------------------------------------- ações API */
async function acao(caminho, corpo, idMsg) {
  try {
    const d = await api(caminho, {
      method: corpo === null ? "POST" : "POST",
      body: corpo === null ? undefined : JSON.stringify(corpo),
    });
    mostrarMsg(idMsg, d.mensagem || "ok", true);
    await carregarStatus();
    return d;
  } catch (e) {
    mostrarMsg(idMsg, e.message, false);
    return null;
  }
}

async function fecharPosicao(symbol) {
  if (!confirm(`Fechar a posição em ${symbol} a mercado?`)) return;
  await acao("/api/motor/fechar", { symbol, fracao: 1.0 }, "msg-risco");
}

/* ----------------------------------------------------------- listeners */
$("btn-scan").addEventListener("click", () => varrer(true));
$("btn-scan-rapido").addEventListener("click", () => varrer(false));
$("btn-fiis").addEventListener("click", carregarFiis);
$("btn-carteira").addEventListener("click", montarCarteira);
$("token").addEventListener("change", guardarToken);

$("btn-conectar").addEventListener("click", async () => {
  const corpo = {
    api_key: $("bg-key").value.trim(),
    api_secret: $("bg-secret").value.trim(),
    passphrase: $("bg-pass").value.trim(),
    senha_mestra: $("bg-master").value,
    salvar: true,
  };
  if (!corpo.api_key || !corpo.api_secret || !corpo.passphrase) {
    mostrarMsg("msg-bitget", "preencha key, secret e passphrase", false);
    return;
  }
  if (corpo.senha_mestra.length < 8) {
    mostrarMsg("msg-bitget", "a senha mestra precisa de pelo menos 8 caracteres", false);
    return;
  }
  try {
    const d = await api("/api/bitget/conectar", { method: "POST", body: JSON.stringify(corpo) });
    const c = d.conexao || {};
    mostrarMsg("msg-bitget", c.ok
      ? `chave ${c.api_key} validada · saldo ${fmtUsd(c.saldo_usdt)} · ${d.aviso}`
      : `chave salva, mas a validação falhou: ${c.erro}`, !!c.ok);
    // Limpa os campos sensíveis do DOM depois de enviar.
    $("bg-secret").value = ""; $("bg-pass").value = ""; $("bg-master").value = "";
    await carregarStatus();
  } catch (e) {
    mostrarMsg("msg-bitget", e.message, false);
  }
});

$("btn-destravar").addEventListener("click", async () => {
  const senha = $("bg-master").value;
  if (senha.length < 8) {
    mostrarMsg("msg-bitget", "informe a senha mestra usada ao salvar", false);
    return;
  }
  try {
    const d = await api("/api/bitget/destravar", {
      method: "POST", body: JSON.stringify({ senha_mestra: senha }),
    });
    const c = d.conexao || {};
    mostrarMsg("msg-bitget", c.ok
      ? `chave ${d.api_key} destravada · saldo ${fmtUsd(c.saldo_usdt)}`
      : `destravada, mas a validação falhou: ${c.erro}`, !!c.ok);
    $("bg-master").value = "";
    await carregarStatus();
  } catch (e) {
    mostrarMsg("msg-bitget", e.message, false);
  }
});

$("btn-apagar-cred").addEventListener("click", async () => {
  if (!confirm("Apagar a chave de API salva neste computador? O motor volta para simulação.")) return;
  try {
    await api("/api/bitget/credenciais", { method: "DELETE" });
    mostrarMsg("msg-bitget", "chave apagada; motor em simulação", true);
    await carregarStatus();
  } catch (e) {
    mostrarMsg("msg-bitget", e.message, false);
  }
});

$("btn-motor-iniciar").addEventListener("click", () => acao("/api/motor/iniciar", null, "msg-motor"));
$("btn-motor-parar").addEventListener("click", () => acao("/api/motor/parar", null, "msg-motor"));
$("btn-motor-ciclo").addEventListener("click", () => acao("/api/motor/ciclo", null, "msg-motor"));
$("btn-desarmar").addEventListener("click", () => acao("/api/motor/desarmar-live", null, "msg-motor"));
$("btn-rearmar").addEventListener("click", () => acao("/api/risco/rearmar", null, "msg-risco"));

$("btn-armar").addEventListener("click", async () => {
  const frase = $("confirma-live").value;
  if (!confirm("A partir de agora o sistema enviará ORDENS REAIS na sua conta " +
               "Bitget, sem pedir confirmação a cada operação. Continuar?")) return;
  const d = await acao("/api/motor/armar-live", { confirmacao: frase }, "msg-motor");
  if (d) $("confirma-live").value = "";
});

$("btn-fechar-tudo").addEventListener("click", async () => {
  if (!confirm("Fechar TODAS as posições abertas a mercado agora?")) return;
  try {
    const d = await api("/api/motor/fechar-tudo", { method: "POST" });
    mostrarMsg("msg-risco", d.resultados.length
      ? d.resultados.join(" | ") : "nenhuma posição aberta", true);
    await carregarStatus();
  } catch (e) {
    mostrarMsg("msg-risco", e.message, false);
  }
});

/* ------------------------------------------------------------------ boot */
(async function iniciar() {
  restaurarToken();
  try {
    configApp = await api("/api/config");
    $("frase-live").textContent = configApp.confirmacao_live;
    if (configApp.conexao.modo_dados === "sintetico") {
      $("aviso-dados").innerHTML =
        "<br><br><strong>Modo de dados sintético ativo.</strong> Os preços na tela " +
        "são gerados por simulação, não são cotações da Bitget. Serve para " +
        "conhecer o sistema; desligue <code>INVESTAI_SYNTHETIC</code> para usar " +
        "dados reais de mercado.";
    }
    if (configApp.token_gerado_automaticamente) {
      $("aviso-dados").innerHTML += "<br><br>O token da API foi gerado no " +
        "boot e está no log do servidor. Cole-o no campo do topo para habilitar " +
        "os comandos de operação.";
    }
  } catch (e) {
    console.warn("config indisponível:", e.message);
  }
  await carregarStatus();
  setInterval(carregarStatus, 15000);
})();
