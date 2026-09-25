"""Motor de confluência: transforma features em score, plano e classificação.

Filosofia do módulo
-------------------
Não existe entrada com ~100% de acerto em mercado futuro. O que existe é
*expectativa positiva*: acertar com frequência razoável e, principalmente,
ganhar mais nos acertos do que se perde nos erros. Por isso este motor:

1. pontua cada fator separadamente e guarda a contribuição (auditável);
2. só promove um sinal a operável se, ALÉM do score, a estatística histórica
   medida em walk-forward (win rate, profit factor, expectativa em R) passar
   nos mínimos configurados;
3. deriva `prob_acerto_estimada` do histórico real e a encolhe para a média
   quando a amostra é pequena (shrinkage bayesiano), em vez de exibir um
   número inflado;
4. define stop ANTES do alvo — o risco é o dado primário, o lucro é
   consequência.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..config import SignalConfig
from ..models import (
    BacktestStats, FactorScore, Features, MarketSnapshot, Regime, Side, Signal,
    SignalGrade,
)

# Pesos dos fatores. Somam 1.0 — mudar um exige rebalancear os outros.
PESOS: dict[str, float] = {
    "alinhamento_tf": 0.18,
    "forca_tendencia": 0.14,
    "momentum": 0.13,
    "estrutura": 0.12,
    "localizacao": 0.11,
    "rsi_contexto": 0.09,
    "volume": 0.08,
    "volatilidade": 0.08,
    "funding": 0.07,
}

# Win rate "prior" usado no shrinkage: uma estratégia genérica de seguir
# tendência com alvo 2R acerta perto de 40%. Começar daqui evita que 8 trades
# sortudos virem "90% de acerto".
PRIOR_WIN_RATE = 0.40
PRIOR_PESO = 25.0     # equivale a 25 trades imaginários de prior


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _sinal_direcional(side: Side) -> float:
    return 1.0 if side is Side.LONG else -1.0


# --------------------------------------------------------------------- fatores
def _fator_alinhamento(feats: Mapping[str, Features], principal: str,
                       side: Side) -> FactorScore:
    """Estrutura de médias no TF principal + concordância dos outros TFs."""
    d = _sinal_direcional(side)
    f = feats[principal]
    denom = f.close or 1.0

    # Distâncias relativas entre médias, normalizadas pelo preço.
    curto = (f.ema_fast - f.ema_slow) / denom * 100.0
    longo = (f.ema_slow - f.ema_trend) / denom * 100.0
    # 0.5% de separação já é estrutura clara em cripto intradiário.
    base = _clamp(d * (curto / 0.5) * 0.6 + d * (longo / 1.5) * 0.4)

    outros = [tf for tf in feats if tf != principal]
    concordam = 0
    for tf in outros:
        g = feats[tf]
        if d > 0 and g.ema_fast > g.ema_slow:
            concordam += 1
        elif d < 0 and g.ema_fast < g.ema_slow:
            concordam += 1
    acordo = (concordam / len(outros)) if outros else 1.0
    # Discordância entre timeframes corta o fator pela metade.
    valor = _clamp(base * (0.5 + 0.5 * acordo))
    return FactorScore(
        "alinhamento_tf", PESOS["alinhamento_tf"], valor,
        f"EMA9-21 {curto:+.2f}% / EMA21-200 {longo:+.2f}% | "
        f"{concordam}/{len(outros)} TFs concordam",
    )


def _fator_forca_tendencia(f: Features, side: Side) -> FactorScore:
    d = _sinal_direcional(side)
    # ADX 15 = fraco, 40 = muito forte.
    intensidade = _clamp((f.adx - 15.0) / 25.0, 0.0, 1.0)
    spread = f.di_plus - f.di_minus
    direcao = _clamp(d * spread / 15.0)
    valor = _clamp(direcao * intensidade)
    return FactorScore(
        "forca_tendencia", PESOS["forca_tendencia"], valor,
        f"ADX {f.adx:.1f} | +DI {f.di_plus:.1f} / -DI {f.di_minus:.1f}",
    )


def _fator_momentum(f: Features, side: Side) -> FactorScore:
    d = _sinal_direcional(side)
    denom = f.close or 1.0
    hist_norm = f.macd_hist / denom * 100.0     # histograma em % do preço
    valor = _clamp(d * hist_norm / 0.35)
    return FactorScore(
        "momentum", PESOS["momentum"], valor,
        f"MACD hist {hist_norm:+.3f}% do preço | linha {f.macd:+.2f}",
    )


def _fator_estrutura(f: Features, side: Side) -> FactorScore:
    """Rompimento de canal (Donchian 20, excluindo o candle atual)."""
    d = _sinal_direcional(side)
    canal = f.donchian_high - f.donchian_low
    if canal <= 0:
        return FactorScore("estrutura", PESOS["estrutura"], 0.0, "canal degenerado")
    if d > 0:
        # 1.0 = rompeu o topo; 0 = no fundo do canal.
        pos = (f.close - f.donchian_low) / canal
        valor = _clamp((pos - 0.5) * 2.0)
        detalhe = f"preço a {pos * 100:.0f}% do canal (topo {f.donchian_high:.4f})"
    else:
        pos = (f.donchian_high - f.close) / canal
        valor = _clamp((pos - 0.5) * 2.0)
        detalhe = f"preço a {pos * 100:.0f}% invertido (fundo {f.donchian_low:.4f})"
    return FactorScore("estrutura", PESOS["estrutura"], valor, detalhe)


def _fator_localizacao(f: Features, side: Side) -> FactorScore:
    """Penaliza entrada esticada — comprar topo é o erro mais caro que existe.

    Mede a distância do preço à EMA rápida em unidades de ATR. Até ~1 ATR é
    entrada saudável; acima de 2.5 ATR a reversão à média domina.
    """
    d = _sinal_direcional(side)
    if f.atr <= 0:
        return FactorScore("localizacao", PESOS["localizacao"], 0.0, "ATR zero")
    dist_atr = (f.close - f.ema_fast) / f.atr
    esticado = d * dist_atr          # positivo = a favor, mas longe demais
    if esticado <= 1.0:
        valor = _clamp(0.6 - abs(esticado - 0.2) * 0.5, -1.0, 1.0)
    else:
        valor = _clamp(0.5 - (esticado - 1.0) * 0.75)
    return FactorScore(
        "localizacao", PESOS["localizacao"], valor,
        f"{dist_atr:+.2f} ATR da EMA9 ({'esticado' if esticado > 2 else 'ok'})",
    )


def _fator_rsi(f: Features, side: Side) -> FactorScore:
    """RSI lido conforme o regime, não como gatilho isolado.

    Em tendência, RSI alto confirma força. Em lateralidade, RSI alto é
    exaustão. Ler RSI sem regime é a origem clássica do 'vendi a alta e ela
    continuou subindo'.
    """
    d = _sinal_direcional(side)
    r = f.rsi
    em_tendencia = f.regime in (Regime.TENDENCIA_ALTA, Regime.TENDENCIA_BAIXA)
    if em_tendencia:
        alvo = 60.0 if d > 0 else 40.0
        valor = _clamp(1.0 - abs(r - alvo) / 25.0)
        if (d > 0 and r > 78) or (d < 0 and r < 22):
            valor = _clamp(valor - 0.6)   # sobrecompra extrema mesmo em tendência
        detalhe = f"RSI {r:.1f} em tendência (alvo ~{alvo:.0f})"
    else:
        # Reversão: favorece comprar fundo / vender topo da faixa.
        valor = _clamp(d * (50.0 - r) / 20.0)
        detalhe = f"RSI {r:.1f} em faixa (reversão à média)"
    return FactorScore("rsi_contexto", PESOS["rsi_contexto"], valor, detalhe)


def _fator_volume(f: Features, side: Side) -> FactorScore:
    """Volume confirma movimento; volume abaixo da média o desmente."""
    vr = f.volume_ratio
    valor = _clamp((vr - 1.0) / 0.8)
    if vr < 0.6:
        valor = _clamp(valor - 0.3)
    return FactorScore("volume", PESOS["volume"], valor,
                       f"volume {vr:.2f}x a média de 20")


def _fator_volatilidade(f: Features, cfg: SignalConfig) -> FactorScore:
    """Volatilidade útil é faixa intermediária. Muito baixa = sem movimento;
    muito alta = stop estourado por ruído."""
    a = f.atr_pct
    if a <= 0:
        valor = -1.0
    elif a < 0.4:
        valor = -0.5
    elif a <= 2.5:
        valor = 1.0 - abs(a - 1.2) / 2.0
    else:
        valor = _clamp(0.4 - (a - 2.5) / (cfg.max_atr_pct - 2.5 + 1e-9))
    return FactorScore("volatilidade", PESOS["volatilidade"], _clamp(valor),
                       f"ATR {a:.2f}% do preço")


def _fator_funding(snap: MarketSnapshot | None, side: Side,
                   cfg: SignalConfig) -> FactorScore:
    """Funding é termômetro de posicionamento, usado de forma contrária.

    Funding muito positivo = multidão comprada e pagando para ficar comprada;
    entrar long ali é aumentar a fila de liquidação. O sinal é invertido de
    propósito.
    """
    if snap is None:
        return FactorScore("funding", PESOS["funding"], 0.0, "sem dado de funding")
    d = _sinal_direcional(side)
    fr = snap.funding_rate
    valor = _clamp(-d * fr / cfg.max_funding_abs)
    if abs(fr) > cfg.max_funding_abs * 2:
        valor = _clamp(valor - 0.4)
    return FactorScore("funding", PESOS["funding"], valor,
                       f"funding {fr * 100:+.4f}% por período")


# ----------------------------------------------------------------- plano/score
def montar_plano(f: Features, side: Side, *, atr_mult_stop: float = 1.6,
                 alvos_r: Sequence[float] = (1.8, 3.0, 4.5)) -> dict:
    """Define stop e alvos. O stop vem primeiro, sempre.

    O stop é o maior valor entre (a) ATR * multiplicador e (b) a borda do
    canal recente — para não ficar dentro do ruído da estrutura atual.
    """
    atr_stop = f.atr * atr_mult_stop
    if side is Side.LONG:
        estrutural = f.close - f.donchian_low
        dist = max(atr_stop, min(estrutural * 1.02, atr_stop * 2.2))
        stop = f.close - dist
        alvos = [f.close + dist * r for r in alvos_r]
    else:
        estrutural = f.donchian_high - f.close
        dist = max(atr_stop, min(estrutural * 1.02, atr_stop * 2.2))
        stop = f.close + dist
        alvos = [f.close - dist * r for r in alvos_r]
    if dist <= 0 or stop <= 0:
        raise ValueError(f"plano inválido para {f.symbol}: dist={dist}, stop={stop}")
    return {
        "entry": f.close,
        "stop_loss": stop,
        "take_profits": alvos,
        "risco_abs": dist,
        # R:R do primeiro alvo — é o que decide se vale entrar.
        "risk_reward": alvos_r[0],
    }


def prob_acerto_ajustada(hist: BacktestStats | None) -> float:
    """Win rate histórico encolhido para o prior conforme a amostra.

    Com 10 trades, o histórico pesa 10/(10+25) = 29%. Com 200 trades, pesa
    89%. É o que impede que uma amostra pequena produza número irreal.
    """
    if hist is None or hist.trades <= 0:
        return PRIOR_WIN_RATE
    n = float(hist.trades)
    return (hist.win_rate * n + PRIOR_WIN_RATE * PRIOR_PESO) / (n + PRIOR_PESO)


def _retorno_esperado_r(prob: float, hist: BacktestStats | None,
                        rr: float) -> float:
    """Expectativa em R. Usa a expectativa medida quando há amostra; senão
    calcula pelo R:R do plano assumindo perda de 1R no erro."""
    if hist is not None and hist.trades >= 20:
        return hist.expectancy_r
    return prob * rr - (1.0 - prob) * 1.0


def classificar(score: float, hist: BacktestStats | None,
                cfg: SignalConfig) -> tuple[SignalGrade, list[str]]:
    """Duplo portão: confluência técnica E validação estatística."""
    notas: list[str] = []
    if score < cfg.score_min_grade_b:
        return SignalGrade.REJEITADO, [f"score {score:.1f} abaixo do mínimo "
                                       f"{cfg.score_min_grade_b:.1f}"]

    estatistica_ok = True
    if hist is None or hist.trades < cfg.min_trades_historico:
        n = hist.trades if hist else 0
        notas.append(f"amostra histórica insuficiente ({n} trades, "
                     f"mínimo {cfg.min_trades_historico}) — apenas observação")
        estatistica_ok = False
    else:
        if hist.win_rate < cfg.min_win_rate_historico:
            notas.append(f"win rate histórico {hist.win_rate:.1%} < "
                         f"{cfg.min_win_rate_historico:.0%}")
            estatistica_ok = False
        if hist.profit_factor < cfg.min_profit_factor_historico:
            notas.append(f"profit factor {hist.profit_factor:.2f} < "
                         f"{cfg.min_profit_factor_historico:.2f}")
            estatistica_ok = False
        if hist.expectancy_r < cfg.min_expectancy_r:
            notas.append(f"expectativa {hist.expectancy_r:+.2f}R < "
                         f"{cfg.min_expectancy_r:+.2f}R")
            estatistica_ok = False

    if not estatistica_ok:
        # Técnica boa sem lastro estatístico não vira ordem automática.
        return SignalGrade.C, notas
    if score >= cfg.score_min_grade_a:
        return SignalGrade.A, notas
    return SignalGrade.B, notas


def avaliar(symbol: str, feats: Mapping[str, Features], side: Side,
            cfg: SignalConfig, *, snapshot: MarketSnapshot | None = None,
            hist: BacktestStats | None = None,
            agora_ms: int = 0) -> Signal:
    """Avalia uma direção e devolve o sinal completo (possivelmente rejeitado)."""
    principal = cfg.timeframe_principal
    if principal not in feats:
        raise KeyError(f"features do TF principal ({principal}) ausentes")
    f = feats[principal]

    fatores = [
        _fator_alinhamento(feats, principal, side),
        _fator_forca_tendencia(f, side),
        _fator_momentum(f, side),
        _fator_estrutura(f, side),
        _fator_localizacao(f, side),
        _fator_rsi(f, side),
        _fator_volume(f, side),
        _fator_volatilidade(f, cfg),
        _fator_funding(snapshot, side, cfg),
    ]
    # Soma ponderada em [-1, +1] mapeada para 0..100.
    bruto = sum(x.contribuicao for x in fatores)
    score = _clamp(bruto) * 50.0 + 50.0

    plano = montar_plano(f, side, atr_mult_stop=cfg.atr_mult_stop,
                         alvos_r=cfg.alvos_r)
    prob = prob_acerto_ajustada(hist)
    grade, notas = classificar(score, hist, cfg)

    # Filtros duros de mercado: reprovam independentemente do score.
    if f.atr_pct > cfg.max_atr_pct:
        grade = SignalGrade.REJEITADO
        notas.append(f"volatilidade {f.atr_pct:.2f}% acima do limite "
                     f"{cfg.max_atr_pct:.1f}%")
    if snapshot and snapshot.volume_24h_usd < cfg.min_volume_24h_usd:
        grade = SignalGrade.REJEITADO
        notas.append(f"liquidez 24h US$ {snapshot.volume_24h_usd:,.0f} abaixo do "
                     f"mínimo US$ {cfg.min_volume_24h_usd:,.0f}")
    if plano["risk_reward"] < 1.0:
        grade = SignalGrade.REJEITADO
        notas.append("relação risco/retorno abaixo de 1:1")

    invalidacao = (
        f"Tese anulada se o preço fechar {'abaixo' if side is Side.LONG else 'acima'} "
        f"de {plano['stop_loss']:.6g} no {principal}, ou se a EMA9 cruzar "
        f"{'abaixo' if side is Side.LONG else 'acima'} da EMA21."
    )

    return Signal(
        symbol=symbol,
        timeframe=principal,
        side=side,
        grade=grade,
        score=score,
        entry=plano["entry"],
        stop_loss=plano["stop_loss"],
        take_profits=plano["take_profits"],
        risk_reward=plano["risk_reward"],
        atr=f.atr,
        regime=f.regime,
        fatores=fatores,
        hist=hist,
        prob_acerto_estimada=prob,
        retorno_esperado_r=_retorno_esperado_r(prob, hist, plano["risk_reward"]),
        invalidacao=invalidacao,
        gerado_em=agora_ms,
        notas=notas,
    )


def melhor_direcao(symbol: str, feats: Mapping[str, Features], cfg: SignalConfig,
                   *, snapshot: MarketSnapshot | None = None,
                   hist_long: BacktestStats | None = None,
                   hist_short: BacktestStats | None = None,
                   agora_ms: int = 0) -> Signal:
    """Avalia long e short e devolve o lado mais bem pontuado.

    Avaliar os dois lados é intencional: evita o viés de só procurar compra em
    mercado que está, na verdade, em tendência de baixa.
    """
    long_sig = avaliar(symbol, feats, Side.LONG, cfg, snapshot=snapshot,
                       hist=hist_long, agora_ms=agora_ms)
    short_sig = avaliar(symbol, feats, Side.SHORT, cfg, snapshot=snapshot,
                        hist=hist_short, agora_ms=agora_ms)
    return long_sig if long_sig.score >= short_sig.score else short_sig
