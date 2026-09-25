"""Correlação e detecção de falsa diversificação.

O problema que isto resolve
---------------------------
"Tenho BTC, ETH, SOL, AVAX e LINK — estou diversificado." Não está. Em queda
de mercado essas cinco posições caem juntas, com correlação que se aproxima de
1 exatamente no momento em que a diversificação seria útil. O número de
tickers não mede diversificação; a estrutura de correlação mede.

Duas medidas são calculadas:

* **correlação de Pearson** sobre retornos — a medida usual;
* **correlação em cauda** — correlação medida SÓ nos dias de queda forte.
  É a que importa: ativos que se descorrelacionam em mercado calmo e se
  correlacionam no pânico dão falsa sensação de proteção.

E uma síntese: o **número efetivo de apostas independentes**, que costuma ser
muito menor que o número de posições.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..models import Candle, Position


def retornos(closes: Sequence[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1.0
            for i in range(1, len(closes)) if closes[i - 1] > 0]


def pearson(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Correlação de Pearson. `None` quando não é calculável."""
    n = min(len(a), len(b))
    if n < 10:
        return None
    a, b = list(a[-n:]), list(b[-n:])
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da == 0 or db == 0:
        return None
    return max(-1.0, min(1.0, num / (da * db)))


def dependencia_de_cauda(a: Sequence[float], b: Sequence[float],
                         percentil: float = 0.20) -> float | None:
    """Dependência de cauda por co-excedência, normalizada para 0..1.

    Mede: dado que `a` está entre seus piores `percentil` retornos, com que
    frequência `b` também está entre os piores dele?

    Por que NÃO usar Pearson no subconjunto da cauda
    ------------------------------------------------
    Calcular Pearson apenas nos dias de queda de `a` parece a medida óbvia,
    mas está errado: condicionar numa faixa estreita de `a` TRUNCA a variância
    dele naquele subconjunto, e correlação com variância truncada é enviesada
    PARA BAIXO. O resultado é que essa abordagem reporta correlação de cauda
    menor que a normal mesmo quando a dependência real é idêntica ou maior —
    exatamente a conclusão oposta à correta, e perigosa, porque sugere
    proteção onde não há.

    A co-excedência não sofre desse viés, porque conta eventos conjuntos em
    vez de medir dispersão condicionada.

    Normalização: sob independência a frequência esperada é `percentil`
    (20% das vezes `b` também estaria na cauda por acaso). O valor devolvido
    é `(observado - percentil) / (1 - percentil)`, então 0 significa
    independência na cauda e 1 significa que os dois sempre caem juntos.
    """
    n = min(len(a), len(b))
    if n < 30:
        return None
    a_j, b_j = list(a[-n:]), list(b[-n:])
    k = max(5, int(n * percentil))
    corte_a = sorted(a_j)[k - 1]
    corte_b = sorted(b_j)[k - 1]

    na_cauda_a = [i for i, x in enumerate(a_j) if x <= corte_a]
    if len(na_cauda_a) < 5:
        return None
    conjuntos = sum(1 for i in na_cauda_a if b_j[i] <= corte_b)
    frequencia = conjuntos / len(na_cauda_a)

    denom = 1.0 - percentil
    if denom <= 0:
        return None
    return max(0.0, min(1.0, (frequencia - percentil) / denom))


@dataclass(slots=True)
class MatrizCorrelacao:
    simbolos: list[str]
    pearson: dict[str, dict[str, float | None]] = field(default_factory=dict)
    cauda: dict[str, dict[str, float | None]] = field(default_factory=dict)
    janela: int = 0
    avisos: list[str] = field(default_factory=list)

    def par(self, a: str, b: str, *, em_cauda: bool = False) -> float | None:
        fonte = self.cauda if em_cauda else self.pearson
        return fonte.get(a, {}).get(b)

    def pares_acima(self, limiar: float, *,
                    em_cauda: bool = False) -> list[tuple[str, str, float]]:
        fonte = self.cauda if em_cauda else self.pearson
        out: list[tuple[str, str, float]] = []
        for i, a in enumerate(self.simbolos):
            for b in self.simbolos[i + 1:]:
                v = fonte.get(a, {}).get(b)
                if v is not None and v >= limiar:
                    out.append((a, b, v))
        return sorted(out, key=lambda t: t[2], reverse=True)

    def media(self, *, em_cauda: bool = False) -> float | None:
        fonte = self.cauda if em_cauda else self.pearson
        vals = [v for i, a in enumerate(self.simbolos)
                for b in self.simbolos[i + 1:]
                if (v := fonte.get(a, {}).get(b)) is not None]
        return sum(vals) / len(vals) if vals else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "simbolos": self.simbolos, "janela": self.janela,
            "pearson": self.pearson, "cauda": self.cauda,
            "media_pearson": self.media(),
            "media_cauda": self.media(em_cauda=True),
            "avisos": self.avisos,
            "observacao": "'cauda' é dependência de cauda por co-excedência "
                          "(0 = independentes nas quedas, 1 = caem sempre "
                          "juntos), não Pearson condicionado — que seria "
                          "enviesado para baixo por truncamento de variância",
        }


def matriz_correlacao(series: dict[str, Sequence[Candle]], *,
                      janela: int = 200) -> MatrizCorrelacao:
    """Monta a matriz a partir de séries de candles alinhadas por timestamp."""
    simbolos = sorted(series)
    m = MatrizCorrelacao(simbolos=simbolos, janela=janela)

    # Alinha por timestamp: comparar retornos de barras diferentes produz
    # correlação sem sentido.
    por_symbol: dict[str, dict[int, float]] = {
        s: {c.ts: c.close for c in velas} for s, velas in series.items()}
    comuns = set.intersection(*(set(d) for d in por_symbol.values())) \
        if por_symbol else set()
    ts_ordenados = sorted(comuns)[-janela:]

    if len(ts_ordenados) < 30:
        m.avisos.append(
            f"apenas {len(ts_ordenados)} timestamps em comum entre as séries: "
            f"correlação não é confiável")

    rets: dict[str, list[float]] = {}
    for s in simbolos:
        closes = [por_symbol[s][ts] for ts in ts_ordenados]
        rets[s] = retornos(closes)

    for a in simbolos:
        m.pearson[a] = {}
        m.cauda[a] = {}
        for b in simbolos:
            if a == b:
                m.pearson[a][b] = 1.0
                m.cauda[a][b] = 1.0
                continue
            m.pearson[a][b] = pearson(rets[a], rets[b])
            m.cauda[a][b] = dependencia_de_cauda(rets[a], rets[b])
    return m


@dataclass(slots=True)
class AnaliseDiversificacao:
    n_posicoes: int
    apostas_efetivas: float
    concentracao_hhi: float
    correlacao_media: float | None
    correlacao_media_cauda: float | None
    pares_altamente_correlacionados: list[dict[str, Any]] = field(
        default_factory=list)
    grupos: dict[str, list[str]] = field(default_factory=dict)
    exposicao_por_grupo: dict[str, float] = field(default_factory=dict)
    falsa_diversificacao: bool = False
    avisos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_posicoes": self.n_posicoes,
            "apostas_efetivas": round(self.apostas_efetivas, 2),
            "concentracao_hhi": round(self.concentracao_hhi, 4),
            "correlacao_media": (round(self.correlacao_media, 3)
                                 if self.correlacao_media is not None else None),
            "correlacao_media_cauda": (
                round(self.correlacao_media_cauda, 3)
                if self.correlacao_media_cauda is not None else None),
            "pares_altamente_correlacionados":
                self.pares_altamente_correlacionados,
            "grupos": self.grupos,
            "exposicao_por_grupo": {
                k: round(v, 2) for k, v in self.exposicao_por_grupo.items()},
            "falsa_diversificacao": self.falsa_diversificacao,
            "avisos": self.avisos,
        }


def apostas_efetivas(correlacao_media: float | None, n: int) -> float:
    """Número de apostas independentes equivalente a `n` posições.

    Com correlação média `ρ`, N posições equivalem a
    `N / (1 + (N-1)·ρ)` apostas independentes. Cinco posições com ρ = 0,8
    equivalem a 1,47 apostas — não a cinco.
    """
    if n <= 0:
        return 0.0
    if correlacao_media is None:
        return float(n)
    rho = max(0.0, min(1.0, correlacao_media))
    denom = 1.0 + (n - 1) * rho
    return n / denom if denom > 0 else float(n)


def analisar_diversificacao(posicoes: Sequence[Position],
                            matriz: MatrizCorrelacao | None = None, *,
                            limiar_alta_correlacao: float = 0.70
                            ) -> AnaliseDiversificacao:
    """Mede se a carteira é diversificada de fato, não só em contagem."""
    from ..risk.manager import grupo_de

    n = len(posicoes)
    notionais = [abs(p.notional_usd) for p in posicoes]
    total = sum(notionais)

    # Herfindahl: 1,0 = tudo numa posição; 1/n = perfeitamente distribuído.
    hhi = (sum((x / total) ** 2 for x in notionais) if total > 0 else 0.0)

    grupos: dict[str, list[str]] = {}
    exposicao: dict[str, float] = {}
    for p in posicoes:
        g = grupo_de(p.symbol)
        grupos.setdefault(g, []).append(p.symbol)
        exposicao[g] = exposicao.get(g, 0.0) + abs(p.notional_usd)

    corr_media = matriz.media() if matriz else None
    corr_cauda = matriz.media(em_cauda=True) if matriz else None
    efetivas = apostas_efetivas(
        corr_cauda if corr_cauda is not None else corr_media, n)

    pares: list[dict[str, Any]] = []
    if matriz:
        simbolos_abertos = {p.symbol for p in posicoes}
        for a, b, v in matriz.pares_acima(limiar_alta_correlacao):
            if a in simbolos_abertos and b in simbolos_abertos:
                pares.append({"a": a, "b": b, "pearson": round(v, 3),
                              "cauda": (round(c, 3)
                                        if (c := matriz.par(a, b,
                                                            em_cauda=True))
                                        is not None else None)})
        for a, b, v in matriz.pares_acima(limiar_alta_correlacao,
                                          em_cauda=True):
            if a in simbolos_abertos and b in simbolos_abertos and not any(
                    d["a"] == a and d["b"] == b for d in pares):
                pares.append({"a": a, "b": b,
                              "pearson": (round(pv, 3)
                                          if (pv := matriz.par(a, b))
                                          is not None else None),
                              "cauda": round(v, 3),
                              "nota": "descorrelacionado em mercado normal, "
                                      "correlacionado na queda"})

    analise = AnaliseDiversificacao(
        n_posicoes=n, apostas_efetivas=efetivas, concentracao_hhi=hhi,
        correlacao_media=corr_media, correlacao_media_cauda=corr_cauda,
        pares_altamente_correlacionados=pares, grupos=grupos,
        exposicao_por_grupo=exposicao)

    # --------------------------------------------------------- diagnóstico
    if n >= 3 and efetivas < n * 0.5:
        analise.falsa_diversificacao = True
        analise.avisos.append(
            f"FALSA DIVERSIFICAÇÃO: {n} posições equivalem a apenas "
            f"{efetivas:.1f} apostas independentes (correlação média de "
            f"{(corr_cauda or corr_media or 0):.2f}). A carteira tem o risco "
            f"de {efetivas:.0f} posição(ões), não de {n}.")

    if total > 0:
        maior_grupo = max(exposicao, key=lambda k: exposicao[k])
        fracao = exposicao[maior_grupo] / total
        if fracao > 0.60 and len(exposicao) > 1:
            analise.avisos.append(
                f"{fracao:.0%} da exposição está no grupo '{maior_grupo}' "
                f"({', '.join(grupos[maior_grupo])})")
        elif len(exposicao) == 1 and n > 1:
            analise.avisos.append(
                f"todas as {n} posições pertencem ao grupo '{maior_grupo}': "
                f"é uma aposta única repartida")

    if hhi > 0.5 and n > 1:
        analise.avisos.append(
            f"concentração alta (HHI {hhi:.2f}): uma posição domina a "
            f"carteira")

    if corr_cauda is not None and corr_cauda > 0.45:
        analise.avisos.append(
            f"dependência de cauda de {corr_cauda:.2f}: quando um ativo da "
            f"carteira está entre suas piores quedas, os outros também estão "
            f"na maior parte das vezes. A proteção desaparece justamente "
            f"quando seria necessária.")
    if (corr_media is not None and corr_cauda is not None
            and corr_cauda > corr_media):
        analise.avisos.append(
            f"a dependência nas quedas ({corr_cauda:.2f}) supera a "
            f"correlação em mercado normal ({corr_media:.2f})")

    return analise
