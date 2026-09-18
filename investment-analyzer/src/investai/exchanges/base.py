"""Contrato que qualquer exchange/provider precisa cumprir."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Candle, MarketSnapshot, Position, Side


class ExchangeError(RuntimeError):
    """Erro devolvido pela exchange (com código, quando disponível)."""

    def __init__(self, mensagem: str, codigo: str = "", payload: object = None):
        super().__init__(mensagem)
        self.codigo = codigo
        self.payload = payload


class InsufficientPermissions(ExchangeError):
    """Chave de API sem permissão de trade (ou somente leitura)."""


@runtime_checkable
class MarketDataProvider(Protocol):
    def candles(self, symbol: str, timeframe: str, limit: int = 300,
                end_ms: int | None = None) -> list[Candle]: ...

    def ticker(self, symbol: str) -> MarketSnapshot: ...

    def symbols(self) -> list[str]: ...


@runtime_checkable
class TradingProvider(Protocol):
    def saldo_usdt(self) -> float: ...

    def posicoes(self) -> list[Position]: ...

    def abrir_posicao(self, symbol: str, side: Side, size: float,
                      leverage: float, stop_loss: float,
                      take_profit: float | None, client_oid: str) -> dict: ...

    def fechar_posicao(self, symbol: str, side: Side,
                       size: float | None = None) -> dict: ...

    def ajustar_stop(self, symbol: str, side: Side, novo_stop: float) -> dict: ...
