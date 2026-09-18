"""Gerador determinístico de mercado para demo, teste e CI.

Não serve para prever nada. Serve para que toda a cadeia — features, score,
backtest, gestão de risco, execução em papel e painel — possa rodar e ser
testada sem depender da rede ou de chave de API.

O processo gera regimes alternados (tendência / lateral / choque de
volatilidade) com clusterização de volatilidade, porque um gerador puramente
gaussiano faria qualquer estratégia parecer melhor do que é.
"""
from __future__ import annotations

import hashlib
import math
import random
import time

from ..config import tf_ms
from ..models import Candle, MarketSnapshot
from .base import ExchangeError

PRECO_BASE = {
    "BTCUSDT": 64000.0, "ETHUSDT": 3100.0, "SOLUSDT": 150.0,
    "BNBUSDT": 580.0, "XRPUSDT": 0.52, "ADAUSDT": 0.45,
    "AVAXUSDT": 28.0, "LINKUSDT": 14.5, "DOGEUSDT": 0.13,
    "TONUSDT": 6.8, "ARBUSDT": 0.85, "OPUSDT": 1.9,
    "APTUSDT": 8.5, "SUIUSDT": 1.35, "NEARUSDT": 5.2,
    "INJUSDT": 22.0, "ATOMUSDT": 7.4, "DOTUSDT": 6.1,
    "LTCUSDT": 78.0, "MATICUSDT": 0.58,
}


def _seed(*partes: object) -> int:
    h = hashlib.sha256("|".join(str(p) for p in partes).encode()).hexdigest()
    return int(h[:16], 16)


class SyntheticProvider:
    """Implementa o contrato MarketDataProvider com dados reprodutíveis."""

    # Tamanho da série canônica por símbolo/timeframe. Qualquer janela pedida
    # é uma FATIA desta série, nunca uma série nova — sem isso, paginar para
    # trás devolveria segmentos incoerentes entre si.
    BARRAS_CANONICAS = 12_000

    def __init__(self, seed: int = 20240918, agora_ms: int | None = None,
                 vol_anual: float = 0.65):
        self.seed = seed
        self.agora_ms = agora_ms or int(time.time() // 3600 * 3600 * 1000)
        self.vol_anual = vol_anual
        self._cache: dict[tuple[str, str], list[Candle]] = {}

    # ------------------------------------------------------------------ API
    def symbols(self) -> list[str]:
        return sorted(PRECO_BASE)

    def _exigir_conhecido(self, symbol: str) -> str:
        """Recusa símbolo fora do universo simulado.

        Gerar uma série para qualquer texto faria um erro de digitação virar
        uma "análise" plausível, com preço e score inventados.
        """
        sym = symbol.upper()
        if sym not in PRECO_BASE:
            raise ExchangeError(
                f"símbolo {symbol!r} não existe no provider sintético; "
                f"disponíveis: {', '.join(sorted(PRECO_BASE))}")
        return sym

    def candles(self, symbol: str, timeframe: str, limit: int = 300,
                end_ms: int | None = None) -> list[Candle]:
        serie = self._canonica(self._exigir_conhecido(symbol), timeframe)
        if end_ms is None:
            janela = serie
        else:
            janela = [c for c in serie if c.ts <= end_ms]
        return janela[-max(1, limit):]

    def _canonica(self, symbol: str, timeframe: str) -> list[Candle]:
        chave = (symbol, timeframe)
        if chave not in self._cache:
            passo = tf_ms(timeframe)
            fim = self.agora_ms - (self.agora_ms % passo)
            inicio = fim - (self.BARRAS_CANONICAS - 1) * passo
            self._cache[chave] = self._serie(symbol, timeframe, inicio, passo,
                                             self.BARRAS_CANONICAS)
        return self._cache[chave]

    def ticker(self, symbol: str) -> MarketSnapshot:
        symbol = self._exigir_conhecido(symbol)
        velas = self.candles(symbol, "1H", limit=30)
        preco = velas[-1].close if velas else PRECO_BASE.get(symbol, 100.0)
        rnd = random.Random(_seed(self.seed, symbol, "ticker", self.agora_ms))
        return MarketSnapshot(
            symbol=symbol,
            last_price=preco,
            funding_rate=rnd.gauss(0.0001, 0.00035),
            open_interest=preco * rnd.uniform(5_000, 90_000),
            volume_24h_usd=rnd.uniform(25e6, 2.5e9),
            long_short_ratio=rnd.uniform(0.7, 1.4),
            fetched_at=self.agora_ms,
        )

    def tickers(self) -> dict[str, MarketSnapshot]:
        return {s: self.ticker(s) for s in self.symbols()}

    def funding_rate(self, symbol: str) -> float:
        return self.ticker(symbol).funding_rate

    def contrato(self, symbol: str) -> dict:
        symbol = self._exigir_conhecido(symbol)
        preco = PRECO_BASE[symbol]
        return {
            "symbol": symbol,
            "price_place": 2 if preco > 10 else 5,
            "volume_place": 3 if preco > 10 else 0,
            "size_multiplier": 0.001 if preco > 10 else 1.0,
            "min_trade_num": 0.001 if preco > 10 else 1.0,
            "min_trade_usdt": 5.0,
            "max_leverage": 20.0,
        }

    # -------------------------------------------------------------- interno
    def _serie(self, symbol: str, timeframe: str, inicio: int,
               passo: int, n: int) -> list[Candle]:
        rnd = random.Random(_seed(self.seed, symbol, timeframe))
        ancora = PRECO_BASE.get(symbol, 100.0)
        preco = ancora * rnd.uniform(0.75, 1.25)

        barras_ano = 365 * 24 * 3600 * 1000 / passo
        vol_barra = self.vol_anual / math.sqrt(barras_ano)

        velas: list[Candle] = []
        drift = 0.0
        vol_mult = 1.0
        restante_regime = 0

        for i in range(n):
            if restante_regime <= 0:
                # Sorteia um novo regime com duração variável.
                regime = rnd.choices(
                    ["tendencia", "lateral", "choque"], weights=[0.42, 0.45, 0.13])[0]
                restante_regime = rnd.randint(25, 110)
                if regime == "tendencia":
                    drift = rnd.choice([-1, 1]) * vol_barra * rnd.uniform(0.10, 0.30)
                    vol_mult = rnd.uniform(0.85, 1.25)
                elif regime == "lateral":
                    drift = 0.0
                    vol_mult = rnd.uniform(0.45, 0.8)
                else:
                    drift = rnd.choice([-1, 1]) * vol_barra * rnd.uniform(0.3, 0.7)
                    vol_mult = rnd.uniform(1.8, 3.2)
            restante_regime -= 1

            # GARCH-lite: volatilidade persiste, como no mercado real.
            vol_mult = 0.94 * vol_mult + 0.06 * rnd.uniform(0.5, 1.6)
            sigma = vol_barra * vol_mult
            # Reversão fraca ao âncora: sem ela, 900 barras de drift levam o
            # preço a valores absurdos e distorcem métricas percentuais.
            reversao = -0.004 * math.log(preco / ancora)
            ret = drift + reversao + rnd.gauss(0.0, sigma)
            ret = max(min(ret, 0.18), -0.18)   # trava movimentos absurdos

            abertura = preco
            fechamento = max(abertura * (1.0 + ret), 1e-8)
            corpo = abs(fechamento - abertura)
            pavio = max(corpo * rnd.uniform(0.2, 1.6), abertura * sigma * 0.6)
            maxima = max(abertura, fechamento) + pavio * rnd.uniform(0.15, 1.0)
            minima = max(min(abertura, fechamento) - pavio * rnd.uniform(0.15, 1.0), 1e-9)

            vol_notional = max(abertura * rnd.uniform(40, 900) * (1 + vol_mult), 1.0)
            velas.append(Candle(
                ts=inicio + i * passo,
                open=round(abertura, 8), high=round(maxima, 8),
                low=round(minima, 8), close=round(fechamento, 8),
                volume=round(vol_notional, 4),
            ))
            preco = fechamento
        return velas
