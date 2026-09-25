"""Shadow mode: decidir em tempo real sem enviar ordem.

O que o shadow mode revela e o paper trading não
-----------------------------------------------
O paper trading responde "a estratégia dá lucro simulado?". O shadow mode
responde uma pergunta anterior e mais básica: **o sistema executa o que
decide?**

São coisas diferentes. Um sistema pode ter estratégia boa e ainda assim, ao
vivo, decidir comprar a 100 e a execução acontecer a 100,8 porque a latência
somada à volatilidade moveu o preço. Ou decidir entrar e o preço já não estar
disponível. Ou registrar o sinal no candle errado. Nenhum backtest encontra
esses erros — eles só aparecem quando a decisão é tomada em tempo real e
comparada com o que aconteceu de fato.

A **fidelidade** medida aqui é o que o gate de promoção exige antes de
permitir execução assistida.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..models import Side


class ResultadoShadow(str, Enum):
    FIEL = "fiel"                       # execução reproduziu a decisão
    DESVIO_TOLERAVEL = "desvio_toleravel"
    DESVIO_MATERIAL = "desvio_material"
    NAO_EXECUTAVEL = "nao_executavel"   # a decisão não era executável
    PENDENTE = "pendente"


@dataclass(slots=True)
class DecisaoShadow:
    """Uma decisão registrada no momento em que foi tomada."""

    id: str
    symbol: str
    side: Side
    decidido_em_ms: int
    preco_decisao: float
    stop_decisao: float
    alvo_decisao: float
    size_decisao: float
    score: float = 0.0
    estrategia: str = ""
    # Preenchido depois, pela execução simulada.
    preco_execucao: float | None = None
    executado_em_ms: int | None = None
    size_executada: float | None = None
    # Preenchido depois, pelo que o mercado fez.
    preco_max_observado: float | None = None
    preco_min_observado: float | None = None
    resultado_r: float | None = None
    resultado: ResultadoShadow = ResultadoShadow.PENDENTE
    observacoes: list[str] = field(default_factory=list)

    @property
    def slippage_pct(self) -> float | None:
        if self.preco_execucao is None or self.preco_decisao == 0:
            return None
        d = (self.preco_execucao - self.preco_decisao) / self.preco_decisao
        # Slippage sempre expresso como custo: positivo = pior para o operador.
        return (d if self.side is Side.LONG else -d) * 100.0

    @property
    def latencia_ms(self) -> int | None:
        if self.executado_em_ms is None:
            return None
        return self.executado_em_ms - self.decidido_em_ms

    @property
    def fracao_preenchida(self) -> float | None:
        if self.size_executada is None or self.size_decisao == 0:
            return None
        return self.size_executada / self.size_decisao

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "symbol": self.symbol, "side": self.side.value,
            "decidido_em_ms": self.decidido_em_ms,
            "preco_decisao": self.preco_decisao,
            "stop_decisao": self.stop_decisao,
            "alvo_decisao": self.alvo_decisao,
            "size_decisao": self.size_decisao, "score": round(self.score, 2),
            "estrategia": self.estrategia,
            "preco_execucao": self.preco_execucao,
            "executado_em_ms": self.executado_em_ms,
            "size_executada": self.size_executada,
            "slippage_pct": (round(s, 4)
                             if (s := self.slippage_pct) is not None else None),
            "latencia_ms": self.latencia_ms,
            "fracao_preenchida": (round(f, 4)
                                  if (f := self.fracao_preenchida) is not None
                                  else None),
            "resultado_r": (round(self.resultado_r, 4)
                            if self.resultado_r is not None else None),
            "resultado": self.resultado.value,
            "observacoes": self.observacoes,
        }


@dataclass(slots=True)
class LimiaresShadow:
    # Slippage tolerado antes de considerar desvio material.
    slippage_toleravel_pct: float = 0.10
    slippage_material_pct: float = 0.30
    latencia_toleravel_ms: int = 2_000
    fracao_minima_preenchida: float = 0.90

    def to_dict(self) -> dict[str, Any]:
        return {
            "slippage_toleravel_pct": self.slippage_toleravel_pct,
            "slippage_material_pct": self.slippage_material_pct,
            "latencia_toleravel_ms": self.latencia_toleravel_ms,
            "fracao_minima_preenchida": self.fracao_minima_preenchida,
        }


@dataclass(slots=True)
class RelatorioShadow:
    total: int = 0
    avaliadas: int = 0
    fieis: int = 0
    desvios_toleraveis: int = 0
    desvios_materiais: int = 0
    nao_executaveis: int = 0
    pendentes: int = 0
    slippage_mediano_pct: float | None = None
    slippage_p95_pct: float | None = None
    latencia_mediana_ms: float | None = None
    fracao_preenchida_mediana: float | None = None
    dias_observados: int = 0
    avisos: list[str] = field(default_factory=list)

    @property
    def fidelidade(self) -> float:
        """Fração das decisões avaliadas que a execução reproduziu.

        Inclui desvio tolerável como fiel: o que interessa é se a execução
        representou a decisão, não se foi idêntica ao centavo.
        """
        if self.avaliadas == 0:
            return 0.0
        return (self.fieis + self.desvios_toleraveis) / self.avaliadas

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total, "avaliadas": self.avaliadas,
            "fieis": self.fieis,
            "desvios_toleraveis": self.desvios_toleraveis,
            "desvios_materiais": self.desvios_materiais,
            "nao_executaveis": self.nao_executaveis,
            "pendentes": self.pendentes,
            "fidelidade": round(self.fidelidade, 4),
            "slippage_mediano_pct": (round(self.slippage_mediano_pct, 4)
                                     if self.slippage_mediano_pct is not None
                                     else None),
            "slippage_p95_pct": (round(self.slippage_p95_pct, 4)
                                 if self.slippage_p95_pct is not None
                                 else None),
            "latencia_mediana_ms": self.latencia_mediana_ms,
            "fracao_preenchida_mediana": (
                round(self.fracao_preenchida_mediana, 4)
                if self.fracao_preenchida_mediana is not None else None),
            "dias_observados": self.dias_observados,
            "avisos": self.avisos,
            "observacao": "fidelidade mede se o sistema EXECUTA o que DECIDE; "
                          "é diferente de a estratégia dar lucro",
        }


class ShadowRunner:
    """Acumula decisões, confronta com a execução e mede a fidelidade."""

    def __init__(self, limiares: LimiaresShadow | None = None):
        self.limiares = limiares or LimiaresShadow()
        self.decisoes: dict[str, DecisaoShadow] = {}
        self._seq = 0

    def registrar_decisao(self, symbol: str, side: Side, *,
                          preco: float, stop: float, alvo: float,
                          size: float, score: float = 0.0,
                          estrategia: str = "",
                          agora_ms: int = 0) -> DecisaoShadow:
        self._seq += 1
        d = DecisaoShadow(
            id=f"shadow-{self._seq:06d}", symbol=symbol, side=side,
            decidido_em_ms=agora_ms, preco_decisao=preco, stop_decisao=stop,
            alvo_decisao=alvo, size_decisao=size, score=score,
            estrategia=estrategia)
        self.decisoes[d.id] = d
        return d

    def registrar_execucao(self, decisao_id: str, *,
                           preco_execucao: float | None,
                           size_executada: float | None,
                           executado_em_ms: int | None,
                           motivo_nao_executado: str = "") -> DecisaoShadow:
        """Registra o que a execução simulada conseguiu fazer."""
        d = self.decisoes[decisao_id]
        lim = self.limiares

        if preco_execucao is None or not size_executada:
            d.resultado = ResultadoShadow.NAO_EXECUTAVEL
            d.observacoes.append(
                motivo_nao_executado
                or "a decisão não pôde ser executada: em operação real ela "
                   "não teria virado posição")
            return d

        d.preco_execucao = preco_execucao
        d.size_executada = size_executada
        d.executado_em_ms = executado_em_ms

        slip = d.slippage_pct or 0.0
        frac = d.fracao_preenchida or 0.0
        lat = d.latencia_ms or 0

        problemas: list[str] = []
        if slip > lim.slippage_material_pct:
            problemas.append(
                f"slippage de {slip:.3f}% acima do material "
                f"({lim.slippage_material_pct:.2f}%): a entrada real fica "
                f"longe do preço que originou a decisão")
        if frac < lim.fracao_minima_preenchida:
            problemas.append(
                f"apenas {frac:.0%} do tamanho foi preenchido: a posição real "
                f"é menor que a dimensionada pela gestão de risco")
        if lat > lim.latencia_toleravel_ms:
            problemas.append(
                f"latência de {lat} ms acima do tolerável "
                f"({lim.latencia_toleravel_ms} ms)")

        if problemas:
            d.resultado = ResultadoShadow.DESVIO_MATERIAL
            d.observacoes.extend(problemas)
        elif slip > lim.slippage_toleravel_pct:
            d.resultado = ResultadoShadow.DESVIO_TOLERAVEL
            d.observacoes.append(f"slippage de {slip:.3f}%, dentro do "
                                 f"tolerável")
        else:
            d.resultado = ResultadoShadow.FIEL
        return d

    def registrar_movimento(self, decisao_id: str, *,
                            preco_max: float, preco_min: float,
                            resultado_r: float | None = None) -> DecisaoShadow:
        """Registra o que o mercado fez depois da decisão."""
        d = self.decisoes[decisao_id]
        d.preco_max_observado = preco_max
        d.preco_min_observado = preco_min
        d.resultado_r = resultado_r

        # Confere se o plano era coerente com o que o mercado ofereceu.
        long = d.side is Side.LONG
        alvo_alcancado = (preco_max >= d.alvo_decisao if long
                          else preco_min <= d.alvo_decisao)
        stop_atingido = (preco_min <= d.stop_decisao if long
                         else preco_max >= d.stop_decisao)
        if stop_atingido and alvo_alcancado:
            d.observacoes.append(
                "stop e alvo foram ambos tocados na janela observada: sem "
                "dado intrabar não é possível saber qual veio primeiro, e o "
                "backtest assume o stop (pessimista)")
        return d

    def relatorio(self, *, dias_observados: int = 0) -> RelatorioShadow:
        rel = RelatorioShadow(total=len(self.decisoes),
                              dias_observados=dias_observados)
        slippages: list[float] = []
        latencias: list[int] = []
        fracoes: list[float] = []

        for d in self.decisoes.values():
            if d.resultado is ResultadoShadow.PENDENTE:
                rel.pendentes += 1
                continue
            rel.avaliadas += 1
            if d.resultado is ResultadoShadow.FIEL:
                rel.fieis += 1
            elif d.resultado is ResultadoShadow.DESVIO_TOLERAVEL:
                rel.desvios_toleraveis += 1
            elif d.resultado is ResultadoShadow.DESVIO_MATERIAL:
                rel.desvios_materiais += 1
            elif d.resultado is ResultadoShadow.NAO_EXECUTAVEL:
                rel.nao_executaveis += 1

            if (s := d.slippage_pct) is not None:
                slippages.append(s)
            if (l := d.latencia_ms) is not None:
                latencias.append(l)
            if (f := d.fracao_preenchida) is not None:
                fracoes.append(f)

        if slippages:
            ordenado = sorted(slippages)
            rel.slippage_mediano_pct = statistics.median(ordenado)
            idx = min(len(ordenado) - 1, int(len(ordenado) * 0.95))
            rel.slippage_p95_pct = ordenado[idx]
        if latencias:
            rel.latencia_mediana_ms = statistics.median(latencias)
        if fracoes:
            rel.fracao_preenchida_mediana = statistics.median(fracoes)

        # ------------------------------------------------------- avisos
        if rel.avaliadas == 0:
            rel.avisos.append("nenhuma decisão avaliada ainda")
        if rel.nao_executaveis and rel.avaliadas:
            frac = rel.nao_executaveis / rel.avaliadas
            if frac > 0.10:
                rel.avisos.append(
                    f"{frac:.0%} das decisões não eram executáveis: o "
                    f"backtest contou operações que, ao vivo, não "
                    f"aconteceriam")
        if rel.slippage_p95_pct is not None and rel.slippage_p95_pct > 0.25:
            rel.avisos.append(
                f"slippage no percentil 95 é {rel.slippage_p95_pct:.3f}%: se "
                f"o backtest assumiu menos que isso, a expectativa medida "
                f"está superestimada")
        if rel.fidelidade < 0.85 and rel.avaliadas >= 20:
            rel.avisos.append(
                f"fidelidade de {rel.fidelidade:.0%}: o sistema decide uma "
                f"coisa e executa outra em parte relevante dos casos. "
                f"Promover para execução assistida agora seria prematuro.")
        return rel
