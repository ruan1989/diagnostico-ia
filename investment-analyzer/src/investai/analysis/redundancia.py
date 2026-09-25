"""Evidência redundante: quantas opiniões independentes existem de verdade.

O problema
----------
RSI, MACD e cruzamento de médias derivam todos do mesmo preço. Quando os
três "concordam", isso soa como três confirmações independentes, e é uma
só — vista de três ângulos. Contá-las como três infla a confiança
exatamente no momento em que ela deveria ser questionada: em tendência
forte, indicadores de tendência concordam por construção.

O mesmo vale um nível acima. Se o agente técnico e o agente quantitativo
estão ambos respondendo à mesma tendência recente, o consenso de "dois
agentes favoráveis" vale menos do que a contagem sugere.

Como se mede
------------
Correlação entre as SÉRIES de opinião, não entre as opiniões de uma única
análise. Uma análise isolada não tem correlação — ela tem coincidência. É
preciso histórico: como cada fonte se comportou ao longo de muitas
decisões.

Daí sai o **peso efetivo**, pelo fator de inflação de variância:

    m   = (wᵀ R w) / Σ wᵢ²          quanto a correlação infla a variância
    W_ef = Σ wᵢ / m

Com fontes independentes, R é a identidade, m vale 1 e W_ef é a soma dos
pesos. Com três fontes de peso igual perfeitamente correlacionadas, m vale 3
e W_ef cai para o peso de uma só. É uma medida conhecida, não uma penalidade
inventada.

A primeira versão deste módulo usava `(Σw)²/(wᵀRw)`, que devolve o número
efetivo de fontes e não o peso — e, com pesos desiguais, penalizava fontes
independentes só por terem pesos diferentes. A validação numérica pegou:
três fontes idênticas e três fontes independentes davam o mesmo resultado.

Um viés que fica, de propósito
------------------------------
A matriz usa `|r|`, porque o que interessa é quanta informação é
compartilhada, não o sinal. O efeito colateral é que ruído de amostra finita
sempre parece um pouco de redundância: com 200 observações, duas séries
independentes têm |r| esperado perto de 0,06, e o peso efetivo sai alguns
por cento abaixo do total. O erro é para o lado seguro — subestimar a
independência produz cautela, superestimar produz confiança inventada — e
encolhe com amostra maior.

O que se faz com isso
---------------------
Nada automático que mude o score sem aviso. O relatório entra na decisão
como contraindicação e como fator de desconto explícito, porque descontar
em silêncio produziria um score que ninguém consegue reproduzir na mão.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# Observações mínimas para uma correlação dizer alguma coisa. Abaixo disso a
# correlação entre duas séries é ruído com aparência de estrutura.
MIN_OBSERVACOES = 20

# A partir daqui duas fontes são tratadas como a mesma evidência.
LIMIAR_REDUNDANCIA = 0.80

# Abaixo disto o desconto é considerado material e vira aviso.
FATOR_ALERTA = 0.70


def correlacao(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Pearson entre duas séries alinhadas. None quando não dá para medir.

    Devolve None — e não zero — quando a amostra é pequena ou uma das séries
    é constante. Zero significaria "medi e são independentes", que é uma
    afirmação bem diferente de "não consegui medir".
    """
    n = min(len(a), len(b))
    if n < MIN_OBSERVACOES:
        return None
    xa, xb = list(a[:n]), list(b[:n])
    ma = sum(xa) / n
    mb = sum(xb) / n
    va = sum((x - ma) ** 2 for x in xa)
    vb = sum((x - mb) ** 2 for x in xb)
    if va <= 1e-12 or vb <= 1e-12:
        return None
    cov = sum((x - ma) * (y - mb) for x, y in zip(xa, xb))
    r = cov / math.sqrt(va * vb)
    return max(-1.0, min(1.0, r))


@dataclass(slots=True)
class RelatorioRedundancia:
    fontes: list[str] = field(default_factory=list)
    pesos: dict[str, float] = field(default_factory=dict)
    matriz: dict[str, dict[str, float | None]] = field(default_factory=dict)
    peso_total: float = 0.0
    peso_efetivo: float = 0.0
    n_observacoes: int = 0
    # Pares acima do limiar, do mais correlacionado para o menos.
    pares_redundantes: list[tuple[str, str, float]] = field(default_factory=list)
    grupos: list[list[str]] = field(default_factory=list)
    nao_medidos: list[tuple[str, str]] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def fator(self) -> float:
        """Quanto da evidência aparente é de fato independente, de 0 a 1."""
        if self.peso_total <= 0:
            return 1.0
        return max(0.0, min(1.0, self.peso_efetivo / self.peso_total))

    @property
    def material(self) -> bool:
        return self.fator < FATOR_ALERTA

    @property
    def mensurado(self) -> bool:
        return self.n_observacoes >= MIN_OBSERVACOES

    def to_dict(self) -> dict[str, Any]:
        return {
            "fontes": self.fontes,
            "pesos": {k: round(v, 4) for k, v in self.pesos.items()},
            "peso_total": round(self.peso_total, 4),
            "peso_efetivo": round(self.peso_efetivo, 4),
            "fator": round(self.fator, 4),
            "material": self.material,
            "mensurado": self.mensurado,
            "n_observacoes": self.n_observacoes,
            "pares_redundantes": [[a, b, round(r, 3)]
                                  for a, b, r in self.pares_redundantes],
            "grupos": self.grupos,
            "nao_medidos": [[a, b] for a, b in self.nao_medidos],
            "matriz": {a: {b: (round(v, 3) if v is not None else None)
                           for b, v in linha.items()}
                       for a, linha in self.matriz.items()},
            "avisos": self.avisos,
            "leitura": (
                "peso_efetivo é quanto de evidência INDEPENDENTE existe. "
                "Três fontes de peso 0,2 que sempre concordam valem 0,2, não "
                "0,6. O fator não é aplicado sozinho ao score: ele entra "
                "como contraindicação explícita, porque desconto silencioso "
                "produz número que ninguém reproduz na mão."),
        }


def _agrupar(fontes: list[str],
             pares: list[tuple[str, str, float]]) -> list[list[str]]:
    """Junta fontes ligadas por redundância (componentes conexas)."""
    pai = {f: f for f in fontes}

    def raiz(x: str) -> str:
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x

    for a, b, _ in pares:
        ra, rb = raiz(a), raiz(b)
        if ra != rb:
            pai[ra] = rb

    grupos: dict[str, list[str]] = {}
    for f in fontes:
        grupos.setdefault(raiz(f), []).append(f)
    return [sorted(g) for g in grupos.values() if len(g) > 1]


def avaliar_redundancia(series: Mapping[str, Sequence[float]],
                        pesos: Mapping[str, float] | None = None, *,
                        limiar: float = LIMIAR_REDUNDANCIA
                        ) -> RelatorioRedundancia:
    """Mede quanta evidência independente há em um conjunto de fontes.

    `series` mapeia o nome da fonte para o histórico de opiniões dela. As
    séries precisam estar alinhadas: a posição `k` de todas tem de se referir
    à mesma análise. Séries desalinhadas produziriam correlação entre coisas
    diferentes, e o número resultante pareceria válido.
    """
    nomes = sorted(series)
    rel = RelatorioRedundancia(fontes=nomes)
    if not nomes:
        return rel

    pesos_f = {n: float((pesos or {}).get(n, 1.0)) for n in nomes}
    rel.pesos = pesos_f
    rel.peso_total = sum(pesos_f.values())
    rel.n_observacoes = min(len(series[n]) for n in nomes)

    # Matriz de correlação. Onde não foi possível medir, assume-se 1,0 — a
    # suposição conservadora. Supor independência no escuro seria aceitar
    # como evidência nova aquilo que pode ser a mesma coisa repetida.
    matriz: dict[str, dict[str, float | None]] = {}
    r_efetivo: dict[tuple[str, str], float] = {}
    for a in nomes:
        matriz[a] = {}
        for b in nomes:
            if a == b:
                matriz[a][b] = 1.0
                r_efetivo[(a, b)] = 1.0
                continue
            if b in matriz and a in matriz[b]:
                matriz[a][b] = matriz[b][a]
                r_efetivo[(a, b)] = r_efetivo[(b, a)]
                continue
            r = correlacao(series[a], series[b])
            matriz[a][b] = r
            if r is None:
                rel.nao_medidos.append((a, b))
                r_efetivo[(a, b)] = 1.0
            else:
                # Correlação negativa não soma independência além do
                # independente: o que interessa é quanto de informação é
                # compartilhada, e isso é |r|.
                r_efetivo[(a, b)] = abs(r)
    rel.matriz = matriz

    # Peso efetivo pelo fator de inflação de variância.
    quadratica = sum(pesos_f[a] * pesos_f[b] * r_efetivo[(a, b)]
                     for a in nomes for b in nomes)
    soma_quadrados = sum(w * w for w in pesos_f.values())
    if soma_quadrados > 0 and quadratica > 0:
        inflacao = quadratica / soma_quadrados
        rel.peso_efetivo = rel.peso_total / max(1.0, inflacao)
    else:
        rel.peso_efetivo = rel.peso_total
    # Rede de segurança: como a matriz usa |r|, a inflação nunca é menor que
    # 1 e o efetivo nunca deveria passar o total. O clamp existe para o caso
    # de erro numérico, não para corrigir a fórmula.
    rel.peso_efetivo = min(rel.peso_efetivo, rel.peso_total)

    vistos: set[tuple[str, str]] = set()
    for a in nomes:
        for b in nomes:
            if a >= b or (a, b) in vistos:
                continue
            vistos.add((a, b))
            r = matriz[a][b]
            if r is not None and abs(r) >= limiar:
                rel.pares_redundantes.append((a, b, r))
    rel.pares_redundantes.sort(key=lambda p: abs(p[2]), reverse=True)
    rel.grupos = _agrupar(nomes, rel.pares_redundantes)

    # ------------------------------------------------------------- avisos
    if not rel.mensurado:
        rel.avisos.append(
            f"{rel.n_observacoes} observações, abaixo do mínimo de "
            f"{MIN_OBSERVACOES}: a redundância não pôde ser medida e todas as "
            f"fontes foram tratadas como se dissessem a mesma coisa, que é a "
            f"suposição conservadora")
    if rel.nao_medidos:
        rel.avisos.append(
            f"{len(rel.nao_medidos)} par(es) sem correlação mensurável "
            f"(série constante ou curta); tratados como redundantes")
    for grupo in rel.grupos:
        rel.avisos.append(
            f"{', '.join(grupo)} se movem juntos acima de {limiar:.0%}: "
            f"quando concordam, é uma evidência vista de "
            f"{len(grupo)} ângulos, não {len(grupo)} evidências")
    if rel.material and rel.mensurado:
        rel.avisos.append(
            f"apenas {rel.fator:.0%} do peso analítico é independente "
            f"({rel.peso_efetivo:.2f} de {rel.peso_total:.2f}): a confiança "
            f"sugerida pela quantidade de fontes está inflada")
    return rel


class HistoricoOpinioes:
    """Acumula as opiniões das fontes para que a correlação seja mensurável.

    Guarda só o necessário: nome da fonte, valor da opinião e o instante.
    Uma análise em que uma fonte se absteve NÃO entra para nenhuma fonte
    daquela rodada — séries desalinhadas correlacionariam coisas diferentes.
    """

    def __init__(self, maximo: int = 500):
        self.maximo = maximo
        self._rodadas: list[dict[str, float]] = []

    def registrar(self, opinioes: Mapping[str, float]) -> None:
        if not opinioes:
            return
        self._rodadas.append(dict(opinioes))
        if len(self._rodadas) > self.maximo:
            del self._rodadas[:len(self._rodadas) - self.maximo]

    def series(self, fontes: Sequence[str] | None = None
               ) -> dict[str, list[float]]:
        """Séries alinhadas, usando só rodadas em que TODAS opinaram."""
        nomes = list(fontes) if fontes else sorted(
            {k for r in self._rodadas for k in r})
        if not nomes:
            return {}
        completas = [r for r in self._rodadas
                     if all(n in r for n in nomes)]
        return {n: [r[n] for r in completas] for n in nomes}

    @property
    def n_rodadas(self) -> int:
        return len(self._rodadas)

    def avaliar(self, pesos: Mapping[str, float] | None = None, *,
                fontes: Sequence[str] | None = None
                ) -> RelatorioRedundancia:
        return avaliar_redundancia(self.series(fontes), pesos)


__all__ = [
    "FATOR_ALERTA", "HistoricoOpinioes", "LIMIAR_REDUNDANCIA",
    "MIN_OBSERVACOES", "RelatorioRedundancia", "avaliar_redundancia",
    "correlacao",
]
