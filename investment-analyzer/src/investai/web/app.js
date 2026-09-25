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
    if (b.dataset.aba === "shadow") carregarShadow();
    if (b.dataset.aba === "travas") carregarTravas();
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
    // "aceita" e "validada contra a exchange" são estados diferentes. Dizer
    // "validada" sem ter consultado a Bitget seria a própria falha que este
    // sistema existe para não cometer.
    let texto;
    if (c.ok && c.validada) {
      texto = `chave ${c.api_key} validada na Bitget · saldo ${fmtUsd(c.saldo_usdt)} · ${d.aviso}`;
    } else if (c.ok) {
      texto = `chave ${c.api_key} armazenada, mas NÃO validada contra a Bitget`
        + ` — ${c.aviso || "a verificação não foi executada"}. ${d.aviso}`;
    } else {
      texto = `chave salva, mas a validação falhou: ${c.erro}`;
    }
    mostrarMsg("msg-bitget", texto, !!(c.ok && c.validada));
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
  const frase = ($("confirma-live").value || "").trim();
  // A checagem barata vem ANTES do diálogo. Na ordem inversa, quem digitasse
  // a frase errada ainda veria o alerta de "ORDENS REAIS", o aceitaria, e só
  // então seria recusado — treinando a pessoa a clicar em "OK" num aviso que
  // deveria ser raro. Um aviso que aparece à toa deixa de ser aviso.
  const esperada = (configApp && configApp.confirmacao_live) || "";
  if (frase.toUpperCase() !== esperada.toUpperCase()) {
    mostrarMsg("msg-motor",
      `modo real NÃO armado: digite exatamente "${esperada}" no campo de ` +
      `confirmação. O sistema continua em simulação.`, false);
    return;
  }
  if (!confirm("A partir de agora o sistema enviará ORDENS REAIS na sua conta " +
               "Bitget, sem pedir confirmação a cada operação. Continuar?")) {
    mostrarMsg("msg-motor",
      "modo real NÃO armado: você cancelou. O sistema continua em simulação.",
      false);
    return;
  }
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

/* =========================================================================
 * Abas acrescentadas na segunda entrega. Nenhum botão é decorativo: cada um
 * chama um endpoint real e renderiza o que voltou, incluindo as lacunas.
 * ========================================================================= */

/* ------------------------------------------------- ciclo completo */
async function rodarCicloCompleto() {
  const btn = $("btn-ciclo");
  const rotulo = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<i class="spin"></i>analisando…';
  $("lista-operaveis").innerHTML =
    '<div class="carregando"><i class="spin"></i>qualidade → regime → anomalias → agentes → consenso → risco…</div>';
  try {
    const d = await api("/api/ciclo");
    renderCiclo(d);
  } catch (e) {
    $("lista-operaveis").innerHTML =
      `<div class="vazio neg">Falha no ciclo: ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = rotulo;
  }
}

function cardConsenso(a) {
  const c = a.consenso;
  if (!c) {
    return `<div class="card sinal" data-grade="rejeitado">
      <div class="sinal-topo"><div class="sinal-symbol">${esc(a.symbol)}</div>
        <span class="chip chip-rejeitado">${esc(a.decisao)}</span></div>
      <div class="dim" style="font-size:12px">${esc(a.motivo || a.erro || "sem análise")}</div>
      <div class="faint" style="font-size:11px;margin-top:6px">parou em: ${esc(a.etapa_final)}</div>
    </div>`;
  }
  const grade = c.decisao === "validada_pelo_modelo" ? "A"
    : c.decisao === "aguardar_confirmacao" ? "B"
    : c.decisao === "observar" ? "C" : "rejeitado";

  const pareceres = (c.pareceres || []).map((p) => {
    if (!p.entra_no_calculo) {
      return `<div class="fator">
        <div class="fator-nome">${esc(p.agente)}</div>
        <div class="fator-barra"></div>
        <div class="fator-valor faint">${p.postura === "nao_aplicavel" ? "N/A" : "—"}</div>
        <div class="fator-detalhe faint">${esc(p.postura === "nao_aplicavel"
          ? "não se aplica a esta classe de ativo (peso redistribuído)"
          : "abstenção: " + ((p.dados_faltando || [])[0] || "fonte não configurada"))}</div>
      </div>`;
    }
    const largura = Math.min(Math.abs(p.valor) * 50, 50);
    const lateral = p.valor >= 0 ? `left:50%;width:${largura}%` : `right:50%;width:${largura}%`;
    return `<div class="fator">
      <div class="fator-nome">${esc(p.agente)}</div>
      <div class="fator-barra"><i class="fator-fill ${p.valor >= 0 ? "pos" : "neg"}" style="${lateral}"></i></div>
      <div class="fator-valor ${p.valor >= 0 ? "pos" : "neg"}">${p.valor >= 0 ? "+" : ""}${p.valor.toFixed(2)}</div>
      <div class="fator-detalhe">conf ${(p.confianca * 100).toFixed(0)}% · peso efetivo ${p.peso_efetivo.toFixed(3)}${
        (p.evidencias || [])[0] ? " · " + esc(p.evidencias[0]) : ""}</div>
    </div>`;
  }).join("");

  const contra = (c.fatores_contrarios || []).slice(0, 5)
    .map((x) => `<div class="nota">⚠ ${esc(x)}</div>`).join("");
  const faltando = (c.dados_faltando || []).slice(0, 4)
    .map((x) => `<div class="nota faint">○ ${esc(x)}</div>`).join("");

  return `<div class="card sinal" data-grade="${grade}">
    <div class="sinal-topo">
      <div class="sinal-symbol">${esc(a.symbol)}</div>
      <span class="chip chip-${c.direcao === "compra" ? "long" : "short"}">${esc(c.direcao)}</span>
      <span class="chip chip-${grade}">${esc(c.decisao.replace(/_/g, " "))}</span>
      ${a.regime ? `<span class="chip chip-regime">${esc(a.regime.regime)}</span>` : ""}
      <div class="spacer"></div>
      <div class="mono" style="font-size:15px;font-weight:700">${c.score.toFixed(1)}</div>
    </div>

    <div class="grid" style="grid-template-columns:1fr 1fr;gap:8px;margin:8px 0">
      <div class="plano-item"><div class="plano-label">Faixa do score</div>
        <div class="plano-valor" style="font-size:11.5px">${esc(c.faixa)}</div></div>
      <div class="plano-item"><div class="plano-label">Cobertura analítica</div>
        <div class="plano-valor ${c.cobertura < 0.6 ? "warn" : ""}">${(c.cobertura * 100).toFixed(0)}%</div></div>
      <div class="plano-item"><div class="plano-label">Agentes</div>
        <div class="plano-valor" style="font-size:12px">${c.n_concordantes} concordam / ${c.n_divergentes} divergem</div></div>
      <div class="plano-item"><div class="plano-label">Preço</div>
        <div class="plano-valor">${fmtPreco(a.preco)}</div></div>
    </div>

    <div class="card-titulo" style="margin-top:8px">Pareceres dos agentes</div>
    <div class="fatores">${pareceres}</div>

    ${a.risco ? `<div class="invalidacao">
      <strong>Risk Engine:</strong> ${esc(a.risco.veredicto)} — ${esc(a.risco.motivo_principal)}
      ${a.risco.analise_liquidacao ? `<br>liquidação a ${a.risco.analise_liquidacao.dist_liquidacao_pct}% da entrada${
        a.risco.analise_liquidacao.liquidacao_em_atrs ? ` (${a.risco.analise_liquidacao.liquidacao_em_atrs} ATRs)` : ""}` : ""}
    </div>` : ""}

    ${contra ? `<div class="notas">${contra}</div>` : ""}
    ${faltando ? `<div class="notas">${faltando}</div>` : ""}
    <div class="faint" style="font-size:10.5px;margin-top:8px">${esc(c.aviso_score || "")}</div>
  </div>`;
}

function renderCiclo(d) {
  $("kpi-operaveis").textContent = d.total_operavel;
  $("kpi-observacao").textContent = d.total_em_observacao;
  $("kpi-analisados").textContent = d.total_analisado;
  $("kpi-duracao").textContent = `ciclo em ${d.duracao_s}s`;

  const operaveis = (d.analises || []).filter((a) => a.operavel);
  const observacao = (d.analises || []).filter(
    (a) => a.consenso && ["observar", "aguardar_confirmacao"].includes(a.consenso.decisao));
  const rejeitados = (d.analises || []).filter(
    (a) => !a.operavel && !observacao.includes(a));

  const melhorScore = operaveis.length
    ? Math.max(...operaveis.map((a) => a.consenso.score)) : null;
  const kpi = $("kpi-expectativa");
  kpi.textContent = melhorScore !== null ? melhorScore.toFixed(1) : "—";
  kpi.className = "kpi-valor " + (melhorScore !== null ? "pos" : "dim");

  $("cont-operaveis").textContent = operaveis.length;
  $("cont-observacao").textContent = observacao.length;
  $("cont-rejeitados").textContent = rejeitados.length;

  $("lista-operaveis").innerHTML = operaveis.length
    ? operaveis.map(cardConsenso).join("")
    : `<div class="vazio">${esc(d.resumo || "NENHUMA OPORTUNIDADE ATENDE AOS CRITÉRIOS.")}
       <br><br>Isso é um resultado válido: o sistema não encontrou evidência
       suficiente para arriscar capital.</div>`;

  $("lista-observacao").innerHTML = observacao.length
    ? observacao.slice(0, 10).map(cardConsenso).join("")
    : '<div class="vazio">—</div>';

  $("tbody-rejeitados").innerHTML = rejeitados.length
    ? rejeitados.map((a) => `<tr>
        <td class="mono"><strong>${esc(a.symbol)}</strong></td>
        <td class="faint" style="text-align:right">${esc(a.etapa_final)}</td>
        <td style="text-align:right">${esc(a.decisao.replace(/_/g, " "))}</td>
        <td style="text-align:right"><span class="chip chip-c">${esc(a.categoria_motivo)}</span></td>
        <td class="dim td-texto" style="font-size:11.5px">${esc(a.motivo || a.erro || "—")}</td>
      </tr>`).join("")
    : '<tr><td colspan="5" class="vazio">nenhum rejeitado</td></tr>';

  // Reaproveita a matriz de mercado com os dados do ciclo.
  const tb = $("tbody-matriz");
  tb.innerHTML = (d.analises || []).map((a) => {
    const c = a.consenso;
    const m = a.regime ? a.regime.metricas : {};
    return `<tr>
      <td class="mono"><strong>${esc(a.symbol)}</strong></td>
      <td class="num">${fmtPreco(a.preco)}</td>
      <td class="faint" style="text-align:right">${esc(a.regime ? a.regime.regime : "—")}</td>
      <td class="num">${m.adx !== undefined ? m.adx.toFixed(0) : "—"}</td>
      <td class="num">${m.atr_pct !== undefined ? m.atr_pct.toFixed(2) + "%" : "—"}</td>
      <td class="num">${a.qualidade ? esc(a.qualidade.status) : "—"}</td>
      <td class="num">${a.anomalias ? a.anomalias.total : "—"}</td>
      <td class="num">${c ? c.score.toFixed(1) : "—"}</td>
      <td class="num">${c ? (c.cobertura * 100).toFixed(0) + "%" : "—"}</td>
      <td>${c ? `<span class="chip chip-${c.decisao === "validada_pelo_modelo" ? "a"
        : c.decisao === "aguardar_confirmacao" ? "b" : "c"}">${esc(c.decisao.replace(/_/g, " "))}</span>` : "—"}</td>
      <td class="faint" style="text-align:right">${esc(a.categoria_motivo)}</td>
    </tr>`;
  }).join("") || '<tr><td colspan="11" class="vazio">sem dados</td></tr>';

  if (d.saude) {
    const b = $("badge-saude");
    if (b) {
      b.textContent = `sistema ${d.saude.estado_geral}`;
      b.className = "badge " + (d.saude.estado_geral === "HEALTHY" ? "badge-on"
        : d.saude.estado_geral === "OFFLINE" ? "badge-live" : "badge-warn");
    }
  }
}

/* ---------------------------------------------------- validação */
async function rodarValidacao() {
  const btn = $("btn-validar");
  btn.disabled = true;
  btn.innerHTML = '<i class="spin"></i>validando…';
  try {
    const d = await api("/api/validacao/estrategia", {
      method: "POST",
      body: JSON.stringify({
        symbol: $("v-symbol").value.trim().toUpperCase(),
        timeframe: $("v-tf").value,
        lado: $("v-lado").value,
        barras: Number($("v-barras").value),
        n_ciclos: Number($("v-ciclos").value),
        score_minimo: Number($("v-score").value),
      }),
    });
    renderValidacao(d);
    // O veredicto exibido é o do ÚLTIMO gate, não `promovida`: uma
    // estratégia que subiu duas fases e foi barrada na terceira não pode
    // aparecer em verde.
    const subiu = (d.fases_avancadas || []).length
      ? ` (avançou: ${d.fases_avancadas.join(" → ")})` : "";
    mostrarMsg("msg-validacao",
      `${d.chave_estrategia}: fase final ${d.fase_final}${subiu}` +
      (d.gate_aprovado ? "" : " — BARRADA no gate seguinte"),
      d.gate_aprovado);
    await carregarEstrategias();
  } catch (e) {
    mostrarMsg("msg-validacao", e.message, false);
  } finally {
    btn.disabled = false;
    btn.textContent = "Validar estratégia";
  }
}

function renderValidacao(d) {
  const ev = d.evidencia || {};
  const wf = d.walk_forward || {};
  const gate = d.gate || {};
  const is_ = ev.stats_backtest || {};
  const oos = ev.stats_oos || {};
  const mc = ev.monte_carlo || {};
  const of = ev.overfit || {};
  const evo = ev.ev_oos || {};

  const criterios = (gate.criterios || []).map((c) => `<tr>
    <td>${esc(c.nome)}</td>
    <td class="num">${esc(String(c.exigido))}</td>
    <td class="num ${c.passou ? "pos" : "neg"}">${esc(String(c.medido))}</td>
    <td style="text-align:right">${c.passou ? '<span class="chip chip-a">passou</span>'
      : '<span class="chip chip-rejeitado">reprovou</span>'}</td>
    <td class="faint" style="text-align:right;font-size:11px">${esc((c.explicacao || "").slice(0, 90))}</td>
  </tr>`).join("");

  $("resultado-validacao").innerHTML = `
    <div class="grid grid-kpi">
      <div class="card"><div class="card-titulo">In-sample</div>
        <div class="kpi-valor">${(is_.expectancy_r ?? 0) >= 0 ? "+" : ""}${(is_.expectancy_r ?? 0).toFixed(3)}R</div>
        <div class="kpi-sub">${is_.trades ?? 0} operações · PF ${(is_.profit_factor ?? 0).toFixed(2)}</div></div>
      <div class="card"><div class="card-titulo">OUT-OF-SAMPLE</div>
        <div class="kpi-valor ${(oos.expectancy_r ?? 0) > 0 ? "pos" : "neg"}">${(oos.expectancy_r ?? 0) >= 0 ? "+" : ""}${(oos.expectancy_r ?? 0).toFixed(3)}R</div>
        <div class="kpi-sub">${oos.trades ?? 0} operações · o único número que conta</div></div>
      <div class="card"><div class="card-titulo">Degradação IS → OOS</div>
        <div class="kpi-valor ${(ev.degradacao_expectancy ?? 0) > 0.5 ? "neg" : "warn"}">${((ev.degradacao_expectancy ?? 0) * 100).toFixed(0)}%</div>
        <div class="kpi-sub">quanto desapareceu fora da amostra</div></div>
      <div class="card"><div class="card-titulo">Overfitting</div>
        <div class="kpi-valor ${of.veredicto === "robusto" ? "pos" : "neg"}" style="font-size:16px">${esc(of.veredicto || "—")}</div>
        <div class="kpi-sub">${of.n_disparados ?? 0} sinais disparados</div></div>
    </div>

    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr));margin-top:14px">
      <div class="card"><div class="card-titulo">Expectativa com intervalo de confiança</div>
        <div class="grid" style="grid-template-columns:1fr 1fr;gap:8px">
          <div class="plano-item"><div class="plano-label">EV líquido</div>
            <div class="plano-valor ${(evo.ev_liquido_r ?? 0) > 0 ? "pos" : "neg"}">${(evo.ev_liquido_r ?? 0) >= 0 ? "+" : ""}${(evo.ev_liquido_r ?? 0).toFixed(3)}R</div></div>
          <div class="plano-item"><div class="plano-label">EV no pior caso do IC</div>
            <div class="plano-valor ${(evo.ev_pessimista_r ?? 0) > 0 ? "pos" : "neg"}">${(evo.ev_pessimista_r ?? 0) >= 0 ? "+" : ""}${(evo.ev_pessimista_r ?? 0).toFixed(3)}R</div></div>
        </div>
        ${evo.ic_win_rate ? `<div class="dim" style="font-size:12px;margin-top:8px">
          IC 95% da taxa de acerto: [${(evo.ic_win_rate.inferior * 100).toFixed(1)}%,
          ${(evo.ic_win_rate.superior * 100).toFixed(1)}%] sobre ${evo.n} operações
          ${evo.ic_win_rate.informativo ? "" : " — amplitude larga demais para sustentar decisão"}</div>` : ""}
        ${(evo.motivos || []).map((m) => `<div class="nota">⚠ ${esc(m)}</div>`).join("")}
      </div>

      <div class="card"><div class="card-titulo">Monte Carlo (out-of-sample, blocos)</div>
        <div class="grid" style="grid-template-columns:1fr 1fr;gap:8px">
          <div class="plano-item"><div class="plano-label">Drawdown p95</div>
            <div class="plano-valor ${(mc.drawdown_p95 ?? 0) > 25 ? "neg" : ""}">${(mc.drawdown_p95 ?? 0).toFixed(2)}%</div></div>
          <div class="plano-item"><div class="plano-label">Prob. de prejuízo</div>
            <div class="plano-valor">${((mc.prob_prejuizo ?? 0) * 100).toFixed(1)}%</div></div>
          <div class="plano-item"><div class="plano-label">Prob. de ruína</div>
            <div class="plano-valor ${(mc.prob_ruina ?? 0) > 0.01 ? "neg" : "pos"}">${((mc.prob_ruina ?? 0) * 100).toFixed(2)}%</div></div>
          <div class="plano-item"><div class="plano-label">Perdas seguidas p95</div>
            <div class="plano-valor">${mc.perdas_consecutivas ? mc.perdas_consecutivas.p95 : "—"}</div></div>
        </div>
        ${(mc.avisos || []).slice(0, 3).map((m) => `<div class="nota">⚠ ${esc(m)}</div>`).join("")}
      </div>
    </div>

    <div class="secao-titulo">Gate de ${esc(gate.fase_atual || "?")} → ${esc(gate.fase_alvo || "?")}
      <span class="contagem">${gate.aprovado ? "aprovado" : "reprovado"}</span></div>
    <div class="aviso ${gate.aprovado ? "" : "aviso-critico"}">${esc(gate.resumo || "")}</div>
    ${criterios ? `<div class="tabela-wrap"><table>
      <thead><tr><th>Critério</th><th>Exigido</th><th>Medido</th><th>Resultado</th><th>Por quê</th></tr></thead>
      <tbody>${criterios}</tbody></table></div>` : ""}
    ${(gate.dados_faltando || []).length ? `<div class="aviso aviso-critico" style="margin-top:10px">
      <strong>Medições obrigatórias faltando:</strong> ${esc(gate.dados_faltando.join(", "))}.
      Ausência de dado não é aprovação.</div>` : ""}
    ${(d.avisos || []).map((a) => `<div class="aviso" style="margin-top:10px">${esc(a)}</div>`).join("")}
    ${wf.ciclos ? `<div class="secao-titulo">Ciclos de walk-forward</div>
      <div class="tabela-wrap"><table>
      <thead><tr><th>Janela</th><th>Operações</th><th>Expectativa</th><th>PF</th><th>Drawdown</th></tr></thead>
      <tbody>${wf.ciclos.map((c) => `<tr>
        <td class="mono">${esc(c.janela.nome)}</td>
        <td class="num">${c.stats.trades}</td>
        <td class="num ${c.stats.expectancy_r > 0 ? "pos" : "neg"}">${c.stats.expectancy_r >= 0 ? "+" : ""}${c.stats.expectancy_r.toFixed(3)}R</td>
        <td class="num">${c.stats.profit_factor.toFixed(2)}</td>
        <td class="num">${c.stats.max_drawdown_pct.toFixed(2)}%</td>
      </tr>`).join("")}</tbody></table></div>` : ""}`;
}

async function carregarEstrategias() {
  try {
    const d = await api("/api/estrategias");
    $("cont-estrategias").textContent = d.total;

    const fases = d.ordem_das_fases || [];
    const porFase = d.por_fase || {};
    $("pipeline-fases").innerHTML = `
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        ${fases.map((f, i) => {
          const n = (porFase[f] || []).length;
          return `${i ? '<span class="faint">→</span>' : ""}
            <span class="chip ${n ? "chip-a" : "chip-regime"}" title="${esc(d.descricao_das_fases[f] || "")}">
              ${esc(f)}${n ? ` (${n})` : ""}</span>`;
        }).join("")}
      </div>
      <div class="dim" style="font-size:12px;margin-top:10px">
        ${esc(d.descricao_das_fases ? (d.descricao_das_fases.out_of_sample || "") : "")}
      </div>
      <div class="dim" style="font-size:12px;margin-top:6px">
        Operáveis com capital real: <strong>${(d.operaveis_em_real || []).length
          ? esc(d.operaveis_em_real.join(", ")) : "nenhuma"}</strong>
      </div>`;

    $("tbody-estrategias").innerHTML = (d.versoes || []).length
      ? d.versoes.map((v) => `<tr>
          <td class="mono"><strong>${esc(v.chave)}</strong>
            <div class="faint" style="font-size:10.5px">${esc(v.descricao || "")}</div></td>
          <td style="text-align:right"><span class="chip ${v.fase === "real_limitado" ? "chip-a"
            : v.fase === "reprovada" ? "chip-rejeitado" : "chip-c"}">${esc(v.fase)}</span></td>
          <td class="num faint">${esc(v.params_hash)}</td>
          <td style="text-align:right">${v.operavel_real ? '<span class="pos">sim</span>' : '<span class="faint">não</span>'}</td>
          <td style="text-align:right">${v.ultimo_gate
            ? (v.ultimo_gate.aprovado ? '<span class="pos">aprovado</span>'
              : `<span class="neg">${(v.ultimo_gate.reprovacoes || []).length} reprovações</span>`)
            : '<span class="faint">—</span>'}</td>
          <td class="num faint">${(v.historico || []).length}</td>
        </tr>`).join("")
      : '<tr><td colspan="6" class="vazio">nenhuma estratégia registrada</td></tr>';
  } catch (e) {
    console.warn(e);
  }
}

/* ---------------------------------------------------- portfólio */
async function carregarPortfolio() {
  try {
    const d = await api("/api/portfolio");
    if (!d.diversificacao) {
      $("p-posicoes").textContent = d.n_posicoes ?? 0;
      $("aviso-diversificacao").innerHTML =
        `<div class="aviso" style="margin-top:14px">${esc(d.mensagem || "sem posições")}.
         Cenários de estresse disponíveis: ${esc((d.cenarios_disponiveis || []).join(", "))}</div>`;
      $("matriz-correlacao").innerHTML = '<div class="vazio">sem posições abertas</div>';
      return;
    }
    const dv = d.diversificacao;
    const co = d.correlacao || {};
    $("p-posicoes").textContent = dv.n_posicoes;
    const ap = $("p-apostas");
    ap.textContent = dv.apostas_efetivas;
    ap.className = "kpi-valor " + (dv.falsa_diversificacao ? "neg" : "pos");
    $("p-corr").textContent = co.media_pearson !== null && co.media_pearson !== undefined
      ? co.media_pearson.toFixed(2) : "—";
    $("p-corr-cauda").textContent = co.media_cauda !== null && co.media_cauda !== undefined
      ? `dependência de cauda ${co.media_cauda.toFixed(2)}` : "cauda indisponível";

    $("aviso-diversificacao").innerHTML = (dv.avisos || []).length
      ? (dv.avisos || []).map((a) => `<div class="aviso ${dv.falsa_diversificacao
          ? "aviso-critico" : ""}" style="margin-top:14px">${esc(a)}</div>`).join("")
      : "";

    const simbolos = co.simbolos || [];
    $("matriz-correlacao").innerHTML = simbolos.length
      ? `<table><thead><tr><th>par</th>${simbolos.map((s) =>
          `<th>${esc(s.slice(0, 7))}</th>`).join("")}<th>cauda média</th></tr></thead>
        <tbody>${simbolos.map((a) => `<tr>
          <td class="mono"><strong>${esc(a)}</strong></td>
          ${simbolos.map((b) => {
            const v = (co.pearson[a] || {})[b];
            const cls = v === null || v === undefined ? "faint"
              : v >= 0.7 ? "neg" : v >= 0.4 ? "warn" : "";
            return `<td class="num ${cls}">${v === null || v === undefined ? "—" : v.toFixed(2)}</td>`;
          }).join("")}
          <td class="num faint">${(() => {
            const vals = simbolos.filter((b) => b !== a)
              .map((b) => (co.cauda[a] || {})[b]).filter((x) => x !== null && x !== undefined);
            return vals.length ? (vals.reduce((s, x) => s + x, 0) / vals.length).toFixed(2) : "—";
          })()}</td>
        </tr>`).join("")}</tbody></table>`
      : '<div class="vazio">sem dados de correlação</div>';
  } catch (e) {
    $("aviso-diversificacao").innerHTML =
      `<div class="aviso aviso-critico">${esc(e.message)}</div>`;
  }
}

async function rodarStress() {
  const btn = $("btn-stress");
  btn.disabled = true;
  try {
    const status = await api("/api/status");
    const capital = (status.risco && status.risco.capital_atual) || 1000;
    const d = await api("/api/portfolio/stress", {
      method: "POST", body: JSON.stringify({ capital }),
    });
    $("p-stress").textContent = `${d.pior_drawdown_pct}%`;
    $("p-stress").className = "kpi-valor " + (d.pior_drawdown_pct > 25 ? "neg" : "warn");
    $("p-stress-nome").textContent = d.pior_cenario;

    $("tbody-stress").innerHTML = (d.resultados || []).map((r) => `<tr>
      <td class="mono">${esc(r.cenario.nome)}
        <div class="faint" style="font-size:10.5px">${esc(r.cenario.descricao)}</div></td>
      <td class="num">${fmtUsd(r.perda_planejada_usd)}</td>
      <td class="num neg">${fmtUsd(r.perda_realista_usd)}</td>
      <td class="num ${r.amplificacao > 2 ? "neg" : r.amplificacao > 1.3 ? "warn" : ""}">${r.amplificacao.toFixed(1)}x</td>
      <td class="num ${r.drawdown_pct >= 100 ? "neg" : r.drawdown_pct > 25 ? "warn" : ""}">${r.drawdown_pct.toFixed(2)}%</td>
      <td class="num">${r.stops_furados}</td>
      <td style="text-align:right">${r.sobrevive ? '<span class="chip chip-a">sim</span>'
        : '<span class="chip chip-rejeitado">não</span>'}</td>
    </tr>`).join("") || '<tr><td colspan="7" class="vazio">—</td></tr>';

    const ruina = d.cenarios_de_ruina || [];
    $("stress-avisos").innerHTML = ruina.length
      ? `<div class="aviso aviso-critico" style="margin-top:14px">
          <strong>Cenários de RUÍNA (perda excede o capital):</strong>
          ${esc(ruina.join(", "))}. Drawdown acima de 100% mede o quanto o
          cenário passa da ruína, não uma perda absorvível.</div>`
      : `<div class="aviso" style="margin-top:14px">${esc(d.observacao || "")}</div>`;
  } catch (e) {
    $("stress-avisos").innerHTML = `<div class="aviso aviso-critico">${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

/* --------------------------------------------------- multiativos */
const TITULOS_EXEMPLO = [
  { nome: "Tesouro Selic 2029", tipo: "tesouro", indexador: "pos_selic", taxa: 0.05, prazo_dias: 1800, emissor: "Tesouro", risco_credito: 1, liquidez_diaria: true },
  { nome: "Tesouro IPCA+ 2035", tipo: "tesouro", indexador: "ipca_mais", taxa: 6.2, prazo_dias: 4015, emissor: "Tesouro", risco_credito: 1, liquidez_diaria: true },
  { nome: "Prefixado 11,8%", tipo: "tesouro", indexador: "prefixado", taxa: 11.8, prazo_dias: 1460, emissor: "Tesouro", risco_credito: 1, liquidez_diaria: true },
  { nome: "CDB banco AAA 102% CDI", tipo: "cdb", indexador: "pos_cdi", taxa: 102, prazo_dias: 720, emissor: "Banco AAA", risco_credito: 2, liquidez_diaria: true },
  { nome: "CDB banco pequeno 128% CDI", tipo: "cdb", indexador: "pos_cdi", taxa: 128, prazo_dias: 1080, emissor: "Banco pequeno", risco_credito: 5, liquidez_diaria: false },
  { nome: "LCI 96% CDI (isenta)", tipo: "lci", indexador: "pos_cdi", taxa: 96, prazo_dias: 730, emissor: "Banco médio", risco_credito: 3, liquidez_diaria: false },
  { nome: "Debênture incentivada IPCA+7%", tipo: "debenture_incentivada", indexador: "ipca_mais", taxa: 7.0, prazo_dias: 2555, emissor: "Empresa", risco_credito: 4, liquidez_diaria: false },
];

async function compararRendaFixa() {
  try {
    const d = await api("/api/ativos/renda-fixa", {
      method: "POST",
      body: JSON.stringify({
        titulos: TITULOS_EXEMPLO,
        cdi_aa: Number($("rf-cdi").value),
        selic_aa: Number($("rf-cdi").value),
        ipca_aa: Number($("rf-ipca").value),
        valor_aplicado: Number($("rf-valor").value),
      }),
    });
    if (!d.disponivel) {
      mostrarMsg("msg-rf", d.mensagem, false);
      return;
    }
    $("tbody-rf").innerHTML = d.titulos.map((t) => `<tr>
      <td class="mono"><strong>${esc(t.titulo.nome)}</strong>
        <div class="faint" style="font-size:10.5px">${esc(t.titulo.emissor)} · risco ${t.titulo.risco_credito}/5</div></td>
      <td class="num">${t.bruto_aa.toFixed(2)}%</td>
      <td class="num">${t.liquido_aa.toFixed(2)}%</td>
      <td class="num ${t.real_liquido_aa > 0 ? "pos" : "neg"}"><strong>${t.real_liquido_aa.toFixed(2)}%</strong></td>
      <td class="num faint">${(t.aliquota_ir * 100).toFixed(1)}%</td>
      <td class="num">${t.duration_anos.toFixed(1)}a</td>
      <td style="text-align:right">${t.dentro_do_fgc ? '<span class="chip chip-b">sim</span>' : '<span class="faint">—</span>'}</td>
      <td class="num"><strong>${t.score.toFixed(1)}</strong></td>
      <td style="text-align:right">${t.alertas.length
        ? `<span class="chip chip-c" title="${esc(t.alertas.join(" | "))}">${t.alertas.length}</span>`
        : '<span class="faint">—</span>'}</td>
    </tr>`).join("");
    mostrarMsg("msg-rf", d.observacao, true);
  } catch (e) {
    mostrarMsg("msg-rf", e.message, false);
  }
}

const CANDIDATOS_EXEMPLO = [
  { identificador: "Tesouro Selic", classe: "renda_fixa", retorno_nominal_aa: 10.55, incerteza_retorno: 0.3, volatilidade_aa: 0.8, drawdown_plausivel_pct: 1.0, aliquota_ir: 0.15, liquidez_dias: 1, horizonte_minimo_meses: 1, risco_credito: 1, garantia: "soberano" },
  { identificador: "CDB frágil 128%", classe: "renda_fixa", retorno_nominal_aa: 13.44, incerteza_retorno: 0.5, volatilidade_aa: 0.5, drawdown_plausivel_pct: 2.0, aliquota_ir: 0.15, liquidez_dias: 1080, horizonte_minimo_meses: 36, risco_credito: 5, garantia: "FGC" },
  { identificador: "FII de logística", classe: "fii", retorno_nominal_aa: 12.5, incerteza_retorno: 8.0, volatilidade_aa: 14.0, drawdown_plausivel_pct: 25.0, isento_ir: true, liquidez_dias: 2, horizonte_minimo_meses: 36 },
  { identificador: "ETF de índice", classe: "etf", retorno_nominal_aa: 14.0, incerteza_retorno: 20.0, volatilidade_aa: 24.0, drawdown_plausivel_pct: 45.0, aliquota_ir: 0.15, liquidez_dias: 2, horizonte_minimo_meses: 60 },
  { identificador: "BTC spot", classe: "cripto", retorno_nominal_aa: 25.0, incerteza_retorno: 60.0, volatilidade_aa: 65.0, drawdown_plausivel_pct: 75.0, aliquota_ir: 0.15, liquidez_dias: 1, horizonte_minimo_meses: 60 },
];

async function compararClasses() {
  try {
    const d = await api("/api/ativos/comparar", {
      method: "POST",
      body: JSON.stringify({
        candidatos: CANDIDATOS_EXEMPLO,
        inflacao_aa: Number($("rf-ipca").value),
        horizonte_meses: Number($("cc-horizonte").value),
        tolerancia_drawdown_pct: Number($("cc-dd").value),
        necessidade_liquidez_dias: Number($("cc-liq").value),
        objetivo: $("cc-objetivo").value,
      }),
    });
    const excluidos = new Set((d.incompativeis || []).map((x) => x.identificador));
    $("tbody-classes").innerHTML = (d.linhas || []).map((l) => {
      const c = l.candidato;
      if (!l.comparavel) {
        return `<tr><td class="mono">${esc(c.identificador)}</td>
          <td colspan="7" class="faint">${esc(l.motivo_incomparavel)}</td></tr>`;
      }
      const fora = excluidos.has(c.identificador);
      return `<tr style="${fora ? "opacity:.55" : ""}">
        <td class="mono"><strong>${esc(c.identificador)}</strong>
          ${fora ? '<div class="faint" style="font-size:10.5px">excluído pelo perfil</div>' : ""}</td>
        <td class="faint" style="text-align:right">${esc(c.classe)}</td>
        <td class="num">${c.retorno_nominal_aa.toFixed(2)}%</td>
        <td class="num">${l.retorno_liquido_aa.toFixed(2)}%</td>
        <td class="num ${l.retorno_real_liquido_aa > 0 ? "pos" : "neg"}">${l.retorno_real_liquido_aa.toFixed(2)}%</td>
        <td class="num">${l.risco_total_aa !== null ? l.risco_total_aa.toFixed(1) + "%" : "—"}</td>
        <td class="num"><strong>${l.retorno_por_risco !== null ? l.retorno_por_risco.toFixed(2) : "—"}</strong></td>
        <td class="num ${l.retorno_pessimista_aa < 0 ? "neg" : ""}">${l.retorno_pessimista_aa !== null
          ? l.retorno_pessimista_aa.toFixed(2) + "%" : "—"}</td>
      </tr>`;
    }).join("");

    $("classes-conclusao").innerHTML = `
      <div class="aviso" style="margin-top:14px">${esc(d.conclusao)}</div>
      ${(d.incompativeis || []).length ? `<div class="card" style="margin-top:10px">
        <div class="card-titulo">Excluídos por incompatibilidade com o perfil (não por retorno)</div>
        ${d.incompativeis.map((x) => `<div class="nota">○ <strong>${esc(x.identificador)}</strong>: ${esc(x.motivo)}</div>`).join("")}
      </div>` : ""}
      ${(d.avisos || []).map((a) => `<div class="aviso aviso-critico" style="margin-top:10px">${esc(a)}</div>`).join("")}`;
  } catch (e) {
    $("classes-conclusao").innerHTML = `<div class="aviso aviso-critico">${esc(e.message)}</div>`;
  }
}

async function testarAcaoSemFonte() {
  try {
    const d = await api("/api/ativos/acao", {
      method: "POST", body: JSON.stringify({ ticker: "PETR4" }),
    });
    const a = d.analise;
    $("resultado-acao").innerHTML = `
      <div class="aviso aviso-critico" style="margin-top:12px">
        <strong>${esc(a.ticker)}:</strong> ${esc(a.mensagem)}
      </div>
      <div class="dim" style="font-size:12px;margin-top:8px">
        campos exigidos e ausentes: ${esc((a.dados_faltando || []).slice(0, 10).join(", "))}
      </div>
      <div class="faint" style="font-size:11.5px;margin-top:6px">
        cobertura declarada: ${esc(d.cobertura.mensagem)}<br>
        como configurar: ${esc(d.cobertura.instrucao || "—")}
      </div>`;
  } catch (e) {
    $("resultado-acao").innerHTML = `<div class="aviso aviso-critico">${esc(e.message)}</div>`;
  }
}

/* ------------------------------------------------------ sistema */
async function carregarSistema() {
  try {
    const [saude, cobertura, risco, alertas] = await Promise.all([
      api("/api/saude"), api("/api/dados/cobertura"), api("/api/risco"),
      api("/api/alertas?nivel=urgente"),
    ]);

    const s = $("s-saude");
    s.textContent = saude.estado_geral;
    s.className = "kpi-valor " + (saude.estado_geral === "HEALTHY" ? "pos"
      : saude.estado_geral === "OFFLINE" ? "neg" : "warn");
    $("s-pode-abrir").textContent = saude.pode_abrir_posicao
      ? "pode abrir posição" : "abertura BLOQUEADA";
    $("s-provedores").textContent = (cobertura.resumo.conectados || []).length;
    $("s-lacunas").textContent = cobertura.resumo.combinacoes_sem_fonte;
    $("s-alertas").textContent = (alertas.alertas || []).length;

    $("tbody-componentes").innerHTML = (saude.componentes || []).map((c) => `<tr>
      <td class="mono"><strong>${esc(c.nome)}</strong>
        <div class="faint" style="font-size:10.5px">${esc(c.descricao)}</div></td>
      <td style="text-align:right">${c.critico ? '<span class="chip chip-c">crítico</span>' : '<span class="faint">não</span>'}</td>
      <td style="text-align:right"><span class="chip ${c.estado === "HEALTHY" ? "chip-a"
        : c.estado === "OFFLINE" ? "chip-rejeitado" : "chip-c"}">${esc(c.estado)}</span></td>
      <td class="num">${c.falhas_consecutivas}</td>
      <td class="num faint">${c.latencia_ms !== null ? c.latencia_ms + " ms" : "—"}</td>
      <td class="faint td-texto" style="font-size:11px">${esc(c.ultimo_erro || "—")}</td>
    </tr>`).join("");

    $("tbody-provedores").innerHTML = (cobertura.provedores || []).map((p) => `<tr>
      <td class="mono"><strong>${esc(p.nome)}</strong>
        <div class="faint" style="font-size:10.5px">${esc(p.descricao)}</div></td>
      <td style="text-align:right"><span class="chip ${p.estado === "conectado" ? "chip-a" : "chip-rejeitado"}">${esc(p.estado)}</span></td>
      <td class="faint" style="text-align:right;font-size:11px">${esc((p.classes || []).join(", "))}</td>
      <td class="faint" style="text-align:right;font-size:11px">${esc((p.tipos || []).join(", "))}</td>
      <td class="dim td-texto" style="font-size:11px">${esc(p.instrucao_configuracao || "—")}</td>
    </tr>`).join("");

    $("lista-proibicoes").innerHTML = `
      ${(risco.proibicoes_estruturais || []).map((x) => `<div class="nota">✕ ${esc(x)}</div>`).join("")}
      <div class="dim" style="font-size:12px;margin-top:10px">
        Estas proibições não têm flag de configuração. Um sistema em que elas
        podem ser desligadas não as tem.
      </div>
      <div class="dim" style="font-size:12px;margin-top:6px">
        Halt ativo: <strong>${risco.halted ? "SIM — " + esc(risco.motivo_halt) : "não"}</strong>
        · frase para retomar: <code>${esc(risco.confirmacao_para_retomar)}</code>
      </div>`;
  } catch (e) {
    mostrarMsg("msg-sistema", e.message, false);
  }
}

async function gerarRelatorio() {
  const btn = $("btn-relatorio");
  btn.disabled = true;
  btn.innerHTML = '<i class="spin"></i>gerando…';
  try {
    const d = await api("/api/relatorio-diario");
    $("texto-relatorio").textContent = d.texto;
  } catch (e) {
    $("texto-relatorio").textContent = `falha: ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Gerar relatório diário";
  }
}

/* -------------------------------------------- listeners das novas abas */
$("btn-ciclo").addEventListener("click", rodarCicloCompleto);
$("btn-validar").addEventListener("click", rodarValidacao);
$("btn-estrategias").addEventListener("click", carregarEstrategias);
$("btn-portfolio").addEventListener("click", carregarPortfolio);
$("btn-stress").addEventListener("click", rodarStress);
$("btn-rf").addEventListener("click", compararRendaFixa);
$("btn-comparar-classes").addEventListener("click", compararClasses);
$("btn-acao-teste").addEventListener("click", testarAcaoSemFonte);
/* ------------------------------------------------------------ shadow mode */
// Casas decimais pela escala do ativo: 12 casas num par de US$ 86 mil é
// ruído, e 2 casas num par de US$ 0,46 apaga a informação.
function preco(v) {
  if (v === null || v === undefined) return "—";
  const casas = Math.abs(v) >= 100 ? 2 : Math.abs(v) >= 1 ? 4 : 6;
  return Number(v).toLocaleString("pt-BR",
    { minimumFractionDigits: casas, maximumFractionDigits: casas });
}

async function carregarShadow() {
  try {
    const d = await api("/api/shadow");
    const r = d.resumo;

    $("sh-total").textContent = r.total;
    $("sh-pendentes").textContent = r.pendentes + " ainda em aberto";
    $("sh-ev").textContent = r.expectativa_r === null
      ? "—" : (r.expectativa_r >= 0 ? "+" : "") + r.expectativa_r.toFixed(3) + "R";
    $("sh-ev").className = "kpi-valor " + (r.expectativa_r === null ? "dim"
      : (r.expectativa_r > 0 ? "pos" : "neg"));
    $("sh-wr").textContent = r.win_rate === null
      ? "—" : (r.win_rate * 100).toFixed(1) + "%";
    $("sh-ganhos").textContent = r.ganhos + " no alvo · " + r.perdas +
      " no stop · " + r.expiradas + " expiradas";
    $("sh-dias").textContent = r.dias_corridos.toFixed(1);
    // Só declara conclusão quando a amostra sustenta: caso contrário o
    // número acima é anedota, e a tela precisa dizer isso.
    $("sh-conclusivo").textContent = r.conclusivo
      ? "amostra suficiente" : "ainda não conclusivo";

    $("sh-avisos").innerHTML = (r.avisos || []).length
      ? r.avisos.map((a) => `<div class="aviso">${esc(a)}</div>`).join("")
      : `<div class="nota">${esc(r.observacao)}</div>`;

    $("tbody-shadow").innerHTML = (d.decisoes || []).length
      ? d.decisoes.map((x) => {
          const chip = x.estado === "alvo" ? "chip-a"
            : x.estado === "stop" ? "chip-rejeitado" : "chip-c";
          const res = x.resultado_r === null || x.resultado_r === undefined
            ? "—"
            : `<span class="${x.resultado_r >= 0 ? "pos" : "neg"}">${
                (x.resultado_r >= 0 ? "+" : "") + x.resultado_r.toFixed(2)}R</span>`;
          return `<tr>
            <td class="mono"><strong>${esc(x.symbol)}</strong></td>
            <td>${esc(x.side)}</td>
            <td class="faint" style="text-align:right">${
              new Date(x.decidido_em).toLocaleString("pt-BR")}</td>
            <td class="num">${preco(x.entry)}</td>
            <td class="num">${preco(x.stop_loss)}</td>
            <td class="num">${preco(x.alvo)}</td>
            <td style="text-align:right"><span class="chip ${chip}">${esc(x.estado)}</span></td>
            <td class="num">${res}</td>
            <td class="dim td-texto" style="font-size:11.5px">${esc(x.motivo_saida || "—")}</td>
          </tr>`;
        }).join("")
      : `<tr><td colspan="9" class="vazio">nenhuma decisão registrada ainda —
         o sistema só registra o que aprova, e aprova pouco</td></tr>`;
  } catch (e) {
    mostrarMsg("msg-shadow", e.message, false);
  }
}

$("btn-shadow").addEventListener("click", carregarShadow);
$("btn-shadow-liquidar").addEventListener("click", async () => {
  const d = await acao("/api/shadow/liquidar", null, "msg-shadow");
  if (d) {
    mostrarMsg("msg-shadow",
      `${d.liquidadas} decisão(ões) conferida(s) contra o mercado`, true);
    await carregarShadow();
  }
});

$("btn-sistema").addEventListener("click", carregarSistema);
$("btn-relatorio").addEventListener("click", gerarRelatorio);

$("btn-halt").addEventListener("click", async () => {
  if (!confirm("Acionar TRADING HALT? Nenhuma nova operação até retomada explícita.")) return;
  try {
    const d = await api("/api/risco/halt", { method: "POST" });
    mostrarMsg("msg-sistema", d.mensagem, true);
    await carregarSistema();
    await carregarStatus();
  } catch (e) {
    mostrarMsg("msg-sistema", e.message, false);
  }
});

$("btn-retomar").addEventListener("click", async () => {
  const frase = prompt('Para retomar, digite exatamente: RETOMAR OPERACAO');
  if (frase === null) return;
  try {
    const d = await api("/api/risco/retomar", {
      method: "POST", body: JSON.stringify({ confirmacao: frase }),
    });
    mostrarMsg("msg-sistema", d.mensagem, true);
    await carregarSistema();
    await carregarStatus();
  } catch (e) {
    mostrarMsg("msg-sistema", e.message, false);
  }
});

/* Carrega cada aba nova ao abri-la. */
document.querySelectorAll("nav button").forEach((b) => {
  b.addEventListener("click", () => {
    const aba = b.dataset.aba;
    if (aba === "validacao") carregarEstrategias();
    if (aba === "portfolio") carregarPortfolio();
    if (aba === "sistema") carregarSistema();
  });
});

/* --------------------------------------------------- travas e diagnóstico */
/* Esta aba mostra o que IMPEDE uma ordem real de sair. São quatro travas
   independentes em série, e o valor da tela está em mostrar o MOTIVO de cada
   bloqueio — "bloqueado" sem motivo é o tipo de mensagem que o operador
   aprende a ignorar. */
function corDaTrava(ativa) {
  return ativa ? "neg" : "";
}

async function carregarTravas() {
  try {
    const [g, e, a, n] = await Promise.all([
      api("/api/guarda"),
      api("/api/envios"),
      api("/api/ambiente"),
      api("/api/reconciliacao"),
    ]);

    const guarda = g.guarda || {};
    const versao = guarda.versao || null;
    $("tr-chave").textContent = guarda.chave_vinculada || "nenhuma";
    $("tr-chave").className = "kpi-valor kpi-texto" + (versao ? "" : " dim");
    $("tr-fase").textContent = versao
      ? `fase ${versao.fase}` + (versao.faltam_fases && versao.faltam_fases.length
          ? ` · faltam ${versao.faltam_fases.join(", ")}` : "")
      : "sem estratégia vinculada, o modo real não arma";

    const travada = !!guarda.trava_ativa;
    $("tr-trava").textContent = travada ? "ATIVA" : "livre";
    $("tr-trava").className = "kpi-valor " + (travada ? "neg" : "dim");
    $("tr-trava-motivo").textContent = travada
      ? (guarda.trava_motivo || "sem motivo registrado")
      : "nenhuma parada em vigor";

    const pendentes = (e.idempotencia || {}).pendentes || 0;
    $("tr-envios").textContent = pendentes;
    $("tr-envios").className = "kpi-valor" + (pendentes ? " neg" : " dim");

    /* Em modo sintético nenhuma ordem sai para lugar nenhum. Mostrar
       "REAL" em vermelho aqui enquanto o cabeçalho diz SIMULAÇÃO seria
       contradizer a própria tela — e a contradição faria o operador
       desconfiar da parte certa. */
    const sintetico = !!(configApp && configApp.conexao &&
                         configApp.conexao.modo_dados === "sintetico");
    if (sintetico) {
      $("tr-ambiente").textContent = "SIMULAÇÃO";
      $("tr-ambiente").className = "kpi-valor dim";
      $("tr-ambiente-sub").textContent =
        `nenhuma ordem sai; quando conectado seria ${a.ambiente}`;
    } else {
      $("tr-ambiente").textContent = (a.ambiente || "?").toUpperCase();
      $("tr-ambiente").className = "kpi-valor" +
        (a.ambiente === "demo" ? " dim" : " neg");
      $("tr-ambiente-sub").textContent = a.descricao || "";
    }

    $("tr-guarda-nota").textContent = g.aviso || "";
    const estrategias = g.estrategias || [];
    /* `tbody-guarda-estrategias`, e não `tbody-estrategias`: este segundo
       id já existe na aba de Validação. Com o nome repetido,
       `getElementById` devolvia a tabela DAQUELA aba, e esta ficava vazia
       enquanto a outra era sobrescrita com marcação de outro formato. */
    $("tbody-guarda-estrategias").innerHTML = estrategias.length
      ? estrategias.map((v) => `
        <tr>
          <td class="mono">${esc(v.chave)}</td>
          <td>${esc(v.fase)}</td>
          <td>${v.operavel_real ? "sim" : "não"}</td>
          <td class="td-texto">${esc(v.fase_descricao || "")}</td>
        </tr>`).join("")
      : `<tr><td colspan="4" class="dim">nenhuma estratégia registrada;
           nada foi medido ainda</td></tr>`;

    const propostas = g.propostas_pendentes || [];
    $("tbody-propostas").innerHTML = propostas.length
      ? propostas.map((p) => `
        <tr>
          <td class="mono">${esc(p.symbol)}</td>
          <td>${p.side === "long" ? "compra" : "venda"}</td>
          <td>${preco(p.entry)}</td>
          <td>${preco(p.stop_loss)}</td>
          <td>${p.size}</td>
          <td>US$ ${(p.risco_usd || 0).toFixed(2)}</td>
          <td>
            <button class="btn btn-perigo" data-confirmar="${esc(p.client_oid)}">
              confirmar</button>
            <button class="btn" data-recusar="${esc(p.client_oid)}">recusar</button>
          </td>
        </tr>`).join("")
      : `<tr><td colspan="7" class="dim">nenhuma proposta aguardando</td></tr>`;

    document.querySelectorAll("[data-confirmar]").forEach((b) => {
      b.addEventListener("click", () => confirmarProposta(b.dataset.confirmar));
    });
    document.querySelectorAll("[data-recusar]").forEach((b) => {
      b.addEventListener("click", () => recusarProposta(b.dataset.recusar));
    });

    const rec = n.ultimo;
    if (rec) {
      $("tr-reconciliacao").textContent =
        `${rec.veredicto} · local ${rec.posicoes_locais}, ` +
        `corretora ${rec.posicoes_remotas}\n` +
        (rec.divergencias || []).map((d) =>
          `  [${d.pausa ? "PAUSA" : "aviso"}] ${d.symbol} ${d.tipo}: ${d.detalhe}`
        ).join("\n") + (rec.erro ? `\n  não conferido: ${rec.erro}` : "");
    }
  } catch (err) {
    mostrarMsg("msg-travas", err.message, false);
  }
}

async function confirmarProposta(clientOid) {
  /* A frase é a mesma trava do modo real: confirmar uma ordem que movimenta
     dinheiro não pode ser um clique distraído. */
  if (!confirm(
      "Esta ordem vai para a corretora e movimenta dinheiro real.\n\n" +
      "Confirmar o envio?")) return;
  const d = await acao("/api/guarda/confirmar", { client_oid: clientOid },
                       "msg-travas");
  if (d) await carregarTravas();
}

async function recusarProposta(clientOid) {
  const d = await acao("/api/guarda/recusar",
                       { client_oid: clientOid, motivo: "recusada no painel" },
                       "msg-travas");
  if (d) await carregarTravas();
}

$("btn-travas").addEventListener("click", carregarTravas);

$("btn-diagnostico").addEventListener("click", async () => {
  try {
    const d = await api("/api/diagnostico");
    $("tr-diagnostico").textContent = d.texto || "";
    mostrarMsg("msg-travas",
      d.pode_operar_real
        ? `sem bloqueios; ${(d.avisos || []).length} aviso(s)`
        : `${(d.bloqueios || []).length} bloqueio(s): o sistema NÃO deve ` +
          `operar real agora`,
      !!d.pode_operar_real);
  } catch (e) {
    mostrarMsg("msg-travas", e.message, false);
  }
});

$("btn-reconciliar").addEventListener("click", async () => {
  const d = await acao("/api/reconciliacao/conferir", null, "msg-travas");
  if (d) {
    $("tr-reconciliacao").textContent = d.texto || "";
    await carregarTravas();
  }
});

$("btn-parar-tudo").addEventListener("click", async () => {
  /* Dois passos de propósito: este comando fecha posições a mercado (o que
     custa spread e taxa) e deixa uma trava que sobrevive a reinício. */
  if (!confirm(
      "PARADA DE EMERGÊNCIA\n\n" +
      "Isto vai:\n" +
      "  1. travar o envio de novas ordens (a trava sobrevive a reinício);\n" +
      "  2. desarmar o modo real e parar o motor;\n" +
      "  3. FECHAR todas as posições a mercado.\n\n" +
      "Fechar a mercado custa spread e taxa. Continuar?")) return;
  const d = await acao("/api/operacao/parada-emergencia",
                       { motivo: "parada pelo painel" }, "msg-travas");
  if (d) await carregarTravas();
});
