"""Shadow mode operante: decide em tempo real, não envia ordem, e depois
confere o que o mercado fez com cada decisão.

Por que isto existe
-------------------
O backtest responde "esta estratégia teria dado lucro no passado?". É a
pergunta errada quando o passado é o mesmo período em que a estratégia foi
ajustada. O shadow mode responde a pergunta cara: **as decisões que este
sistema toma HOJE, sobre dados que ele nunca viu, dão lucro?**

A diferença é o carimbo de tempo. Uma decisão registrada antes de o preço
andar não pode ser reescrita depois. Semanas dessas decisões formam uma
amostra que nenhum ajuste de parâmetro consegue contaminar.

Nada aqui envia ordem. Nenhuma função deste módulo fala com a exchange para
escrever — só lê candles para descobrir o que aconteceu.

Como a liquidação decide o resultado
------------------------------------
Caminha vela a vela a partir da decisão, na ordem. Numa vela que toca stop e
alvo, assume **stop** — a mesma convenção pessimista do backtest. Sem dado
intrabar não há como saber qual veio primeiro, e errar para o lado otimista
aqui produziria exatamente o número bonito que este modo existe para evitar.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..models import Candle, Side

log = logging.getLogger("investai.shadow")

# Uma decisão que não resolveu em 14 dias é encerrada a mercado. Sem isso a
# amostra fica cheia de posições eternas que nunca viram resultado, e a
# expectativa medida seria calculada só sobre as que fecharam rápido — um
# viés de sobrevivência ao contrário.
MAX_BARRAS_ABERTA = 24 * 14


@dataclass(slots=True)
class ResultadoLiquidacao:
    estado: str                  # alvo | stop | expirada | pendente
    preco_saida: float | None = None
    motivo: str = ""
    resultado_r: float | None = None
    barras: int = 0
    fechado_em_ms: int | None = None


def liquidar(decisao: dict[str, Any], velas: Sequence[Candle], *,
             max_barras: int = MAX_BARRAS_ABERTA) -> ResultadoLiquidacao:
    """Descobre o que o mercado fez com uma decisão, vela a vela.

    `velas` são as velas ESTRITAMENTE posteriores ao instante da decisão. A
    vela em que a decisão foi tomada não entra: usá-la olharia para dentro da
    barra que originou o sinal.
    """
    entry = float(decisao["entry"])
    stop = float(decisao["stop_loss"])
    alvo = float(decisao["alvo"])
    long = decisao["side"] == Side.LONG.value
    risco = abs(entry - stop)
    if risco <= 0:
        return ResultadoLiquidacao("expirada", motivo="risco zero na decisão")

    posteriores = [v for v in velas if v.ts > int(decisao["decidido_em"])]
    for i, v in enumerate(posteriores[:max_barras], start=1):
        bateu_stop = v.low <= stop if long else v.high >= stop
        bateu_alvo = v.high >= alvo if long else v.low <= alvo
        if bateu_stop:
            # Empate na mesma vela resolve para o stop, por convenção.
            return ResultadoLiquidacao(
                "stop", stop, "stop atingido", -1.0, i, v.ts)
        if bateu_alvo:
            r = abs(alvo - entry) / risco
            return ResultadoLiquidacao(
                "alvo", alvo, "alvo atingido", round(r, 4), i, v.ts)

    if len(posteriores) >= max_barras:
        ultima = posteriores[max_barras - 1]
        bruto = (ultima.close - entry) if long else (entry - ultima.close)
        return ResultadoLiquidacao(
            "expirada", ultima.close,
            f"sem resolver em {max_barras} velas; encerrada a mercado",
            round(bruto / risco, 4), max_barras, ultima.ts)

    return ResultadoLiquidacao("pendente", barras=len(posteriores))


@dataclass(slots=True)
class ResumoShadow:
    """O que a amostra acumulada permite afirmar — e o que ainda não."""

    total: int = 0
    pendentes: int = 0
    liquidadas: int = 0
    ganhos: int = 0
    perdas: int = 0
    expiradas: int = 0
    expectativa_r: float | None = None
    soma_r: float = 0.0
    win_rate: float | None = None
    dias_corridos: float = 0.0
    primeira_ms: int | None = None
    ultima_ms: int | None = None
    por_par: dict[str, int] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)
    conclusivo: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total, "pendentes": self.pendentes,
            "liquidadas": self.liquidadas, "ganhos": self.ganhos,
            "perdas": self.perdas, "expiradas": self.expiradas,
            "expectativa_r": self.expectativa_r, "soma_r": round(self.soma_r, 4),
            "win_rate": self.win_rate, "dias_corridos": round(self.dias_corridos, 2),
            "primeira_ms": self.primeira_ms, "ultima_ms": self.ultima_ms,
            "por_par": self.por_par, "avisos": self.avisos,
            "conclusivo": self.conclusivo,
            "observacao": (
                "Cada decisão foi registrada ANTES de o preço andar. Nenhum "
                "ajuste posterior de parâmetro pode melhorar estes números — "
                "é essa a diferença entre shadow mode e backtest."),
        }


# Mínimos para o resumo deixar de ser anedota. São os mesmos do gate de
# paper_trading: exigir menos aqui seria medir com régua mais frouxa
# justamente onde a medida importa.
MIN_DECISOES = 40
MIN_DIAS = 21


class ShadowLive:
    """Liga o ciclo de análise ao registro persistente de decisões."""

    def __init__(self, store: Any, hub: Any, *, timeframe: str = "1H",
                 max_barras: int = MAX_BARRAS_ABERTA):
        self.store = store
        self.hub = hub
        self.timeframe = timeframe
        self.max_barras = max_barras

    # ------------------------------------------------------- registro
    def registrar_ciclo(self, analises: Sequence[Any], *,
                        agora_ms: int | None = None) -> list[str]:
        """Registra as decisões que o sistema APROVOU neste ciclo.

        Só o que passou no consenso e no Risk Engine entra: são exatamente as
        que teriam virado ordem. Registrar as rejeitadas mediria outra coisa.
        """
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        novos: list[str] = []
        for a in analises:
            if not getattr(a, "operavel", False):
                continue
            risco = getattr(a, "risco", None)
            consenso = getattr(a, "consenso", None)
            # O plano vem do sinal do lado escolhido pelo consenso. Sem stop e
            # alvo a decisão não é conferível depois, e registrá-la produziria
            # uma linha que nunca liquida.
            sinal = getattr(a, "sinal", None)
            if sinal is None:
                log.debug("shadow: %s aprovado sem plano de trade", a.symbol)
                continue
            entry = getattr(sinal, "entry", None) or a.preco
            stop = getattr(sinal, "stop_loss", None)
            alvos = list(getattr(sinal, "take_profits", None) or [])
            if not stop or not alvos:
                log.debug("shadow: %s sem stop ou alvo", a.symbol)
                continue
            bruta = getattr(consenso, "direcao", "compra") if consenso else "compra"
            side = Side.SHORT.value if bruta in ("venda", "short") else Side.LONG.value
            d = {
                "id": f"sh-{a.symbol}-{side}-{agora}",
                "decidido_em": agora, "symbol": a.symbol, "side": side,
                "entry": float(entry), "stop_loss": float(stop),
                "alvo": float(alvos[0]), "size": float(getattr(risco, "size", 0.0)),
                "score": float(getattr(consenso, "score", 0.0)),
                "estrategia": getattr(a, "estrategia", "confluencia_tendencia@v1"),
            }
            if self.store.salvar_decisao_shadow(d):
                novos.append(d["id"])
        return novos

    # ---------------------------------------------------- liquidação
    def liquidar_pendentes(self, *, agora_ms: int | None = None,
                           limite: int = 200) -> int:
        """Confere o que o mercado fez com cada decisão ainda em aberto."""
        pendentes = self.store.decisoes_shadow(estado="pendente", limite=limite)
        fechadas = 0
        por_par: dict[str, list[Candle]] = {}
        for d in pendentes:
            sym = d["symbol"]
            if sym not in por_par:
                try:
                    por_par[sym] = self.hub.candles(sym, self.timeframe, limit=1000)
                except Exception as exc:                # noqa: BLE001
                    log.warning("shadow: sem candles de %s: %s", sym, exc)
                    por_par[sym] = []
            r = liquidar(d, por_par[sym], max_barras=self.max_barras)
            if r.estado == "pendente":
                continue
            self.store.liquidar_decisao_shadow(
                d["id"], estado=r.estado, fechado_em=r.fechado_em_ms or 0,
                preco_saida=r.preco_saida or 0.0, motivo_saida=r.motivo,
                resultado_r=r.resultado_r if r.resultado_r is not None else 0.0,
                barras=r.barras)
            fechadas += 1
        return fechadas

    # ------------------------------------------------------- resumo
    def resumo(self, *, agora_ms: int | None = None) -> ResumoShadow:
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        bruto = self.store.resumo_shadow()
        por_estado = bruto["por_estado"]
        decisoes = self.store.decisoes_shadow(limite=1000)

        res = ResumoShadow(
            total=sum(por_estado.values()),
            pendentes=por_estado.get("pendente", 0),
            ganhos=por_estado.get("alvo", 0),
            perdas=por_estado.get("stop", 0),
            expiradas=por_estado.get("expirada", 0),
            primeira_ms=bruto["primeira_ms"], ultima_ms=bruto["ultima_ms"])
        res.liquidadas = res.ganhos + res.perdas + res.expiradas

        for d in decisoes:
            res.por_par[d["symbol"]] = res.por_par.get(d["symbol"], 0) + 1

        erres = bruto["resultados_r"]
        if erres:
            res.soma_r = sum(erres)
            res.expectativa_r = round(res.soma_r / len(erres), 4)
        if res.liquidadas:
            res.win_rate = round(res.ganhos / res.liquidadas, 4)
        if res.primeira_ms:
            res.dias_corridos = (agora - res.primeira_ms) / 86_400_000

        # O resumo diz o que ainda falta para ele valer alguma coisa.
        if res.liquidadas < MIN_DECISOES:
            res.avisos.append(
                f"{res.liquidadas} decisões liquidadas, abaixo do mínimo de "
                f"{MIN_DECISOES}: a expectativa acima ainda é anedota, não "
                f"evidência")
        if res.dias_corridos < MIN_DIAS:
            res.avisos.append(
                f"{res.dias_corridos:.1f} dias corridos, abaixo do mínimo de "
                f"{MIN_DIAS}: uma amostra concentrada em poucos dias mede um "
                f"regime de mercado, não a estratégia")
        if res.expectativa_r is not None and res.expectativa_r <= 0 and not res.avisos:
            res.avisos.append(
                "expectativa não positiva com amostra suficiente: as decisões "
                "deste sistema, ao vivo, NÃO deram dinheiro")
        res.conclusivo = (res.liquidadas >= MIN_DECISOES
                          and res.dias_corridos >= MIN_DIAS)
        return res
