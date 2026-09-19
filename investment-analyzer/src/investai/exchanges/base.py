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


class ExchangeUnreachable(ExchangeError):
    """A requisição não chegou à exchange: DNS, TLS, proxy, timeout.

    Separado de `ExchangeError` porque a causa e a correção são outras. Uma
    resposta de erro DA exchange aponta para a chave, o parâmetro ou a conta;
    não chegar até ela aponta para a rede — proxy corporativo, firewall,
    bloqueio por região. Tratar os dois como a mesma coisa faz o operador
    procurar defeito no conector quando o problema está no caminho.
    """


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
