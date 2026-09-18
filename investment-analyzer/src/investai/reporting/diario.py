"""Relatório diário.

Uma seção deste relatório é incomum e proposital: **oportunidades
rejeitadas**. A maioria dos sistemas só mostra o que recomendou, o que dá a
impressão de que o sistema "encontrou 3 oportunidades hoje". A informação
completa é: analisou 20 pares, rejeitou 17, e por quais motivos. Sem isso não
se distingue um sistema seletivo de um sistema que não achou nada.

O relatório também separa BACKTEST, PAPER e LIVE sem misturar, porque somar
resultado simulado com resultado real produz um número que não significa nada.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence


def _data_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d")


@dataclass(slots=True)
class BlocoDesempenho:
    """Desempenho de um modo. Nunca somado com outro modo."""

    modo: str
    trades: int = 0
    win_rate: float = 0.0
    pnl_usd: float = 0.0
    expectancy_r: float = 0.0
    profit_factor: float = 0.0
    taxas_usd: float = 0.0
    drawdown_pct: float = 0.0
    capital_atual: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "modo": self.modo, "trades": self.trades,
            "win_rate": round(self.win_rate, 4),
            "pnl_usd": round(self.pnl_usd, 2),
            "expectancy_r": round(self.expectancy_r, 4),
            "profit_factor": round(self.profit_factor, 3),
            "taxas_usd": round(self.taxas_usd, 2),
            "drawdown_pct": round(self.drawdown_pct, 2),
            "capital_atual": round(self.capital_atual, 2),
        }


@dataclass(slots=True)
class OportunidadeRejeitada:
    symbol: str
    direcao: str
    score: float
    decisao: str
    motivo: str
    categoria_motivo: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "direcao": self.direcao,
            "score": round(self.score, 2), "decisao": self.decisao,
            "motivo": self.motivo,
            "categoria_motivo": self.categoria_motivo,
        }


@dataclass(slots=True)
class RelatorioDiario:
    data: str
    gerado_em_ms: int
    modo_de_dados: str = ""
    # Mercado
    pares_analisados: int = 0
    regimes: dict[str, str] = field(default_factory=dict)
    mudancas_de_regime: list[dict[str, str]] = field(default_factory=list)
    # Oportunidades
    operaveis: list[dict[str, Any]] = field(default_factory=list)
    em_observacao: list[dict[str, Any]] = field(default_factory=list)
    rejeitadas: list[OportunidadeRejeitada] = field(default_factory=list)
    # Risco e portfólio
    risco: dict[str, Any] = field(default_factory=dict)
    portfolio: dict[str, Any] = field(default_factory=dict)
    stress: dict[str, Any] = field(default_factory=dict)
    # Desempenho separado por modo
    desempenho: list[BlocoDesempenho] = field(default_factory=list)
    # Estratégias
    estrategias_por_fase: dict[str, list[str]] = field(default_factory=dict)
    promocoes: list[dict[str, Any]] = field(default_factory=list)
    # Processo
    journal: dict[str, Any] = field(default_factory=dict)
    # Operacional
    saude: dict[str, Any] = field(default_factory=dict)
    cobertura_de_dados: dict[str, Any] = field(default_factory=dict)
    anomalias: list[dict[str, Any]] = field(default_factory=list)
    alertas_urgentes: list[dict[str, Any]] = field(default_factory=list)
    eventos_economicos: list[dict[str, Any]] = field(default_factory=list)
    lacunas: list[str] = field(default_factory=list)

    @property
    def resumo_executivo(self) -> str:
        total = len(self.operaveis)
        if total == 0:
            base = (f"NENHUMA OPORTUNIDADE ATENDE AOS CRITÉRIOS de "
                    f"{self.pares_analisados} pares analisados. CAPITAL "
                    f"PRESERVADO.")
        else:
            base = (f"{total} candidato(s) aprovado(s) de "
                    f"{self.pares_analisados} pares analisados.")
        rejeitadas = len(self.rejeitadas)
        obs = len(self.em_observacao)
        return (f"{base} {obs} em observação e {rejeitadas} rejeitado(s). "
                f"Rejeitar é o resultado mais frequente e mais valioso de um "
                f"sistema seletivo.")

    def motivos_de_rejeicao(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rejeitadas:
            chave = r.categoria_motivo or r.decisao
            out[chave] = out.get(chave, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data, "gerado_em_ms": self.gerado_em_ms,
            "modo_de_dados": self.modo_de_dados,
            "resumo_executivo": self.resumo_executivo,
            "mercado": {
                "pares_analisados": self.pares_analisados,
                "regimes": self.regimes,
                "mudancas_de_regime": self.mudancas_de_regime,
            },
            "oportunidades": {
                "operaveis": self.operaveis,
                "em_observacao": self.em_observacao,
                "rejeitadas": [r.to_dict() for r in self.rejeitadas],
                "motivos_de_rejeicao": self.motivos_de_rejeicao(),
            },
            "risco": self.risco,
            "portfolio": self.portfolio,
            "stress": self.stress,
            "desempenho_por_modo": [d.to_dict() for d in self.desempenho],
            "estrategias": {
                "por_fase": self.estrategias_por_fase,
                "promocoes_do_dia": self.promocoes,
            },
            "processo": self.journal,
            "operacional": {
                "saude": self.saude,
                "cobertura_de_dados": self.cobertura_de_dados,
                "anomalias": self.anomalias,
                "alertas_urgentes": self.alertas_urgentes,
                "eventos_economicos": self.eventos_economicos,
            },
            "lacunas_declaradas": self.lacunas,
            "avisos": [
                "BACKTEST, PAPER e LIVE são reportados separadamente; somar "
                "resultado simulado com resultado real produz número sem "
                "significado.",
                "Score é ferramenta quantitativa interna e não representa "
                "probabilidade de lucro.",
                "Nenhuma estimativa aqui é garantia de resultado.",
            ],
        }

    def para_texto(self) -> str:
        """Versão legível, para leitura rápida ou envio por e-mail."""
        L: list[str] = []
        L.append("=" * 74)
        L.append(f"RELATÓRIO DIÁRIO — {self.data}")
        if self.modo_de_dados:
            L.append(f"modo de dados: {self.modo_de_dados}")
        L.append("=" * 74)
        L.append("")
        L.append("RESUMO")
        L.append(f"  {self.resumo_executivo}")
        L.append("")

        if self.saude:
            L.append(f"SAÚDE DO SISTEMA: {self.saude.get('estado_geral', '?')}"
                     f" | pode abrir posição: "
                     f"{self.saude.get('pode_abrir_posicao')}")
            for m in (self.saude.get("motivos") or [])[:3]:
                L.append(f"  - {m}")
            L.append("")

        if self.operaveis:
            L.append("CANDIDATOS APROVADOS")
            for o in self.operaveis:
                L.append(f"  {o.get('symbol', '?')} {o.get('direcao', '')} | "
                         f"score {o.get('score', 0)} | "
                         f"decisão {o.get('decisao', '')}")
            L.append("")

        if self.rejeitadas:
            L.append(f"REJEITADOS ({len(self.rejeitadas)})")
            for categoria, n in sorted(self.motivos_de_rejeicao().items(),
                                       key=lambda kv: -kv[1]):
                L.append(f"  {n:3}x {categoria}")
            L.append("")

        if self.risco:
            L.append("RISCO")
            L.append(f"  capital US$ {self.risco.get('capital_atual', 0)} | "
                     f"drawdown {self.risco.get('drawdown_pct', 0)}% | "
                     f"kill switch: {self.risco.get('kill_switch')}")
            L.append("")

        if self.portfolio:
            L.append("PORTFÓLIO")
            L.append(f"  {self.portfolio.get('n_posicoes', 0)} posições = "
                     f"{self.portfolio.get('apostas_efetivas', '?')} apostas "
                     f"independentes")
            for a in (self.portfolio.get("avisos") or [])[:2]:
                L.append(f"  ! {a}")
            L.append("")

        if self.desempenho:
            L.append("DESEMPENHO (modos nunca somados)")
            for d in self.desempenho:
                x = d.to_dict()
                L.append(f"  {x['modo']:9} {x['trades']:4} trades | "
                         f"acerto {x['win_rate']:.1%} | "
                         f"exp {x['expectancy_r']:+.3f}R | "
                         f"PnL US$ {x['pnl_usd']:+.2f}")
            L.append("")

        if self.journal:
            L.append("PROCESSO")
            q = self.journal.get("por_quadrante", {})
            for k, v in q.items():
                if v:
                    L.append(f"  {v:3}x {k}")
            for a in (self.journal.get("alertas") or [])[:2]:
                L.append(f"  ! {a}")
            L.append("")

        if self.estrategias_por_fase:
            L.append("ESTRATÉGIAS POR FASE")
            for fase, chaves in self.estrategias_por_fase.items():
                L.append(f"  {fase:16} {', '.join(chaves)}")
            L.append("")

        if self.alertas_urgentes:
            L.append("ALERTAS URGENTES")
            for a in self.alertas_urgentes[:6]:
                L.append(f"  [{a.get('categoria', '')}] {a.get('titulo', '')}")
            L.append("")

        if self.lacunas:
            L.append("LACUNAS DECLARADAS (fontes não configuradas)")
            for x in self.lacunas[:8]:
                L.append(f"  - {x}")
            L.append("")

        L.append("-" * 74)
        L.append("BACKTEST, PAPER e LIVE são reportados separadamente. Score é")
        L.append("ferramenta interna e não representa probabilidade de lucro.")
        L.append("Nenhuma estimativa aqui é garantia de resultado.")
        return "\n".join(L)


def montar_relatorio(*, pares_analisados: int,
                     operaveis: Sequence[dict[str, Any]] = (),
                     em_observacao: Sequence[dict[str, Any]] = (),
                     rejeitadas: Sequence[OportunidadeRejeitada] = (),
                     risco: dict[str, Any] | None = None,
                     portfolio: dict[str, Any] | None = None,
                     stress: dict[str, Any] | None = None,
                     desempenho: Sequence[BlocoDesempenho] = (),
                     estrategias_por_fase: dict[str, list[str]] | None = None,
                     promocoes: Sequence[dict[str, Any]] = (),
                     journal: dict[str, Any] | None = None,
                     saude: dict[str, Any] | None = None,
                     cobertura_de_dados: dict[str, Any] | None = None,
                     regimes: dict[str, str] | None = None,
                     mudancas_de_regime: Sequence[dict[str, str]] = (),
                     anomalias: Sequence[dict[str, Any]] = (),
                     alertas_urgentes: Sequence[dict[str, Any]] = (),
                     eventos_economicos: Sequence[dict[str, Any]] = (),
                     lacunas: Sequence[str] = (),
                     modo_de_dados: str = "",
                     agora_ms: int | None = None) -> RelatorioDiario:
    agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
    return RelatorioDiario(
        data=_data_utc(agora), gerado_em_ms=agora,
        modo_de_dados=modo_de_dados, pares_analisados=pares_analisados,
        regimes=dict(regimes or {}),
        mudancas_de_regime=list(mudancas_de_regime),
        operaveis=list(operaveis), em_observacao=list(em_observacao),
        rejeitadas=list(rejeitadas), risco=dict(risco or {}),
        portfolio=dict(portfolio or {}), stress=dict(stress or {}),
        desempenho=list(desempenho),
        estrategias_por_fase=dict(estrategias_por_fase or {}),
        promocoes=list(promocoes), journal=dict(journal or {}),
        saude=dict(saude or {}),
        cobertura_de_dados=dict(cobertura_de_dados or {}),
        anomalias=list(anomalias), alertas_urgentes=list(alertas_urgentes),
        eventos_economicos=list(eventos_economicos), lacunas=list(lacunas))
