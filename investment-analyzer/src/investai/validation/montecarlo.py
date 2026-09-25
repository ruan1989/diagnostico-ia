"""Monte Carlo sobre a sequência de resultados.

O que isto responde
-------------------
Um backtest produz UMA sequência de operações. Ela é uma amostra entre muitas
possíveis: a mesma estratégia, com a mesma vantagem, teria produzido curvas
bem diferentes se as operações tivessem chegado em outra ordem. Olhar só a
curva realizada leva a duas conclusões erradas:

* subestimar o drawdown — a sequência realizada pode simplesmente não ter
  tido a má sorte de agrupar as perdas;
* superestimar a estabilidade — um resultado bom pode depender de 3 operações
  excepcionais que talvez não se repitam.

A reamostragem (bootstrap) embaralha e reamostra os resultados observados
milhares de vezes e devolve a DISTRIBUIÇÃO de drawdown, retorno e ruína. O
número que interessa para decidir não é a média: é o percentil ruim.

Duas variantes são calculadas de propósito:

* **IID** — reamostra com reposição, tratando cada operação como
  independente. Otimista, porque perdas reais se agrupam.
* **Em blocos** — reamostra blocos contíguos, preservando o agrupamento de
  perdas presente nos dados. É a variante que o gate de promoção usa.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(slots=True)
class DistribuicaoMC:
    """Percentis de uma métrica ao longo das simulações."""

    p05: float = 0.0
    p25: float = 0.0
    mediana: float = 0.0
    p75: float = 0.0
    p95: float = 0.0
    media: float = 0.0
    pior: float = 0.0
    melhor: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {k: round(v, 4) for k, v in {
            "p05": self.p05, "p25": self.p25, "mediana": self.mediana,
            "p75": self.p75, "p95": self.p95, "media": self.media,
            "pior": self.pior, "melhor": self.melhor,
        }.items()}


def _percentil(ordenado: Sequence[float], q: float) -> float:
    if not ordenado:
        return 0.0
    if len(ordenado) == 1:
        return ordenado[0]
    pos = q * (len(ordenado) - 1)
    baixo = int(pos)
    alto = min(baixo + 1, len(ordenado) - 1)
    peso = pos - baixo
    return ordenado[baixo] * (1 - peso) + ordenado[alto] * peso


def _distribuicao(valores: Sequence[float]) -> DistribuicaoMC:
    if not valores:
        return DistribuicaoMC()
    ordenado = sorted(valores)
    return DistribuicaoMC(
        p05=_percentil(ordenado, 0.05), p25=_percentil(ordenado, 0.25),
        mediana=_percentil(ordenado, 0.50), p75=_percentil(ordenado, 0.75),
        p95=_percentil(ordenado, 0.95),
        media=sum(ordenado) / len(ordenado),
        pior=ordenado[0], melhor=ordenado[-1],
    )


@dataclass(slots=True)
class RelatorioMonteCarlo:
    n_simulacoes: int
    n_trades_por_sim: int
    amostra_original: int
    modo: str
    retorno_final_pct: DistribuicaoMC = field(default_factory=DistribuicaoMC)
    max_drawdown_pct: DistribuicaoMC = field(default_factory=DistribuicaoMC)
    perdas_consecutivas: DistribuicaoMC = field(default_factory=DistribuicaoMC)
    prob_prejuizo: float = 0.0
    prob_ruina: float = 0.0
    limiar_ruina_pct: float = 50.0
    # Drawdown que 95% das simulações não excederam: é o número a usar para
    # dimensionar tolerância, não a média.
    drawdown_p95: float = 0.0
    avisos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_simulacoes": self.n_simulacoes,
            "n_trades_por_sim": self.n_trades_por_sim,
            "amostra_original": self.amostra_original,
            "modo": self.modo,
            "retorno_final_pct": self.retorno_final_pct.to_dict(),
            "max_drawdown_pct": self.max_drawdown_pct.to_dict(),
            "perdas_consecutivas": self.perdas_consecutivas.to_dict(),
            "prob_prejuizo": round(self.prob_prejuizo, 4),
            "prob_ruina": round(self.prob_ruina, 4),
            "limiar_ruina_pct": self.limiar_ruina_pct,
            "drawdown_p95": round(self.drawdown_p95, 3),
            "avisos": self.avisos,
            "observacao": "use o percentil ruim (p95 de drawdown, p05 de "
                          "retorno) para decidir; a média esconde a cauda",
        }


def _simular_curva(retornos_r: Sequence[float], risco_frac: float,
                   capital: float) -> tuple[float, float, int]:
    """Aplica a sequência de retornos em R e devolve (retorno %, maxDD %, seq).

    Cada operação move o capital por `retorno_R × risco_frac × capital`, ou
    seja, o risco é recalculado sobre o capital corrente (risco fixo
    fracionário). É assim que o sistema opera, então é assim que a simulação
    tem de ser.
    """
    equity = capital
    pico = capital
    max_dd = 0.0
    seq_perdas = 0
    pior_seq = 0

    for r in retornos_r:
        equity += equity * risco_frac * r
        if equity <= 0:
            return -100.0, 100.0, max(pior_seq, seq_perdas + 1)
        pico = max(pico, equity)
        dd = (pico - equity) / pico * 100.0
        max_dd = max(max_dd, dd)
        if r < 0:
            seq_perdas += 1
            pior_seq = max(pior_seq, seq_perdas)
        else:
            seq_perdas = 0

    return (equity / capital - 1.0) * 100.0, max_dd, pior_seq


def _reamostrar_iid(base: Sequence[float], n: int,
                    rnd: random.Random) -> list[float]:
    return [base[rnd.randrange(len(base))] for _ in range(n)]


def _reamostrar_blocos(base: Sequence[float], n: int, tamanho_bloco: int,
                       rnd: random.Random) -> list[float]:
    """Bootstrap em blocos: preserva o agrupamento de perdas.

    Perdas reais vêm em sequência (mesmo regime desfavorável, posições
    correlacionadas). Reamostrar operação por operação destrói essa estrutura
    e produz drawdowns menores do que os que acontecem de verdade.
    """
    out: list[float] = []
    tamanho_bloco = max(1, min(tamanho_bloco, len(base)))
    while len(out) < n:
        inicio = rnd.randrange(len(base))
        for i in range(tamanho_bloco):
            out.append(base[(inicio + i) % len(base)])
            if len(out) >= n:
                break
    return out[:n]


def monte_carlo(retornos_r: Sequence[float], *,
                n_simulacoes: int = 5000,
                n_trades: int | None = None,
                risco_por_trade_frac: float = 0.005,
                capital: float = 1000.0,
                limiar_ruina_pct: float = 50.0,
                modo: str = "blocos",
                tamanho_bloco: int = 5,
                seed: int = 20240918) -> RelatorioMonteCarlo:
    """Reamostra os resultados observados e devolve a distribuição de risco."""
    base = [r for r in retornos_r if r is not None]
    if not base:
        return RelatorioMonteCarlo(
            n_simulacoes=0, n_trades_por_sim=0, amostra_original=0, modo=modo,
            avisos=["nenhuma operação na amostra — Monte Carlo não aplicável"])
    if modo not in {"iid", "blocos"}:
        raise ValueError(f"modo desconhecido: {modo!r} (use iid ou blocos)")
    if not 0 < risco_por_trade_frac < 1:
        raise ValueError("risco_por_trade_frac deve estar entre 0 e 1")

    n = n_trades or max(len(base), 100)
    rnd = random.Random(seed)

    retornos_finais: list[float] = []
    drawdowns: list[float] = []
    sequencias: list[float] = []
    ruinas = 0
    prejuizos = 0

    for _ in range(n_simulacoes):
        amostra = (_reamostrar_blocos(base, n, tamanho_bloco, rnd)
                   if modo == "blocos" else _reamostrar_iid(base, n, rnd))
        ret, dd, seq = _simular_curva(amostra, risco_por_trade_frac, capital)
        retornos_finais.append(ret)
        drawdowns.append(dd)
        sequencias.append(float(seq))
        if dd >= limiar_ruina_pct:
            ruinas += 1
        if ret < 0:
            prejuizos += 1

    rel = RelatorioMonteCarlo(
        n_simulacoes=n_simulacoes, n_trades_por_sim=n,
        amostra_original=len(base), modo=modo,
        retorno_final_pct=_distribuicao(retornos_finais),
        max_drawdown_pct=_distribuicao(drawdowns),
        perdas_consecutivas=_distribuicao(sequencias),
        prob_prejuizo=prejuizos / n_simulacoes,
        prob_ruina=ruinas / n_simulacoes,
        limiar_ruina_pct=limiar_ruina_pct,
        drawdown_p95=_percentil(sorted(drawdowns), 0.95),
    )

    # -------------------------------------------------------------- avisos
    if len(base) < 30:
        rel.avisos.append(
            f"amostra original de {len(base)} operações: a reamostragem "
            f"reaproveita os mesmos poucos resultados, então a distribuição "
            f"herda a limitação da amostra e tende a ser otimista")
    if rel.prob_ruina > 0.01:
        rel.avisos.append(
            f"probabilidade de perder {limiar_ruina_pct:.0f}% do capital é "
            f"{rel.prob_ruina:.2%} — inaceitável para operação real")
    if rel.prob_prejuizo > 0.35:
        rel.avisos.append(
            f"{rel.prob_prejuizo:.1%} das simulações terminam no prejuízo")
    if rel.drawdown_p95 > 25.0:
        rel.avisos.append(
            f"drawdown no percentil 95 é {rel.drawdown_p95:.1f}% — 1 em 20 "
            f"sequências passa disso")
    if rel.perdas_consecutivas.p95 >= 8:
        rel.avisos.append(
            f"sequências de até {rel.perdas_consecutivas.p95:.0f} perdas "
            f"seguidas são esperadas no percentil 95; confirme que isso é "
            f"tolerável antes de operar")
    return rel


def comparar_modos(retornos_r: Sequence[float], **kw) -> dict[str, Any]:
    """Roda IID e blocos e mostra o quanto a independência é otimista.

    Se o drawdown em blocos for muito maior que o IID, as perdas da estratégia
    são fortemente agrupadas — o que é informação de risco de primeira ordem.
    """
    kw.pop("modo", None)
    iid = monte_carlo(retornos_r, modo="iid", **kw)
    blocos = monte_carlo(retornos_r, modo="blocos", **kw)
    if iid.n_simulacoes == 0:
        return {"iid": iid.to_dict(), "blocos": blocos.to_dict(),
                "agrupamento": None}
    piora = (blocos.drawdown_p95 - iid.drawdown_p95) / max(iid.drawdown_p95, 1e-9)
    return {
        "iid": iid.to_dict(),
        "blocos": blocos.to_dict(),
        "agrupamento": {
            "drawdown_p95_iid": round(iid.drawdown_p95, 3),
            "drawdown_p95_blocos": round(blocos.drawdown_p95, 3),
            "piora_relativa": round(piora, 4),
            "interpretacao": (
                "perdas agrupadas: o drawdown real tende a ser bem pior que o "
                "estimado assumindo independência"
                if piora > 0.20 else
                "agrupamento moderado de perdas"
                if piora > 0.05 else
                "sem agrupamento relevante nesta amostra"),
        },
    }
