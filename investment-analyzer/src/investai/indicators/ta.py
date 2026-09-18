"""Indicadores técnicos em Python puro.

Todas as funções recebem listas de floats (mais antigo -> mais recente) e
devolvem listas do mesmo tamanho, com `None` nas posições sem dados
suficientes. Isso evita o desalinhamento de índices que é a causa mais comum
de viés de lookahead em backtests.
"""
from __future__ import annotations

from math import sqrt
from typing import Sequence

Num = float | None


def _check(period: int) -> None:
    if period <= 0:
        raise ValueError("period deve ser > 0")


def sma(values: Sequence[float], period: int) -> list[Num]:
    _check(period)
    out: list[Num] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= period:
            acc -= values[i - period]
        if i >= period - 1:
            out[i] = acc / period
    return out


def ema(values: Sequence[float], period: int) -> list[Num]:
    """EMA semeada com a SMA do primeiro bloco (padrão de mercado)."""
    _check(period)
    out: list[Num] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rma(values: Sequence[float], period: int) -> list[Num]:
    """Média móvel de Wilder (usada em RSI/ATR/ADX)."""
    _check(period)
    out: list[Num] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def rsi(closes: Sequence[float], period: int = 14) -> list[Num]:
    _check(period)
    n = len(closes)
    out: list[Num] = [None] * n
    if n < period + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    # Wilder sobre as diferenças (índice 0 não tem variação)
    avg_g = sum(gains[1:period + 1]) / period
    avg_l = sum(losses[1:period + 1]) / period
    def _rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0 if g > 0 else 50.0
        rs = g / l
        return 100.0 - (100.0 / (1.0 + rs))
    out[period] = _rsi(avg_g, avg_l)
    for i in range(period + 1, n):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        out[i] = _rsi(avg_g, avg_l)
    return out


def true_range(highs: Sequence[float], lows: Sequence[float],
               closes: Sequence[float]) -> list[float]:
    n = len(closes)
    tr = [0.0] * n
    if n:
        tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return tr


def atr(highs: Sequence[float], lows: Sequence[float],
        closes: Sequence[float], period: int = 14) -> list[Num]:
    return rma(true_range(highs, lows, closes), period)


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[list[Num], list[Num], list[Num]]:
    ef, es = ema(closes, fast), ema(closes, slow)
    line: list[Num] = [
        (a - b) if (a is not None and b is not None) else None
        for a, b in zip(ef, es)
    ]
    # A linha de sinal é EMA apenas sobre o trecho válido, reposicionada.
    valid = [(i, v) for i, v in enumerate(line) if v is not None]
    sig: list[Num] = [None] * len(closes)
    hist: list[Num] = [None] * len(closes)
    if valid:
        sub = ema([v for _, v in valid], signal)
        for (idx, _), sv in zip(valid, sub):
            sig[idx] = sv
            if sv is not None and line[idx] is not None:
                hist[idx] = line[idx] - sv
    return line, sig, hist


def bollinger(closes: Sequence[float], period: int = 20,
              mult: float = 2.0) -> tuple[list[Num], list[Num], list[Num]]:
    _check(period)
    mid = sma(closes, period)
    up: list[Num] = [None] * len(closes)
    lo: list[Num] = [None] * len(closes)
    for i in range(len(closes)):
        m = mid[i]
        if m is None:
            continue
        window = closes[i - period + 1:i + 1]
        var = sum((x - m) ** 2 for x in window) / period
        sd = sqrt(var)
        up[i] = m + mult * sd
        lo[i] = m - mult * sd
    return up, mid, lo


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> tuple[list[Num], list[Num], list[Num]]:
    """Retorna (adx, +DI, -DI)."""
    _check(period)
    n = len(closes)
    if n < 2:
        return [None] * n, [None] * n, [None] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
    tr_s = rma(true_range(highs, lows, closes), period)
    pdm_s = rma(plus_dm, period)
    mdm_s = rma(minus_dm, period)
    di_p: list[Num] = [None] * n
    di_m: list[Num] = [None] * n
    dx: list[Num] = [None] * n
    for i in range(n):
        t, p, m = tr_s[i], pdm_s[i], mdm_s[i]
        if t is None or p is None or m is None or t == 0:
            continue
        di_p[i] = 100.0 * p / t
        di_m[i] = 100.0 * m / t
        denom = di_p[i] + di_m[i]
        dx[i] = 100.0 * abs(di_p[i] - di_m[i]) / denom if denom else 0.0
    valid = [(i, v) for i, v in enumerate(dx) if v is not None]
    adx_out: list[Num] = [None] * n
    if len(valid) >= period:
        sub = rma([v for _, v in valid], period)
        for (idx, _), sv in zip(valid, sub):
            adx_out[idx] = sv
    return adx_out, di_p, di_m


def donchian(highs: Sequence[float], lows: Sequence[float],
             period: int = 20) -> tuple[list[Num], list[Num]]:
    """Canal de Donchian EXCLUINDO o candle atual.

    Excluir o candle corrente é essencial: um rompimento precisa ser medido
    contra o passado, não contra si mesmo.
    """
    _check(period)
    n = len(highs)
    hi: list[Num] = [None] * n
    lo: list[Num] = [None] * n
    for i in range(n):
        if i < period:
            continue
        hi[i] = max(highs[i - period:i])
        lo[i] = min(lows[i - period:i])
    return hi, lo


def stoch_rsi(closes: Sequence[float], rsi_period: int = 14,
              stoch_period: int = 14) -> list[Num]:
    r = rsi(closes, rsi_period)
    n = len(closes)
    out: list[Num] = [None] * n
    for i in range(n):
        window = [v for v in r[max(0, i - stoch_period + 1):i + 1] if v is not None]
        if len(window) < stoch_period or r[i] is None:
            continue
        lo, hi = min(window), max(window)
        out[i] = 50.0 if hi == lo else (r[i] - lo) / (hi - lo) * 100.0
    return out


def vwap_rolling(highs: Sequence[float], lows: Sequence[float],
                 closes: Sequence[float], volumes: Sequence[float],
                 period: int = 20) -> list[Num]:
    _check(period)
    n = len(closes)
    out: list[Num] = [None] * n
    for i in range(n):
        if i < period - 1:
            continue
        pv = 0.0
        vol = 0.0
        for j in range(i - period + 1, i + 1):
            typ = (highs[j] + lows[j] + closes[j]) / 3.0
            pv += typ * volumes[j]
            vol += volumes[j]
        out[i] = pv / vol if vol else closes[i]
    return out


def slope_pct(values: Sequence[Num], lookback: int = 5) -> Num:
    """Inclinação da série em % relativo, olhando `lookback` barras atrás."""
    if len(values) <= lookback:
        return None
    cur, past = values[-1], values[-1 - lookback]
    if cur is None or past is None or past == 0:
        return None
    return (cur - past) / abs(past) * 100.0


def zscore(values: Sequence[float], period: int = 50) -> Num:
    if len(values) < period:
        return None
    window = values[-period:]
    mean = sum(window) / period
    var = sum((x - mean) ** 2 for x in window) / period
    sd = sqrt(var)
    return 0.0 if sd == 0 else (values[-1] - mean) / sd
