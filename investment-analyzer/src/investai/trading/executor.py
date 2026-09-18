"""Execução de ordens em dois modos: papel e real.

O modo papel não é um brinquedo: ele aplica as mesmas taxas, o mesmo slippage
e as mesmas regras de saída do modo real. É a única forma honesta de saber se
a estratégia funciona antes de arriscar dinheiro.

Proteções embutidas no modo real:

* toda ordem leva um `clientOid` determinístico — se a rede cair entre o envio
  e a resposta, o reenvio não cria posição dobrada;
* o stop-loss vai anexado à ordem de abertura, não em uma segunda chamada;
* alavancagem e modo de margem são configurados antes de abrir;
* o executor NUNCA aciona saque ou transferência — a chave de API deve ser
  criada sem essa permissão.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import ExecutionConfig
from ..models import Position, Side, Signal, Trade
from ..risk.manager import DecisaoRisco

log = logging.getLogger("investai.executor")


class _TradingBackend(Protocol):
    def saldo_usdt(self) -> float: ...
    def posicoes(self) -> list[Position]: ...
    def definir_alavancagem(self, symbol: str, leverage: float,
                            hold_side: str | None = None) -> dict: ...
    def definir_margin_mode(self, symbol: str, modo: str) -> dict: ...
    def abrir_posicao(self, symbol: str, side: Side, size: float, leverage: float,
                      stop_loss: float, take_profit: float | None,
                      client_oid: str, margin_mode: str,
                      preco_limite: float | None) -> dict: ...
    def fechar_posicao(self, symbol: str, side: Side, size: float | None) -> dict: ...
    def ajustar_stop(self, symbol: str, side: Side, novo_stop: float) -> dict: ...
    def contrato(self, symbol: str) -> dict: ...


@dataclass(slots=True)
class ResultadoExecucao:
    ok: bool
    mensagem: str
    posicao: Position | None = None
    ordem: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "mensagem": self.mensagem,
            "posicao": {
                "symbol": self.posicao.symbol, "side": self.posicao.side.value,
                "size": self.posicao.size, "entry": self.posicao.entry,
                "stop_loss": self.posicao.stop_loss,
                "take_profits": self.posicao.take_profits,
                "notional_usd": round(self.posicao.notional_usd, 2),
                "risk_usd": round(self.posicao.risk_usd, 2),
                "leverage": round(self.posicao.leverage, 2),
                "modo": self.posicao.modo,
                "client_oid": self.posicao.client_oid,
            } if self.posicao else None,
            "ordem": self.ordem,
        }


def gerar_client_oid(symbol: str, side: Side, entry: float, ts_ms: int,
                     janela_ms: int = 60_000) -> str:
    """ID determinístico por (símbolo, lado, preço, janela de tempo).

    Duas tentativas do mesmo sinal dentro da mesma janela geram o MESMO id, e
    a exchange rejeita a segunda — que é exatamente o comportamento desejado.
    """
    bucket = ts_ms // janela_ms
    bruto = f"{symbol}|{side.value}|{entry:.8f}|{bucket}"
    return "iai" + hashlib.sha256(bruto.encode()).hexdigest()[:26]


def arredondar_size(size: float, volume_place: int, min_trade: float) -> float:
    """Ajusta a quantidade ao passo do contrato, sempre para BAIXO.

    Arredondar para cima aumentaria o risco acima do aprovado pela gestão de
    risco — o que anularia o dimensionamento.
    """
    if volume_place < 0:
        volume_place = 0
    fator = 10 ** volume_place
    ajustado = int(size * fator) / fator
    if ajustado < min_trade:
        return 0.0
    return ajustado


def arredondar_preco(preco: float, price_place: int) -> float:
    return round(preco, max(0, price_place))


class Executor:
    """Executa entradas/saídas e gerencia posições abertas."""

    def __init__(self, cfg: ExecutionConfig, backend: _TradingBackend | None = None,
                 modo: str | None = None):
        self.cfg = cfg
        self.backend = backend
        self.modo = (modo or cfg.modo).lower()
        if self.modo not in {"paper", "live"}:
            raise ValueError(f"modo inválido: {self.modo}")
        if self.modo == "live" and backend is None:
            raise ValueError("modo live exige backend de exchange conectado")
        self._posicoes_papel: dict[str, Position] = {}

    @property
    def is_live(self) -> bool:
        return self.modo == "live"

    # ------------------------------------------------------------------ estado
    def posicoes(self) -> list[Position]:
        if self.is_live and self.backend:
            return self.backend.posicoes()
        return list(self._posicoes_papel.values())

    def carregar_posicoes_papel(self, posicoes: list[Position]) -> None:
        self._posicoes_papel = {p.symbol: p for p in posicoes}

    def saldo(self, fallback: float) -> float:
        if self.is_live and self.backend:
            try:
                return self.backend.saldo_usdt()
            except Exception as exc:                    # noqa: BLE001
                log.error("falha ao ler saldo real: %s", exc)
                return fallback
        return fallback

    # ------------------------------------------------------------------ abrir
    def abrir(self, sinal: Signal, decisao: DecisaoRisco,
              *, agora_ms: int | None = None) -> ResultadoExecucao:
        if not decisao.aprovado:
            return ResultadoExecucao(False, f"risco não aprovou: {decisao.motivo}")
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)

        size = decisao.size
        stop = sinal.stop_loss
        alvo1 = sinal.take_profits[0] if sinal.take_profits else None

        # Ajusta ao passo do contrato quando a informação está disponível.
        if self.backend is not None:
            try:
                spec = self.backend.contrato(sinal.symbol)
                size = arredondar_size(size, spec["volume_place"], spec["min_trade_num"])
                stop = arredondar_preco(stop, spec["price_place"])
                if alvo1:
                    alvo1 = arredondar_preco(alvo1, spec["price_place"])
                if size <= 0:
                    return ResultadoExecucao(
                        False, f"tamanho {decisao.size:.8f} abaixo do mínimo do "
                               f"contrato ({spec['min_trade_num']})")
                if size * sinal.entry < spec["min_trade_usdt"]:
                    return ResultadoExecucao(
                        False, f"notional US$ {size * sinal.entry:.2f} abaixo do "
                               f"mínimo US$ {spec['min_trade_usdt']:.2f}")
            except Exception as exc:                    # noqa: BLE001
                log.warning("spec do contrato %s indisponível: %s", sinal.symbol, exc)

        client_oid = gerar_client_oid(sinal.symbol, sinal.side, sinal.entry, agora)
        posicao = Position(
            symbol=sinal.symbol, side=sinal.side, size=size, entry=sinal.entry,
            stop_loss=stop, take_profits=list(sinal.take_profits), opened_at=agora,
            leverage=decisao.alavancagem, notional_usd=size * sinal.entry,
            risk_usd=decisao.risco_usd, client_oid=client_oid, modo=self.modo,
        )

        if not self.is_live:
            self._posicoes_papel[sinal.symbol] = posicao
            return ResultadoExecucao(
                True, f"posição simulada aberta em {sinal.symbol} "
                      f"({sinal.side.value}, {size:.6f})", posicao=posicao)

        assert self.backend is not None
        try:
            # Margem isolada limita a perda ao valor alocado naquela posição.
            self.backend.definir_margin_mode(sinal.symbol, self.cfg.margin_mode)
        except Exception as exc:                        # noqa: BLE001
            log.warning("não foi possível definir margin mode em %s: %s",
                        sinal.symbol, exc)
        try:
            self.backend.definir_alavancagem(
                sinal.symbol, max(1.0, round(decisao.alavancagem)),
                "long" if sinal.side is Side.LONG else "short")
        except Exception as exc:                        # noqa: BLE001
            log.warning("não foi possível definir alavancagem em %s: %s",
                        sinal.symbol, exc)

        try:
            ordem = self.backend.abrir_posicao(
                sinal.symbol, sinal.side, size, decisao.alavancagem, stop,
                alvo1, client_oid, self.cfg.margin_mode, None)
        except Exception as exc:                        # noqa: BLE001
            log.error("ordem em %s rejeitada: %s", sinal.symbol, exc)
            return ResultadoExecucao(False, f"ordem rejeitada: {exc}")

        return ResultadoExecucao(
            True, f"ordem enviada para {sinal.symbol} ({sinal.side.value})",
            posicao=posicao, ordem=ordem if isinstance(ordem, dict) else {})

    # ------------------------------------------------------------------ fechar
    def fechar(self, symbol: str, preco_atual: float, motivo: str,
               *, fracao: float = 1.0,
               agora_ms: int | None = None) -> tuple[ResultadoExecucao, Trade | None]:
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        pos = next((p for p in self.posicoes() if p.symbol == symbol), None)
        if pos is None:
            return ResultadoExecucao(False, f"nenhuma posição aberta em {symbol}"), None
        fracao = max(0.0, min(1.0, fracao))
        if fracao <= 0:
            return ResultadoExecucao(False, "fração de fechamento inválida"), None

        parte = pos.size * fracao
        saida = self._preco_saida(preco_atual, pos.side)
        delta = (saida - pos.entry) if pos.side is Side.LONG else (pos.entry - saida)
        bruto = delta * parte
        taxas = parte * (pos.entry + saida) * self.cfg.taxa_taker_pct / 100.0
        pnl = bruto - taxas
        risco = pos.risk_usd if pos.risk_usd > 0 else abs(pos.entry - pos.stop_loss) * pos.size

        if self.is_live and self.backend:
            try:
                self.backend.fechar_posicao(pos.symbol, pos.side,
                                            None if fracao >= 1.0 else parte)
            except Exception as exc:                    # noqa: BLE001
                log.error("falha ao fechar %s: %s", symbol, exc)
                return ResultadoExecucao(False, f"falha ao fechar: {exc}"), None

        trade = Trade(
            symbol=pos.symbol, side=pos.side, entry=pos.entry, exit=saida,
            size=parte, opened_at=pos.opened_at, closed_at=agora, pnl_usd=pnl,
            pnl_r=(pnl / risco * (1.0 / fracao if fracao else 1.0)) if risco else 0.0,
            motivo_saida=motivo, fees_usd=taxas,
            bars_held=max(0, int((agora - pos.opened_at) / 3_600_000)),
        )

        if fracao >= 1.0:
            self._posicoes_papel.pop(symbol, None)
        else:
            pos.size -= parte
            pos.notional_usd = pos.size * pos.entry
            pos.tps_atingidos += 1
            if not self.is_live:
                self._posicoes_papel[symbol] = pos

        return ResultadoExecucao(
            True, f"{'fechamento' if fracao >= 1.0 else 'parcial'} de {symbol} "
                  f"por {motivo}: PnL US$ {pnl:+.2f}", posicao=pos), trade

    def _preco_saida(self, preco: float, side: Side) -> float:
        """Slippage sempre desfavorável na saída."""
        f = self.cfg.slippage_pct / 100.0
        return preco * (1.0 - f) if side is Side.LONG else preco * (1.0 + f)

    # --------------------------------------------------------------- gerenciar
    def gerenciar(self, precos: dict[str, float],
                  *, agora_ms: int | None = None) -> list[tuple[ResultadoExecucao, Trade | None]]:
        """Aplica stop, alvos parciais e breakeven às posições abertas."""
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        eventos: list[tuple[ResultadoExecucao, Trade | None]] = []

        for pos in list(self.posicoes()):
            preco = precos.get(pos.symbol)
            if preco is None or preco <= 0:
                continue
            long = pos.side is Side.LONG

            # 1) Stop primeiro: proteção tem precedência sobre lucro.
            if (long and preco <= pos.stop_loss) or (not long and preco >= pos.stop_loss):
                motivo = "breakeven" if pos.trailing_ativo and (
                    (long and pos.stop_loss >= pos.entry)
                    or (not long and pos.stop_loss <= pos.entry)
                ) else "stop_loss"
                eventos.append(self.fechar(pos.symbol, pos.stop_loss, motivo,
                                           agora_ms=agora))
                continue

            # 2) Alvos parciais em ordem.
            idx = pos.tps_atingidos
            if idx < len(pos.take_profits):
                alvo = pos.take_profits[idx]
                atingiu = preco >= alvo if long else preco <= alvo
                if atingiu:
                    fracoes = list(self.cfg.parciais)
                    fracao = fracoes[idx] if idx < len(fracoes) else 1.0
                    # Último alvo fecha o que sobrou.
                    if idx == len(pos.take_profits) - 1:
                        fracao = 1.0
                    res, trade = self.fechar(pos.symbol, alvo, f"alvo_{idx + 1}",
                                             fracao=fracao, agora_ms=agora)
                    eventos.append((res, trade))
                    # 3) Depois do primeiro alvo, o trade não pode mais dar prejuízo.
                    if res.ok and fracao < 1.0 and self.cfg.trailing_apos_tp1:
                        atual = next((p for p in self.posicoes()
                                      if p.symbol == pos.symbol), None)
                        if atual and not atual.trailing_ativo:
                            atual.stop_loss = atual.entry
                            atual.trailing_ativo = True
                            if not self.is_live:
                                self._posicoes_papel[atual.symbol] = atual
                            elif self.backend:
                                try:
                                    self.backend.ajustar_stop(
                                        atual.symbol, atual.side, atual.entry)
                                except Exception as exc:    # noqa: BLE001
                                    log.warning("falha ao mover stop de %s para "
                                                "breakeven: %s", atual.symbol, exc)
        return eventos
