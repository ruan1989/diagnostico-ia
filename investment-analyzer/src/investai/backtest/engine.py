"""Backtest walk-forward barra a barra.

Premissas — todas escolhidas para o lado pessimista, porque um backtest
otimista é pior que nenhum backtest:

* O sinal nasce no FECHAMENTO da barra i e é executado na ABERTURA da barra
  i+1. Nunca há execução no mesmo candle que gerou o sinal.
* Se stop e alvo são tocados dentro da mesma barra, assume-se que o STOP veio
  primeiro (não há dado intrabar para desempatar).
* Taxa taker na entrada e em cada saída, mais slippage nos dois sentidos.
* Custo de funding debitado a cada 8h de posição aberta.
* Nenhuma posição sobrevive ao fim da série: a última é encerrada a mercado,
  para não inflar o resultado com um trade aberto e lucrativo no papel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..analysis.confluence import avaliar
from ..analysis.features import BARRAS_MINIMAS, SerieFeatures
from ..config import ExecutionConfig, SignalConfig
from ..models import (
    BacktestStats, Candle, MarketSnapshot, Side, SignalGrade, Trade,
)
from .metrics import calcular

FUNDING_INTERVALO_MS = 8 * 3600 * 1000


@dataclass(slots=True)
class BacktestResult:
    symbol: str
    timeframe: str
    side_filtro: str
    stats: BacktestStats
    trades: list[Trade] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    barras_avaliadas: int = 0
    sinais_gerados: int = 0
    periodo_inicio_ms: int = 0
    periodo_fim_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "side_filtro": self.side_filtro,
            "stats": self.stats.to_dict(),
            "barras_avaliadas": self.barras_avaliadas,
            "sinais_gerados": self.sinais_gerados,
            "periodo_inicio_ms": self.periodo_inicio_ms,
            "periodo_fim_ms": self.periodo_fim_ms,
            "trades": [t.to_dict() for t in self.trades],
            "equity": self.equity,
        }


@dataclass(slots=True)
class _PosicaoSim:
    symbol: str
    side: Side
    entry: float
    stop: float
    alvos: list[float]
    size: float
    risco_abs: float
    risco_usd: float
    abertura_ms: int
    barra_abertura: int
    taxas: float = 0.0
    realizado: float = 0.0
    fracao_restante: float = 1.0
    alvos_atingidos: int = 0
    breakeven_aplicado: bool = False


def _preco_com_slippage(preco: float, side: Side, saindo: bool,
                        slippage_pct: float) -> float:
    """Slippage sempre contra o operador — entrada mais cara, saída mais barata."""
    fator = slippage_pct / 100.0
    if (side is Side.LONG) != saindo:
        return preco * (1.0 + fator)
    return preco * (1.0 - fator)


def rodar_backtest(symbol: str, timeframe: str, velas: Sequence[Candle],
                   sig_cfg: SignalConfig, exec_cfg: ExecutionConfig, *,
                   side_filtro: Side | None = None,
                   score_minimo: float | None = None,
                   snapshot: MarketSnapshot | None = None,
                   funding_rate: float = 0.0001,
                   capital: float | None = None) -> BacktestResult:
    """Simula a estratégia de confluência em uma série histórica.

    `side_filtro` restringe a longs ou shorts — útil para medir a estatística
    de cada direção separadamente, que é o que alimenta o portão de qualidade
    dos sinais ao vivo.
    """
    capital_inicial = capital if capital is not None else exec_cfg.capital_inicial_usd
    if capital_inicial <= 0:
        raise ValueError("capital inicial deve ser > 0")

    serie = SerieFeatures(symbol, timeframe, velas)
    limiar = score_minimo if score_minimo is not None else sig_cfg.score_min_operavel
    lados = [side_filtro] if side_filtro else [Side.LONG, Side.SHORT]

    equity = capital_inicial
    curva: list[float] = [capital_inicial]
    trades: list[Trade] = []
    pos: _PosicaoSim | None = None
    sinais = 0
    # Risco fixo por operação em fração do capital corrente.
    risco_frac = 0.01

    inicio = max(serie.primeiro_indice_valido, BARRAS_MINIMAS - 1)
    for i in range(inicio, len(velas) - 1):
        proxima = velas[i + 1]

        # ---------------------------------------------------- gestão da posição
        if pos is not None:
            pos, fechou = _processar_barra(pos, proxima, i + 1, exec_cfg,
                                           funding_rate)
            if fechou is not None:
                trades.append(fechou)
                equity += fechou.pnl_usd
                curva.append(equity)
                pos = None
                if equity <= 0:
                    break     # conta zerada: simulação termina
            if pos is not None:
                continue      # uma posição por símbolo de cada vez

        # ------------------------------------------------------ geração do sinal
        feats = {timeframe: serie.at(i)}
        cfg_mono = _cfg_mono_tf(sig_cfg, timeframe)
        melhor = None
        for lado in lados:
            s = avaliar(symbol, feats, lado, cfg_mono, snapshot=snapshot,
                        hist=None, agora_ms=velas[i].ts)
            if melhor is None or s.score > melhor.score:
                melhor = s
        assert melhor is not None
        if melhor.score < limiar or melhor.grade is SignalGrade.REJEITADO:
            continue
        sinais += 1

        # ----------------------------------------------------------- abre trade
        entrada = _preco_com_slippage(proxima.open, melhor.side, False,
                                      exec_cfg.slippage_pct)
        risco_abs = abs(entrada - melhor.stop_loss)
        if risco_abs <= 0:
            continue
        risco_usd = equity * risco_frac
        size = risco_usd / risco_abs
        notional = size * entrada
        # Respeita a alavancagem implícita máxima do backtest.
        max_notional = equity * 5.0
        if notional > max_notional:
            size *= max_notional / notional
            notional = max_notional
            risco_usd = size * risco_abs
        if size <= 0:
            continue

        pos = _PosicaoSim(
            symbol=symbol, side=melhor.side, entry=entrada, stop=melhor.stop_loss,
            alvos=list(melhor.take_profits), size=size, risco_abs=risco_abs,
            risco_usd=risco_usd, abertura_ms=proxima.ts, barra_abertura=i + 1,
            taxas=notional * exec_cfg.taxa_taker_pct / 100.0,
        )

    # -------------------------------------------- encerra posição remanescente
    if pos is not None and len(velas) > 1:
        ultima = velas[-1]
        saida = _preco_com_slippage(ultima.close, pos.side, True,
                                    exec_cfg.slippage_pct)
        trades.append(_encerrar(pos, saida, ultima.ts, len(velas) - 1,
                                "fim_da_serie", exec_cfg, funding_rate))
        equity += trades[-1].pnl_usd
        curva.append(equity)

    stats = calcular(trades, curva, capital_inicial)
    return BacktestResult(
        symbol=symbol, timeframe=timeframe,
        side_filtro=side_filtro.value if side_filtro else "ambos",
        stats=stats, trades=trades, equity=curva,
        barras_avaliadas=max(0, len(velas) - 1 - inicio),
        sinais_gerados=sinais,
        periodo_inicio_ms=velas[0].ts, periodo_fim_ms=velas[-1].ts,
    )


def _cfg_mono_tf(cfg: SignalConfig, timeframe: str) -> SignalConfig:
    """Cópia da config apontando o TF principal para o timeframe simulado.

    O backtest roda em um único timeframe; forçar alinhamento multi-TF aqui
    criaria dependência de dados que a série simulada não tem.
    """
    return SignalConfig(
        score_min_grade_a=cfg.score_min_grade_a,
        score_min_grade_b=cfg.score_min_grade_b,
        score_min_operavel=cfg.score_min_operavel,
        min_trades_historico=0,
        min_win_rate_historico=0.0,
        min_profit_factor_historico=0.0,
        min_expectancy_r=-99.0,
        max_atr_pct=cfg.max_atr_pct,
        min_volume_24h_usd=0.0,
        alvos_r=cfg.alvos_r,
        atr_mult_stop=cfg.atr_mult_stop,
        max_funding_abs=cfg.max_funding_abs,
        timeframes=(timeframe,),
        timeframe_principal=timeframe,
        exigir_alinhamento_multi_tf=False,
    )


def _custo_funding(pos: _PosicaoSim, agora_ms: int, taxa: float) -> float:
    """Funding pago por período de 8h de posição aberta.

    Long paga quando a taxa é positiva; short recebe. Sinal invertido para
    short de propósito.
    """
    periodos = max(0, int((agora_ms - pos.abertura_ms) // FUNDING_INTERVALO_MS))
    if periodos == 0:
        return 0.0
    notional = pos.size * pos.fracao_restante * pos.entry
    direcao = 1.0 if pos.side is Side.LONG else -1.0
    return notional * taxa * periodos * direcao


def _processar_barra(pos: _PosicaoSim, vela: Candle, indice: int,
                     exec_cfg: ExecutionConfig,
                     funding_rate: float) -> tuple[_PosicaoSim | None, Trade | None]:
    """Aplica a barra à posição: stop, alvos parciais e breakeven."""
    long = pos.side is Side.LONG
    stop_tocado = vela.low <= pos.stop if long else vela.high >= pos.stop

    # Pessimismo deliberado: stop antes de alvo quando ambos caem na barra.
    if stop_tocado:
        saida = _preco_com_slippage(pos.stop, pos.side, True, exec_cfg.slippage_pct)
        motivo = "breakeven" if pos.breakeven_aplicado and (
            (long and pos.stop >= pos.entry) or (not long and pos.stop <= pos.entry)
        ) else "stop_loss"
        return None, _encerrar(pos, saida, vela.ts, indice, motivo,
                               exec_cfg, funding_rate)

    # Alvos parciais, em ordem.
    fracoes = list(exec_cfg.parciais)
    while pos.alvos_atingidos < len(pos.alvos):
        alvo = pos.alvos[pos.alvos_atingidos]
        atingiu = vela.high >= alvo if long else vela.low <= alvo
        if not atingiu:
            break
        idx = pos.alvos_atingidos
        fracao = fracoes[idx] if idx < len(fracoes) else pos.fracao_restante
        fracao = min(fracao, pos.fracao_restante)
        if fracao <= 0:
            break
        saida = _preco_com_slippage(alvo, pos.side, True, exec_cfg.slippage_pct)
        parte = pos.size * fracao
        delta = (saida - pos.entry) if long else (pos.entry - saida)
        pos.realizado += delta * parte
        pos.taxas += parte * saida * exec_cfg.taxa_taker_pct / 100.0
        pos.fracao_restante -= fracao
        pos.alvos_atingidos += 1

        if pos.fracao_restante <= 1e-9:
            return None, _encerrar(pos, saida, vela.ts, indice,
                                   f"alvo_{pos.alvos_atingidos}", exec_cfg,
                                   funding_rate, ja_realizado=True)
        # Após o primeiro alvo o stop vai para o preço de entrada: a operação
        # deixa de poder virar prejuízo.
        if exec_cfg.trailing_apos_tp1 and not pos.breakeven_aplicado:
            pos.stop = pos.entry
            pos.breakeven_aplicado = True

    return pos, None


def _encerrar(pos: _PosicaoSim, preco_saida: float, ts: int, indice: int,
              motivo: str, exec_cfg: ExecutionConfig, funding_rate: float,
              *, ja_realizado: bool = False) -> Trade:
    long = pos.side is Side.LONG
    if not ja_realizado:
        parte = pos.size * pos.fracao_restante
        delta = (preco_saida - pos.entry) if long else (pos.entry - preco_saida)
        pos.realizado += delta * parte
        pos.taxas += parte * preco_saida * exec_cfg.taxa_taker_pct / 100.0

    funding = _custo_funding(pos, ts, funding_rate)
    pnl_liquido = pos.realizado - pos.taxas - funding
    pnl_r = pnl_liquido / pos.risco_usd if pos.risco_usd else 0.0
    return Trade(
        symbol=pos.symbol, side=pos.side, entry=pos.entry, exit=preco_saida, size=pos.size,
        opened_at=pos.abertura_ms, closed_at=ts, pnl_usd=pnl_liquido,
        pnl_r=pnl_r, motivo_saida=motivo,
        fees_usd=pos.taxas + max(funding, 0.0),
        bars_held=indice - pos.barra_abertura,
    )
