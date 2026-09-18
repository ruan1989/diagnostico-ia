"""Motor autônomo de operação.

Ciclo de vida
-------------
O motor tem duas frequências, de propósito:

* GERENCIAMENTO (a cada `intervalo_gestao_s`, padrão 20s): confere stop,
  alvos e breakeven das posições abertas. Proteger posição aberta é urgente.
* VARREDURA (a cada `intervalo_scan_segundos`, padrão 5min): procura novas
  oportunidades. Procurar entrada não é urgente — e varrer demais só gera
  overtrading.

Travas para o modo real
-----------------------
Operar dinheiro real exige DUAS ações separadas e deliberadas:
  1. conectar a chave de API (permissão de trade, sem saque);
  2. armar o motor em modo `live` informando o texto de confirmação.

Isso evita o cenário em que alguém liga o sistema para "ver como é" e
descobre depois que ele estava enviando ordens de verdade.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..analysis.screener import ResultadoScan, Screener
from ..config import Settings
from ..models import Side, Signal, SignalGrade
from ..risk.manager import RiskManager
from ..store import Store
from .executor import Executor

log = logging.getLogger("investai.engine")

CONFIRMACAO_LIVE = "OPERAR COM DINHEIRO REAL"


@dataclass(slots=True)
class EstadoMotor:
    rodando: bool = False
    modo: str = "paper"
    armado_live: bool = False
    ultimo_scan_ms: int = 0
    ultima_gestao_ms: int = 0
    ciclos: int = 0
    ordens_enviadas: int = 0
    ordens_bloqueadas: int = 0
    ultimo_erro: str = ""
    iniciado_em_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rodando": self.rodando, "modo": self.modo,
            "armado_live": self.armado_live,
            "ultimo_scan_ms": self.ultimo_scan_ms,
            "ultima_gestao_ms": self.ultima_gestao_ms,
            "ciclos": self.ciclos,
            "ordens_enviadas": self.ordens_enviadas,
            "ordens_bloqueadas": self.ordens_bloqueadas,
            "ultimo_erro": self.ultimo_erro,
            "iniciado_em_ms": self.iniciado_em_ms,
        }


class TradingEngine:
    def __init__(self, settings: Settings, screener: Screener, executor: Executor,
                 risk: RiskManager, store: Store, *,
                 intervalo_gestao_s: float = 20.0):
        self.settings = settings
        self.screener = screener
        self.executor = executor
        self.risk = risk
        self.store = store
        self.intervalo_gestao_s = intervalo_gestao_s
        self.estado = EstadoMotor(modo=executor.modo)
        self._thread: threading.Thread | None = None
        self._parar = threading.Event()
        self._lock = threading.RLock()
        self._ultimo_scan: ResultadoScan | None = None
        self.on_sinal: Callable[[Signal], None] | None = None

        self.executor.carregar_posicoes_papel(
            [p for p in store.posicoes() if p.modo == "paper"])

    # ------------------------------------------------------------------ armar
    def armar_live(self, confirmacao: str) -> tuple[bool, str]:
        """Habilita envio de ordens reais. Exige frase exata."""
        if confirmacao.strip().upper() != CONFIRMACAO_LIVE:
            return False, (f"confirmação incorreta — envie exatamente "
                           f"'{CONFIRMACAO_LIVE}' para habilitar ordens reais")
        if self.executor.backend is None:
            return False, "nenhuma chave de API conectada"
        try:
            saldo = self.executor.backend.saldo_usdt()
        except Exception as exc:                        # noqa: BLE001
            return False, f"chave de API não conseguiu ler o saldo: {exc}"
        if saldo <= 0:
            return False, "saldo em USDT igual a zero na conta de futuros"

        self.executor.modo = "live"
        self.estado.modo = "live"
        self.estado.armado_live = True
        self.risk.sincronizar_capital(saldo)
        self.store.registrar_evento(
            "ALERTA", "engine", "modo real ARMADO",
            {"saldo_usdt": saldo, "risco_por_trade_pct":
             self.settings.risk.risco_por_trade_pct})
        return True, (f"modo real armado; saldo US$ {saldo:.2f}; risco por "
                      f"operação {self.settings.risk.risco_por_trade_pct}% "
                      f"(US$ {saldo * self.settings.risk.risco_por_trade_pct / 100:.2f})")

    def desarmar_live(self) -> str:
        self.executor.modo = "paper"
        self.estado.modo = "paper"
        self.estado.armado_live = False
        self.store.registrar_evento("INFO", "engine", "modo real desarmado")
        return "modo real desarmado; voltou para simulação"

    # ------------------------------------------------------------------ ciclo
    def iniciar(self) -> str:
        with self._lock:
            if self.estado.rodando:
                return "motor já está rodando"
            self._parar.clear()
            self.estado.rodando = True
            self.estado.iniciado_em_ms = int(time.time() * 1000)
            self._thread = threading.Thread(target=self._loop, name="investai-engine",
                                            daemon=True)
            self._thread.start()
        self.store.registrar_evento("INFO", "engine", "motor iniciado",
                                    {"modo": self.executor.modo})
        return f"motor iniciado em modo {self.executor.modo}"

    def parar(self, timeout: float = 10.0) -> str:
        with self._lock:
            if not self.estado.rodando:
                return "motor já está parado"
            self._parar.set()
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)
        self.estado.rodando = False
        self.store.registrar_evento("INFO", "engine", "motor parado")
        return "motor parado"

    def _loop(self) -> None:
        while not self._parar.is_set():
            try:
                self.ciclo()
            except Exception as exc:                    # noqa: BLE001
                # Um ciclo com erro não pode derrubar o motor: as posições
                # abertas precisam continuar sendo gerenciadas.
                self.estado.ultimo_erro = f"{type(exc).__name__}: {exc}"
                log.exception("erro no ciclo do motor")
                self.store.registrar_evento("ERRO", "engine",
                                            "erro no ciclo", {"erro": str(exc)})
            self._parar.wait(self.intervalo_gestao_s)
        self.estado.rodando = False

    def ciclo(self) -> dict[str, Any]:
        """Um ciclo: gerencia posições e, se estiver na hora, varre o mercado."""
        agora = int(time.time() * 1000)
        self.estado.ciclos += 1
        resultado: dict[str, Any] = {"gestao": [], "entradas": [], "scan": False}

        # ------------------------------------------------------- 1) gestão
        posicoes = self.executor.posicoes()
        if posicoes:
            precos: dict[str, float] = {}
            for p in posicoes:
                snap = self.screener.hub.ticker(p.symbol)
                if snap and snap.last_price > 0:
                    precos[p.symbol] = snap.last_price
            for res, trade in self.executor.gerenciar(precos, agora_ms=agora):
                resultado["gestao"].append(res.mensagem)
                if trade is not None:
                    self.store.salvar_trade(trade, modo=self.executor.modo)
                    self.risk.registrar_trade(trade, agora_ms=agora)
                    self.store.registrar_evento(
                        "INFO", "executor", res.mensagem,
                        {"trade": trade.to_dict(),
                         "estado_risco": self.risk.estado.to_dict()})
            for p in self.executor.posicoes():
                if p.modo == "paper":
                    self.store.salvar_posicao(p)
            abertos = {p.symbol for p in self.executor.posicoes()}
            for p in posicoes:
                if p.symbol not in abertos:
                    self.store.remover_posicao(p.symbol)
        self.estado.ultima_gestao_ms = agora

        # ------------------------------------------------------- 2) varredura
        intervalo = self.settings.exec.intervalo_scan_segundos * 1000
        if agora - self.estado.ultimo_scan_ms < intervalo:
            return resultado

        self.estado.ultimo_scan_ms = agora
        resultado["scan"] = True
        scan = self.screener.scan()
        self._ultimo_scan = scan

        for sinal in scan.operaveis:
            self.store.salvar_sinal(sinal)
            if self.on_sinal:
                try:
                    self.on_sinal(sinal)
                except Exception as exc:                # noqa: BLE001
                    log.warning("notificação de sinal falhou: %s", exc)

        # Sincroniza capital com a exchange antes de dimensionar posição nova.
        if self.executor.is_live:
            self.risk.sincronizar_capital(
                self.executor.saldo(self.risk.estado.capital_atual))

        for sinal in scan.operaveis:
            entrada = self._tentar_entrada(sinal, agora)
            if entrada:
                resultado["entradas"].append(entrada)
        return resultado

    def _tentar_entrada(self, sinal: Signal, agora_ms: int) -> dict[str, Any] | None:
        if sinal.grade not in (SignalGrade.A, SignalGrade.B):
            return None
        posicoes = self.executor.posicoes()
        decisao = self.risk.avaliar_entrada(sinal, posicoes, agora_ms=agora_ms)
        if not decisao.aprovado:
            self.estado.ordens_bloqueadas += 1
            self.store.registrar_evento(
                "INFO", "risk", f"entrada em {sinal.symbol} bloqueada: {decisao.motivo}",
                {"sinal": sinal.to_dict(), "decisao": decisao.to_dict()})
            return {"symbol": sinal.symbol, "ok": False, "motivo": decisao.motivo}

        res = self.executor.abrir(sinal, decisao, agora_ms=agora_ms)
        if res.ok and res.posicao:
            self.estado.ordens_enviadas += 1
            if res.posicao.modo == "paper":
                self.store.salvar_posicao(res.posicao)
            self.store.registrar_evento(
                "ALERTA" if self.executor.is_live else "INFO", "executor",
                res.mensagem, {"sinal": sinal.to_dict(),
                               "decisao": decisao.to_dict(),
                               "execucao": res.to_dict()})
        else:
            self.store.registrar_evento("ERRO", "executor", res.mensagem,
                                        {"sinal": sinal.to_dict()})
        return {"symbol": sinal.symbol, "ok": res.ok, "motivo": res.mensagem}

    # ------------------------------------------------------------------ status
    @property
    def ultimo_scan(self) -> ResultadoScan | None:
        return self._ultimo_scan

    def status(self) -> dict[str, Any]:
        posicoes = self.executor.posicoes()
        return {
            "motor": self.estado.to_dict(),
            "risco": self.risk.estado.to_dict(),
            "limites": self.settings.risk.to_dict(),
            "posicoes_abertas": len(posicoes),
            "posicoes": [
                {"symbol": p.symbol, "side": p.side.value, "size": p.size,
                 "entry": p.entry, "stop_loss": p.stop_loss,
                 "take_profits": p.take_profits,
                 "notional_usd": round(p.notional_usd, 2),
                 "risk_usd": round(p.risk_usd, 2),
                 "tps_atingidos": p.tps_atingidos,
                 "trailing_ativo": p.trailing_ativo,
                 "modo": p.modo, "opened_at": p.opened_at}
                for p in posicoes
            ],
            "desempenho_realizado": self.store.resumo_trades(
                modo=self.executor.modo),
            "confirmacao_necessaria_live": CONFIRMACAO_LIVE,
        }

    def fechar_tudo(self, motivo: str = "comando manual") -> list[str]:
        """Botão de pânico: encerra todas as posições a mercado."""
        agora = int(time.time() * 1000)
        mensagens: list[str] = []
        for p in list(self.executor.posicoes()):
            snap = self.screener.hub.ticker(p.symbol)
            preco = snap.last_price if snap else p.entry
            res, trade = self.executor.fechar(p.symbol, preco, motivo, agora_ms=agora)
            mensagens.append(res.mensagem)
            if trade is not None:
                self.store.salvar_trade(trade, modo=self.executor.modo)
                self.risk.registrar_trade(trade, agora_ms=agora)
            self.store.remover_posicao(p.symbol)
        self.store.registrar_evento("ALERTA", "engine",
                                    f"fechamento total: {motivo}",
                                    {"resultados": mensagens})
        return mensagens
