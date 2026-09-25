"""Camada de acesso a dados: paginação, cache em memória e tolerância a falha.

A Bitget devolve no máximo 1000 candles por chamada. Para medir estatística
com amostra utilizável é preciso muito mais que isso, então o hub pagina para
trás pelo campo `endTime` e concatena, deduplicando por timestamp.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .config import tf_ms
from .models import Candle, MarketSnapshot

log = logging.getLogger("investai.datahub")

MAX_POR_PAGINA = 1000


class _Provider(Protocol):
    def candles(self, symbol: str, timeframe: str, limit: int = 300,
                end_ms: int | None = None) -> list[Candle]: ...

    def ticker(self, symbol: str) -> MarketSnapshot: ...


@dataclass(slots=True)
class _Entrada:
    dados: Any
    expira_em: float


class DataHub:
    def __init__(self, provider: _Provider, ttl_candles: float = 60.0,
                 ttl_ticker: float = 20.0):
        self.provider = provider
        self.ttl_candles = ttl_candles
        self.ttl_ticker = ttl_ticker
        self._cache: dict[str, _Entrada] = {}

    # ------------------------------------------------------------------ cache
    def _get(self, chave: str) -> Any | None:
        e = self._cache.get(chave)
        if e is None:
            return None
        if e.expira_em < time.monotonic():
            del self._cache[chave]
            return None
        return e.dados

    def _set(self, chave: str, dados: Any, ttl: float) -> None:
        self._cache[chave] = _Entrada(dados, time.monotonic() + ttl)

    def limpar_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------ dados
    def candles(self, symbol: str, timeframe: str, limit: int = 300,
                usar_cache: bool = True) -> list[Candle]:
        chave = f"c:{symbol}:{timeframe}:{limit}"
        if usar_cache:
            cached = self._get(chave)
            if cached is not None:
                return cached
        velas = self.provider.candles(symbol, timeframe, limit=min(limit, MAX_POR_PAGINA))
        self._set(chave, velas, self.ttl_candles)
        return velas

    def historico(self, symbol: str, timeframe: str, barras: int = 4000,
                  usar_cache: bool = True) -> list[Candle]:
        """Busca `barras` candles paginando para trás.

        Para se a exchange parar de devolver dados novos (série mais curta que
        o pedido) em vez de entrar em laço infinito.
        """
        chave = f"h:{symbol}:{timeframe}:{barras}"
        if usar_cache:
            cached = self._get(chave)
            if cached is not None:
                return cached

        passo = tf_ms(timeframe)
        por_pagina = min(MAX_POR_PAGINA, barras)
        acumulado: dict[int, Candle] = {}
        end_ms: int | None = None
        paginas_max = max(1, (barras // por_pagina) + 2)

        for _ in range(paginas_max):
            pagina = self.provider.candles(symbol, timeframe,
                                           limit=por_pagina, end_ms=end_ms)
            if not pagina:
                break
            novos = [c for c in pagina if c.ts not in acumulado]
            if not novos:
                break     # a exchange repetiu a página: fim do histórico
            for c in pagina:
                acumulado[c.ts] = c
            if len(acumulado) >= barras:
                break
            end_ms = min(c.ts for c in pagina) - passo

        velas = sorted(acumulado.values(), key=lambda c: c.ts)[-barras:]
        self._set(chave, velas, self.ttl_candles * 10)
        return velas

    def ticker(self, symbol: str) -> MarketSnapshot | None:
        """Devolve None em vez de propagar erro: a falta de ticker degrada a
        análise (um fator a menos), mas não deve derrubar o scan inteiro."""
        chave = f"t:{symbol}"
        cached = self._get(chave)
        if cached is not None:
            return cached
        try:
            snap = self.provider.ticker(symbol)
        except Exception as exc:                      # noqa: BLE001
            log.warning("ticker de %s indisponível: %s", symbol, exc)
            return None
        self._set(chave, snap, self.ttl_ticker)
        return snap

    def tickers(self, symbols: list[str]) -> dict[str, MarketSnapshot]:
        """Usa o endpoint em lote quando o provider oferece — 1 chamada em vez de N."""
        em_lote = getattr(self.provider, "tickers", None)
        if callable(em_lote):
            chave = "t:all"
            cached = self._get(chave)
            if cached is None:
                try:
                    cached = em_lote()
                    self._set(chave, cached, self.ttl_ticker)
                except Exception as exc:              # noqa: BLE001
                    log.warning("tickers em lote falharam: %s", exc)
                    cached = {}
            return {s: cached[s] for s in symbols if s in cached}
        return {s: t for s in symbols if (t := self.ticker(s)) is not None}
