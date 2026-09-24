"""Cliente REST da Bitget (API v2, contratos USDT-M).

Autenticação por chave de API — a senha da conta não é usada em nenhum ponto.
Endpoints públicos (candles/tickers/funding) funcionam sem credencial; apenas
saldo, posições e ordens exigem assinatura.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Mapping
from urllib.parse import urlencode

import httpx

from ..models import Candle, MarketSnapshot, Position, Side
from .base import (
    ExchangeError, ExchangeUnreachable, InsufficientPermissions,
)
from .keystore import ApiCredentials

log = logging.getLogger("investai.bitget")

BASE_URL = "https://api.bitget.com"
GRANULARIDADE = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1H": "1H", "4H": "4H", "6H": "6H", "12H": "12H", "1D": "1D",
}
# Códigos de erro da Bitget que significam "chave sem permissão de trade".
COD_PERMISSAO = {"40014", "40018", "40034", "40037"}


def _assinar(secret: str, timestamp: str, method: str, path: str, body: str) -> str:
    prehash = f"{timestamp}{method.upper()}{path}{body}"
    digest = hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


# Códigos da Bitget que significam "essa ordem não existe", e não "deu erro".
# A distinção é a base da idempotência: reenviar porque a consulta falhou é o
# caminho para posição dobrada.
CODIGOS_ORDEM_INEXISTENTE = frozenset({
    "22001",   # no order to cancel / order does not exist
    "40109",   # the order does not exist
    "43001",   # the order does not exist
    "40768",   # order does not exist
})


def _ordem_inexistente(exc: Exception) -> bool:
    texto = str(exc)
    if any(c in texto for c in CODIGOS_ORDEM_INEXISTENTE):
        return True
    baixo = texto.lower()
    return "does not exist" in baixo or "no order" in baixo


def _f(valor: Any, default: float = 0.0) -> float:
    """Converte campos da API (que vêm como string, às vezes vazia) em float."""
    if valor is None or valor == "":
        return default
    try:
        return float(valor)
    except (TypeError, ValueError):
        return default


class BitgetClient:
    def __init__(self, credenciais: ApiCredentials | None = None,
                 product_type: str = "USDT-FUTURES",
                 margin_coin: str = "USDT",
                 base_url: str = BASE_URL,
                 timeout: float = 15.0,
                 max_tentativas: int = 4,
                 client: httpx.Client | None = None):
        self.cred = credenciais
        self.product_type = product_type
        self.margin_coin = margin_coin
        self.base_url = base_url.rstrip("/")
        self.max_tentativas = max_tentativas
        self._client = client or httpx.Client(
            base_url=self.base_url, timeout=timeout,
            headers={"Content-Type": "application/json", "locale": "pt-BR"},
        )

    # ------------------------------------------------------------------ infra
    @property
    def autenticado(self) -> bool:
        return self.cred is not None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BitgetClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _headers(self, method: str, path: str, body: str) -> dict[str, str]:
        if self.cred is None:
            raise InsufficientPermissions(
                "operação exige chave de API; conecte a Bitget antes de operar")
        ts = str(int(time.time() * 1000))
        return {
            "ACCESS-KEY": self.cred.api_key,
            "ACCESS-SIGN": _assinar(self.cred.api_secret, ts, method, path, body),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self.cred.passphrase,
            "Content-Type": "application/json",
            "locale": "pt-BR",
        }

    def _request(self, method: str, endpoint: str, *,
                 params: Mapping[str, Any] | None = None,
                 body: Mapping[str, Any] | None = None,
                 assinado: bool = False) -> Any:
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        path = f"{endpoint}?{query}" if query else endpoint
        body_str = json.dumps(body, separators=(",", ":")) if body else ""
        ultimo_erro: Exception | None = None

        for tentativa in range(1, self.max_tentativas + 1):
            headers = self._headers(method, path, body_str) if assinado else None
            try:
                resp = self._client.request(
                    method, path, headers=headers,
                    content=body_str.encode() if body_str else None,
                )
            except httpx.HTTPError as exc:
                ultimo_erro = exc
                if tentativa == self.max_tentativas:
                    raise ExchangeUnreachable(
                        f"não foi possível alcançar a Bitget em {endpoint}: "
                        f"{type(exc).__name__}: {exc}") from exc
                time.sleep(min(2 ** tentativa * 0.5, 8.0))
                continue

            # 429/5xx são transitórios: vale reenviar com espera crescente.
            if resp.status_code == 429 or resp.status_code >= 500:
                if tentativa == self.max_tentativas:
                    raise ExchangeError(
                        f"{endpoint} devolveu HTTP {resp.status_code} após "
                        f"{tentativa} tentativas", codigo=str(resp.status_code))
                espera = float(resp.headers.get("Retry-After") or min(2 ** tentativa * 0.5, 8.0))
                log.warning("Bitget HTTP %s em %s; aguardando %.1fs",
                            resp.status_code, endpoint, espera)
                time.sleep(espera)
                continue

            try:
                payload = resp.json()
            except ValueError as exc:
                raise ExchangeError(
                    f"resposta não-JSON de {endpoint} (HTTP {resp.status_code})") from exc

            codigo = str(payload.get("code", ""))
            if codigo not in {"00000", "0", ""}:
                msg = payload.get("msg", "erro desconhecido")
                if codigo in COD_PERMISSAO:
                    raise InsufficientPermissions(
                        f"chave de API sem permissão para {endpoint}: {msg} "
                        f"(habilite 'Trade' na chave, mantenha saque desabilitado)",
                        codigo=codigo, payload=payload)
                raise ExchangeError(f"Bitget {codigo} em {endpoint}: {msg}",
                                    codigo=codigo, payload=payload)
            if resp.status_code >= 400:
                raise ExchangeError(f"{endpoint} HTTP {resp.status_code}",
                                    codigo=str(resp.status_code), payload=payload)
            return payload.get("data")

        raise ExchangeError(f"{endpoint} esgotou tentativas: {ultimo_erro}")

    # --------------------------------------------------------------- público
    def server_time_ms(self) -> int:
        data = self._request("GET", "/api/v2/public/time")
        return int(_f(data.get("serverTime") if isinstance(data, dict) else data))

    def candles(self, symbol: str, timeframe: str, limit: int = 300,
                end_ms: int | None = None) -> list[Candle]:
        if timeframe not in GRANULARIDADE:
            raise ValueError(f"timeframe não suportado pela Bitget: {timeframe}")
        data = self._request("GET", "/api/v2/mix/market/candles", params={
            "symbol": symbol,
            "productType": self.product_type,
            "granularity": GRANULARIDADE[timeframe],
            "limit": min(int(limit), 1000),
            "endTime": end_ms,
        }) or []
        velas = [
            Candle(ts=int(row[0]), open=_f(row[1]), high=_f(row[2]),
                   low=_f(row[3]), close=_f(row[4]), volume=_f(row[5]))
            for row in data if len(row) >= 6
        ]
        velas.sort(key=lambda c: c.ts)
        return velas

    def ticker(self, symbol: str) -> MarketSnapshot:
        data = self._request("GET", "/api/v2/mix/market/ticker", params={
            "symbol": symbol, "productType": self.product_type}) or []
        item = data[0] if isinstance(data, list) and data else (data or {})
        return self._snapshot(item, symbol)

    def tickers(self) -> dict[str, MarketSnapshot]:
        data = self._request("GET", "/api/v2/mix/market/tickers", params={
            "productType": self.product_type}) or []
        out: dict[str, MarketSnapshot] = {}
        for item in data:
            sym = item.get("symbol", "")
            if sym:
                out[sym] = self._snapshot(item, sym)
        return out

    def _snapshot(self, item: Mapping[str, Any], symbol: str) -> MarketSnapshot:
        return MarketSnapshot(
            symbol=symbol,
            last_price=_f(item.get("lastPr") or item.get("last")),
            funding_rate=_f(item.get("fundingRate")),
            open_interest=_f(item.get("holdingAmount") or item.get("openInterest")),
            volume_24h_usd=_f(item.get("usdtVolume") or item.get("quoteVolume")),
            fetched_at=int(time.time() * 1000),
        )

    def funding_rate(self, symbol: str) -> float:
        data = self._request("GET", "/api/v2/mix/market/current-fund-rate", params={
            "symbol": symbol, "productType": self.product_type}) or []
        item = data[0] if isinstance(data, list) and data else (data or {})
        return _f(item.get("fundingRate"))

    def symbols(self) -> list[str]:
        data = self._request("GET", "/api/v2/mix/market/contracts", params={
            "productType": self.product_type}) or []
        return sorted(
            c["symbol"] for c in data
            if c.get("symbol") and c.get("symbolStatus", "normal") == "normal"
        )

    def contrato(self, symbol: str) -> dict[str, Any]:
        """Especificação do contrato: passos de preço/quantidade e mínimos."""
        data = self._request("GET", "/api/v2/mix/market/contracts", params={
            "symbol": symbol, "productType": self.product_type}) or []
        if not data:
            raise ExchangeError(f"contrato não encontrado: {symbol}")
        c = data[0]
        return {
            "symbol": c.get("symbol", symbol),
            "price_place": int(_f(c.get("pricePlace"), 2)),
            "volume_place": int(_f(c.get("volumePlace"), 3)),
            "size_multiplier": _f(c.get("sizeMultiplier"), 0.001),
            "min_trade_num": _f(c.get("minTradeNum"), 0.0),
            "min_trade_usdt": _f(c.get("minTradeUSDT"), 5.0),
            "max_leverage": _f(c.get("maxLever"), 20.0),
        }

    # --------------------------------------------------------------- privado
    def saldo_usdt(self) -> float:
        data = self._request("GET", "/api/v2/mix/account/accounts", params={
            "productType": self.product_type}, assinado=True) or []
        for conta in data:
            if conta.get("marginCoin", "").upper() == self.margin_coin:
                return _f(conta.get("usdtEquity") or conta.get("available"))
        return 0.0

    def posicoes(self) -> list[Position]:
        data = self._request("GET", "/api/v2/mix/position/all-position", params={
            "productType": self.product_type, "marginCoin": self.margin_coin,
        }, assinado=True) or []
        out: list[Position] = []
        for p in data:
            size = _f(p.get("total"))
            if size <= 0:
                continue
            entry = _f(p.get("openPriceAvg"))
            out.append(Position(
                symbol=p.get("symbol", ""),
                side=Side.LONG if p.get("holdSide") == "long" else Side.SHORT,
                size=size,
                entry=entry,
                stop_loss=_f(p.get("presetStopLossPrice")),
                take_profits=[_f(p.get("presetStopSurplusPrice"))]
                if _f(p.get("presetStopSurplusPrice")) else [],
                opened_at=int(_f(p.get("cTime"))),
                leverage=_f(p.get("leverage"), 1.0),
                notional_usd=size * entry,
                modo="live",
            ))
        return out

    def definir_alavancagem(self, symbol: str, leverage: float,
                            hold_side: str | None = None) -> dict:
        body: dict[str, Any] = {
            "symbol": symbol, "productType": self.product_type,
            "marginCoin": self.margin_coin, "leverage": str(int(leverage)),
        }
        if hold_side:
            body["holdSide"] = hold_side
        return self._request("POST", "/api/v2/mix/account/set-leverage",
                             body=body, assinado=True) or {}

    def definir_margin_mode(self, symbol: str, modo: str = "isolated") -> dict:
        return self._request("POST", "/api/v2/mix/account/set-margin-mode", body={
            "symbol": symbol, "productType": self.product_type,
            "marginCoin": self.margin_coin, "marginMode": modo,
        }, assinado=True) or {}

    def abrir_posicao(self, symbol: str, side: Side, size: float,
                      leverage: float, stop_loss: float,
                      take_profit: float | None = None,
                      client_oid: str = "",
                      margin_mode: str = "isolated",
                      preco_limite: float | None = None) -> dict:
        """Abre posição com stop-loss anexado na própria ordem.

        O stop vai junto com a ordem (presetStopLossPrice) de propósito: se o
        robô cair logo após abrir, a proteção já está na exchange.
        """
        if size <= 0:
            raise ValueError("size deve ser > 0")
        if stop_loss <= 0:
            raise ValueError("stop_loss é obrigatório para abrir posição")
        body: dict[str, Any] = {
            "symbol": symbol,
            "productType": self.product_type,
            "marginMode": margin_mode,
            "marginCoin": self.margin_coin,
            "size": str(size),
            "side": "buy" if side is Side.LONG else "sell",
            "tradeSide": "open",
            "orderType": "limit" if preco_limite else "market",
            "presetStopLossPrice": str(stop_loss),
        }
        if preco_limite:
            body["price"] = str(preco_limite)
            body["force"] = "gtc"
        if take_profit:
            body["presetStopSurplusPrice"] = str(take_profit)
        if client_oid:
            body["clientOid"] = client_oid   # idempotência: evita ordem duplicada
        return self._request("POST", "/api/v2/mix/order/place-order",
                             body=body, assinado=True) or {}

    def ordem_por_client_oid(self, symbol: str,
                             client_oid: str) -> dict[str, Any] | None:
        """Procura uma ordem pelo `clientOid`. Devolve None se não existir.

        É a pergunta que o sistema precisa fazer ao subir depois de uma queda:
        a ordem que eu ia mandar chegou aqui? A Bitget responde 400 com um
        código de "ordem não existe" quando não encontra, e este método
        traduz isso em None em vez de propagar erro — porque "não existe" é
        uma resposta válida e útil, não uma falha.

        Um erro de rede, por outro lado, é propagado: não saber é diferente
        de saber que não existe, e confundir os dois é o que gera ordem
        duplicada.
        """
        try:
            data = self._request("GET", "/api/v2/mix/order/detail", params={
                "symbol": symbol, "productType": self.product_type,
                "clientOid": client_oid,
            }, assinado=True)
        except ExchangeUnreachable:
            # Rede fora não é "ordem não existe". Propaga para que quem
            # chamou trate como incerteza, nunca como ausência.
            raise
        except ExchangeError as exc:
            if _ordem_inexistente(exc):
                return None
            raise
        if not data:
            return None
        if isinstance(data, list):
            data = data[0] if data else None
        return dict(data) if isinstance(data, Mapping) else None

    def fills_por_client_oid(self, symbol: str,
                             client_oid: str) -> list[dict[str, Any]]:
        """Execuções associadas a um `clientOid`.

        A consulta de ordem pode responder que a ordem existe e foi
        cancelada; os fills dizem se ela moveu dinheiro. Para decidir se há
        posição aberta, o que importa são os fills.
        """
        data = self._request("GET", "/api/v2/mix/order/fills", params={
            "symbol": symbol, "productType": self.product_type,
        }, assinado=True) or {}
        lista = data.get("fillList", data) if isinstance(data, Mapping) else data
        if not isinstance(lista, list):
            return []
        return [dict(f) for f in lista
                if isinstance(f, Mapping)
                and f.get("clientOid") == client_oid]

    def fechar_posicao(self, symbol: str, side: Side,
                       size: float | None = None) -> dict:
        hold_side = "long" if side is Side.LONG else "short"
        if size is None:
            return self._request("POST", "/api/v2/mix/order/close-positions", body={
                "symbol": symbol, "productType": self.product_type,
                "holdSide": hold_side,
            }, assinado=True) or {}
        # Fechamento parcial: ordem reduce-only no sentido oposto.
        return self._request("POST", "/api/v2/mix/order/place-order", body={
            "symbol": symbol, "productType": self.product_type,
            "marginCoin": self.margin_coin, "size": str(size),
            "side": "sell" if side is Side.LONG else "buy",
            "tradeSide": "close", "orderType": "market",
            "reduceOnly": "YES",
        }, assinado=True) or {}

    def ajustar_stop(self, symbol: str, side: Side, novo_stop: float) -> dict:
        return self._request("POST", "/api/v2/mix/order/place-tpsl-order", body={
            "symbol": symbol, "productType": self.product_type,
            "marginCoin": self.margin_coin, "planType": "pos_loss",
            "triggerPrice": str(novo_stop),
            "holdSide": "long" if side is Side.LONG else "short",
        }, assinado=True) or {}

    def verificar_credenciais(self) -> dict[str, Any]:
        """Valida a chave e devolve diagnóstico — sem expor o segredo."""
        if self.cred is None:
            return {"ok": False, "erro": "nenhuma credencial configurada"}
        try:
            saldo = self.saldo_usdt()
        except InsufficientPermissions as exc:
            return {"ok": False, "erro": str(exc), "permissao_trade": False,
                    "api_key": self.cred.mascara()}
        except ExchangeError as exc:
            return {"ok": False, "erro": str(exc), "api_key": self.cred.mascara()}
        return {"ok": True, "api_key": self.cred.mascara(),
                "saldo_usdt": saldo, "product_type": self.product_type}
