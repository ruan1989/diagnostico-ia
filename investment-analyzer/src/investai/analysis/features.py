"""Extração de features a partir de candles.

Regra invariável deste módulo: só usa informação disponível ATÉ o candle
avaliado. Nada de olhar o futuro — é isso que separa um backtest honesto de
um gráfico bonito.
"""
from __future__ import annotations

from typing import Sequence

from ..indicators import adx, atr, bollinger, donchian, ema, macd, rsi, sma
from ..models import Candle, Features, Regime

# Barras mínimas para que todos os indicadores tenham valor válido.
BARRAS_MINIMAS = 210


class DadosInsuficientes(ValueError):
    pass


def classificar_regime(ema_fast: float, ema_slow: float, ema_trend: float,
                       adx_val: float, di_p: float, di_m: float,
                       bb_width_pct: float, atr_pct: float) -> Regime:
    """Traduz indicadores em um dos quatro regimes operacionais.

    A distinção importa porque a mesma configuração de indicadores tem
    expectativa oposta em tendência e em lateralidade.
    """
    tendencia_forte = adx_val >= 22.0
    alta = ema_fast > ema_slow > ema_trend and di_p > di_m
    baixa = ema_fast < ema_slow < ema_trend and di_m > di_p

    if tendencia_forte and alta:
        return Regime.TENDENCIA_ALTA
    if tendencia_forte and baixa:
        return Regime.TENDENCIA_BAIXA
    # Volatilidade alta sem direção definida é o pior cenário para seguir
    # tendência: bandas largas, ADX baixo.
    if atr_pct >= 3.0 and adx_val < 18.0 and bb_width_pct >= 6.0:
        return Regime.VOLATIL_SEM_DIRECAO
    return Regime.LATERAL


def extrair_features(symbol: str, timeframe: str, velas: Sequence[Candle],
                     *, indice: int = -1) -> Features:
    """Calcula as features no candle `indice` (default: o último fechado)."""
    if len(velas) < BARRAS_MINIMAS:
        raise DadosInsuficientes(
            f"{symbol} {timeframe}: {len(velas)} candles; mínimo {BARRAS_MINIMAS}")

    i = indice if indice >= 0 else len(velas) + indice
    if not 0 <= i < len(velas):
        raise IndexError(f"índice {indice} fora da série de {len(velas)} candles")
    if i < BARRAS_MINIMAS - 1:
        raise DadosInsuficientes(
            f"{symbol} {timeframe}: índice {i} tem histórico insuficiente")

    # Fatia até i inclusive — garantia estrutural contra lookahead.
    janela = velas[:i + 1]
    closes = [c.close for c in janela]
    highs = [c.high for c in janela]
    lows = [c.low for c in janela]
    volumes = [c.volume for c in janela]

    ema_f = ema(closes, 9)[-1] or closes[-1]
    ema_s = ema(closes, 21)[-1] or closes[-1]
    ema_t = ema(closes, 200)[-1] or closes[-1]
    rsi_v = rsi(closes, 14)[-1] or 50.0
    m_line, m_sig, m_hist = macd(closes)
    atr_v = atr(highs, lows, closes, 14)[-1] or 0.0
    adx_v, di_p, di_m = adx(highs, lows, closes, 14)
    bb_u, bb_m, bb_l = bollinger(closes, 20, 2.0)
    d_hi, d_lo = donchian(highs, lows, 20)
    vol_media = sma(volumes, 20)[-1] or (volumes[-1] or 1.0)

    close = closes[-1]
    atr_pct = (atr_v / close * 100.0) if close else 0.0
    bb_upper = bb_u[-1] or close
    bb_lower = bb_l[-1] or close
    bb_mid = bb_m[-1] or close
    bb_width_pct = ((bb_upper - bb_lower) / bb_mid * 100.0) if bb_mid else 0.0
    adx_val = adx_v[-1] or 0.0
    dip = di_p[-1] or 0.0
    dim = di_m[-1] or 0.0

    lookback = min(20, len(closes) - 1)
    ret_lb = ((close / closes[-1 - lookback]) - 1.0) * 100.0 if lookback else 0.0

    return Features(
        symbol=symbol,
        timeframe=timeframe,
        close=close,
        ema_fast=ema_f,
        ema_slow=ema_s,
        ema_trend=ema_t,
        rsi=rsi_v,
        macd=m_line[-1] or 0.0,
        macd_signal=m_sig[-1] or 0.0,
        macd_hist=m_hist[-1] or 0.0,
        atr=atr_v,
        atr_pct=atr_pct,
        adx=adx_val,
        di_plus=dip,
        di_minus=dim,
        bb_upper=bb_upper,
        bb_lower=bb_lower,
        bb_width_pct=bb_width_pct,
        donchian_high=d_hi[-1] or close,
        donchian_low=d_lo[-1] or close,
        volume_ratio=(volumes[-1] / vol_media) if vol_media else 1.0,
        ret_pct_lookback=ret_lb,
        regime=classificar_regime(ema_f, ema_s, ema_t, adx_val, dip, dim,
                                  bb_width_pct, atr_pct),
    )


# ---------------------------------------------------------------------------
# Caminho pré-computado: usado pelo backtest.
#
# `extrair_features` recalcula todos os indicadores sobre a fatia [0..i], o que
# torna um backtest de N barras O(N²). A classe abaixo calcula cada série uma
# única vez e devolve as features de qualquer índice em O(1), mantendo a mesma
# garantia de ausência de lookahead: cada posição da série só depende de dados
# até aquela posição (propriedade das próprias funções de indicador).
# ---------------------------------------------------------------------------
class SerieFeatures:
    """Indicadores pré-calculados sobre uma série completa de candles."""

    __slots__ = ("symbol", "timeframe", "velas", "_c", "_h", "_l", "_v",
                 "_ema_f", "_ema_s", "_ema_t", "_rsi", "_macd", "_macd_sig",
                 "_macd_hist", "_atr", "_adx", "_di_p", "_di_m",
                 "_bb_u", "_bb_m", "_bb_l", "_d_hi", "_d_lo", "_vol_ma")

    def __init__(self, symbol: str, timeframe: str, velas: Sequence[Candle]):
        if len(velas) < BARRAS_MINIMAS:
            raise DadosInsuficientes(
                f"{symbol} {timeframe}: {len(velas)} candles; mínimo {BARRAS_MINIMAS}")
        self.symbol = symbol
        self.timeframe = timeframe
        self.velas = list(velas)
        self._c = [c.close for c in velas]
        self._h = [c.high for c in velas]
        self._l = [c.low for c in velas]
        self._v = [c.volume for c in velas]

        self._ema_f = ema(self._c, 9)
        self._ema_s = ema(self._c, 21)
        self._ema_t = ema(self._c, 200)
        self._rsi = rsi(self._c, 14)
        self._macd, self._macd_sig, self._macd_hist = macd(self._c)
        self._atr = atr(self._h, self._l, self._c, 14)
        self._adx, self._di_p, self._di_m = adx(self._h, self._l, self._c, 14)
        self._bb_u, self._bb_m, self._bb_l = bollinger(self._c, 20, 2.0)
        self._d_hi, self._d_lo = donchian(self._h, self._l, 20)
        self._vol_ma = sma(self._v, 20)

    def __len__(self) -> int:
        return len(self.velas)

    @property
    def primeiro_indice_valido(self) -> int:
        return BARRAS_MINIMAS - 1

    def at(self, i: int) -> Features:
        """Features no índice `i`, sem olhar nenhuma barra posterior."""
        if i < self.primeiro_indice_valido or i >= len(self.velas):
            raise DadosInsuficientes(
                f"{self.symbol} {self.timeframe}: índice {i} fora da faixa válida "
                f"[{self.primeiro_indice_valido}, {len(self.velas) - 1}]")
        close = self._c[i]
        atr_v = self._atr[i] or 0.0
        bb_u = self._bb_u[i] or close
        bb_l = self._bb_l[i] or close
        bb_m = self._bb_m[i] or close
        adx_v = self._adx[i] or 0.0
        dip = self._di_p[i] or 0.0
        dim = self._di_m[i] or 0.0
        atr_pct = (atr_v / close * 100.0) if close else 0.0
        bb_width = ((bb_u - bb_l) / bb_m * 100.0) if bb_m else 0.0
        vol_ma = self._vol_ma[i] or (self._v[i] or 1.0)
        lookback = min(20, i)
        ret_lb = ((close / self._c[i - lookback]) - 1.0) * 100.0 if lookback else 0.0
        ema_f = self._ema_f[i] or close
        ema_s = self._ema_s[i] or close
        ema_t = self._ema_t[i] or close

        return Features(
            symbol=self.symbol, timeframe=self.timeframe, close=close,
            ema_fast=ema_f, ema_slow=ema_s, ema_trend=ema_t,
            rsi=self._rsi[i] or 50.0,
            macd=self._macd[i] or 0.0,
            macd_signal=self._macd_sig[i] or 0.0,
            macd_hist=self._macd_hist[i] or 0.0,
            atr=atr_v, atr_pct=atr_pct, adx=adx_v, di_plus=dip, di_minus=dim,
            bb_upper=bb_u, bb_lower=bb_l, bb_width_pct=bb_width,
            donchian_high=self._d_hi[i] or close,
            donchian_low=self._d_lo[i] or close,
            volume_ratio=(self._v[i] / vol_ma) if vol_ma else 1.0,
            ret_pct_lookback=ret_lb,
            regime=classificar_regime(ema_f, ema_s, ema_t, adx_v, dip, dim,
                                      bb_width, atr_pct),
        )
