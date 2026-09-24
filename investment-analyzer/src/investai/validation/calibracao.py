"""Calibração de probabilidade: a probabilidade dita vale o que promete?

O problema que este módulo resolve
----------------------------------
Um modelo que diz "70% de chance" e acerta 70% das vezes é calibrado. Um
modelo que diz "70%" e acerta 45% não está errado por pouco: ele está
mentindo com aparência de número, e todo dimensionamento feito em cima
daquele 0,70 está errado na mesma proporção.

Isso importa aqui mais do que em quase qualquer outra aplicação, porque a
probabilidade não é o produto final — ela entra no cálculo de expectativa,
no tamanho da posição e no critério de Kelly. Uma probabilidade inflada não
gera uma previsão ruim; gera uma posição grande demais.

O que se mede
-------------
* **curva de calibração** — agrupa as previsões em faixas e compara a média
  prevista com a frequência observada em cada faixa. É o gráfico que mostra
  onde o modelo mente, e em que direção;
* **Brier score** — erro quadrático médio da probabilidade. Sozinho diz
  pouco, porque mistura duas coisas diferentes, então vem decomposto;
* **decomposição de Murphy** — `Brier = confiabilidade − resolução +
  incerteza`. Separa "as probabilidades estão nos lugares certos"
  (confiabilidade) de "o modelo distingue os casos" (resolução). Um modelo
  que sempre diz a taxa-base é perfeitamente confiável e completamente
  inútil: confiabilidade zero, resolução zero. Sem a decomposição, o Brier
  dele parece bom;
* **ECE e MCE** — erro de calibração médio e máximo entre as faixas;
* **precisão por faixa, com intervalo de Wilson** — porque "nesta faixa o
  modelo acertou 8 de 10" não é evidência, e o intervalo diz isso.

Recalibração
------------
`recalibrar_isotonico` ajusta um mapa monotônico de probabilidade dita para
probabilidade observada, por regressão isotônica (algoritmo pool-adjacent
violators). É preferível a um ajuste logístico aqui porque não presume forma
nenhuma da distorção — e as distorções que aparecem em sinal de mercado
raramente são logísticas.

O ajuste é feito com os dados que ele recebe. Usá-lo para corrigir as mesmas
previsões que o treinaram produz calibração perfeita e falsa; por isso
`ModeloCalibrado` guarda em que amostra foi ajustado e `avaliar_calibracao`
tem de rodar em dados que não participaram do ajuste.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from .stats import IntervaloConfianca, wilson

# Amostra mínima para uma faixa dizer qualquer coisa. Abaixo disso a faixa
# entra no relatório marcada como não conclusiva, em vez de ficar de fora:
# esconder faixa pequena daria a impressão de que o modelo só opera onde
# tem dados.
MIN_POR_FAIXA = 10

# Amostra mínima total para o veredicto de calibração valer algo.
MIN_AMOSTRA = 100

# Limiares do veredicto. ECE acima disso significa que a probabilidade não
# pode ser usada para dimensionar posição.
ECE_ACEITAVEL = 0.05
ECE_RUIM = 0.10

# Ganho mínimo sobre a taxa-base para o modelo contar como informativo.
#
# Não é zero de propósito. Um modelo que sempre diz a taxa-base tem skill
# matematicamente igual a zero, mas em ponto flutuante sai como 5e-15 — e
# `skill > 0` leria esse resíduo como poder preditivo, aprovando o modelo
# mais inútil possível. O limiar também descarta ganho tão pequeno que
# não sobreviveria a outra amostra.
SKILL_MINIMO = 0.01


@dataclass(slots=True)
class Faixa:
    """Uma faixa de confiança e o que de fato aconteceu nela."""

    inferior: float
    superior: float
    n: int
    prevista_media: float
    observada: float
    ic: IntervaloConfianca

    @property
    def erro(self) -> float:
        """Quanto a probabilidade dita se afasta da observada."""
        return self.observada - self.prevista_media

    @property
    def conclusiva(self) -> bool:
        return self.n >= MIN_POR_FAIXA

    @property
    def coerente(self) -> bool:
        """A probabilidade dita cabe no intervalo do que foi observado?

        Este é o teste honesto por faixa: com amostra pequena, um erro de 15
        pontos pode ser ruído. O intervalo de Wilson responde se é.
        """
        if self.n == 0:
            return True
        return self.ic.inferior <= self.prevista_media <= self.ic.superior

    def to_dict(self) -> dict[str, Any]:
        return {
            "inferior": round(self.inferior, 4),
            "superior": round(self.superior, 4),
            "n": self.n,
            "prevista_media": round(self.prevista_media, 4),
            "observada": round(self.observada, 4),
            "erro": round(self.erro, 4),
            "conclusiva": self.conclusiva,
            "coerente": self.coerente,
            "ic_observada": self.ic.to_dict(),
        }


@dataclass(slots=True)
class Brier:
    """Brier score com a decomposição que o torna interpretável."""

    score: float
    confiabilidade: float          # menor é melhor (0 = perfeita)
    resolucao: float               # maior é melhor
    incerteza: float               # propriedade dos dados, não do modelo
    n: int
    # Brier de um modelo que sempre diz a taxa-base. É a referência honesta:
    # um modelo com Brier pior que este não está agregando nada.
    score_referencia: float = 0.0
    # Diferença entre o Brier medido e a soma da decomposição.
    #
    # A identidade `Brier = confiabilidade - resolução + incerteza` é exata
    # para a previsão AGRUPADA, não para a original: dentro de cada faixa,
    # a decomposição usa a média da faixa. Com previsão contínua sobra um
    # resíduo de agrupamento, que encolhe com mais faixas. Ele fica exposto
    # aqui em vez de escondido — um resíduo grande significa que as faixas
    # estão largas demais para o formato desta previsão.
    residuo_binagem: float = 0.0

    @property
    def skill(self) -> float:
        """Ganho sobre o modelo que só diz a taxa-base.

        1 = perfeito, 0 = equivalente a não modelar nada, negativo = pior que
        não modelar nada. O corte para "informativo" é `SKILL_MINIMO`, não
        zero — ver o comentário daquela constante.
        """
        if self.incerteza <= 0:
            return 0.0
        return 1.0 - self.score / self.incerteza

    @property
    def melhor_que_taxa_base(self) -> bool:
        return self.skill > SKILL_MINIMO

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 6),
            "confiabilidade": round(self.confiabilidade, 6),
            "resolucao": round(self.resolucao, 6),
            "incerteza": round(self.incerteza, 6),
            "score_referencia": round(self.score_referencia, 6),
            "residuo_binagem": round(self.residuo_binagem, 6),
            "skill": round(self.skill, 4),
            "melhor_que_taxa_base": self.melhor_que_taxa_base,
            "n": self.n,
            "leitura": (
                "Brier = confiabilidade - resolução + incerteza, a menos do "
                "resíduo de agrupamento. "
                "Confiabilidade perto de zero significa que as probabilidades "
                "estão nos lugares certos; resolução alta significa que o "
                "modelo distingue os casos. Um modelo que sempre diz a "
                "taxa-base tem confiabilidade zero e resolução zero: "
                "perfeitamente honesto e completamente inútil."),
        }


@dataclass(slots=True)
class RelatorioCalibracao:
    faixas: list[Faixa] = field(default_factory=list)
    brier: Brier | None = None
    ece: float = 0.0               # erro de calibração esperado (ponderado)
    mce: float = 0.0               # erro máximo entre faixas conclusivas
    n: int = 0
    taxa_base: float = 0.0
    prevista_media: float = 0.0
    # Viés global: positivo = o modelo subestima; negativo = superestima.
    # Superestimar é o caso perigoso: infla o tamanho da posição.
    vies: float = 0.0
    avisos: list[str] = field(default_factory=list)

    @property
    def amostra_suficiente(self) -> bool:
        return self.n >= MIN_AMOSTRA

    @property
    def calibrado(self) -> bool:
        """Veredicto: esta probabilidade pode dimensionar posição?

        Exige amostra, ECE dentro do aceitável e ganho sobre a taxa-base.
        Faltar qualquer um dos três reprova — ausência de medição nunca é
        aprovação.
        """
        if not self.amostra_suficiente:
            return False
        if self.ece > ECE_ACEITAVEL:
            return False
        if self.brier is not None and not self.brier.melhor_que_taxa_base:
            return False
        return True

    @property
    def veredicto(self) -> str:
        if not self.amostra_suficiente:
            return "SEM_AMOSTRA"
        if self.ece > ECE_RUIM:
            return "DESCALIBRADO"
        if self.ece > ECE_ACEITAVEL:
            return "CALIBRACAO_FRACA"
        if self.brier is not None and not self.brier.melhor_que_taxa_base:
            return "SEM_PODER_DISCRIMINANTE"
        return "CALIBRADO"

    def to_dict(self) -> dict[str, Any]:
        return {
            "veredicto": self.veredicto,
            "calibrado": self.calibrado,
            "amostra_suficiente": self.amostra_suficiente,
            "n": self.n,
            "ece": round(self.ece, 4),
            "mce": round(self.mce, 4),
            "taxa_base": round(self.taxa_base, 4),
            "prevista_media": round(self.prevista_media, 4),
            "vies": round(self.vies, 4),
            "direcao_vies": ("superestima" if self.vies < -0.005 else
                             "subestima" if self.vies > 0.005 else "neutro"),
            "faixas": [f.to_dict() for f in self.faixas],
            "brier": self.brier.to_dict() if self.brier else None,
            "avisos": self.avisos,
        }


def _validar(previstas: Sequence[float],
             ocorreu: Sequence[bool | int]) -> tuple[list[float], list[int]]:
    if len(previstas) != len(ocorreu):
        raise ValueError(
            f"previstas ({len(previstas)}) e ocorreu ({len(ocorreu)}) têm "
            f"tamanhos diferentes; alinhar previsão com resultado é "
            f"responsabilidade de quem chama")
    p: list[float] = []
    y: list[int] = []
    for prev, res in zip(previstas, ocorreu):
        valor = float(prev)
        if not 0.0 <= valor <= 1.0:
            raise ValueError(
                f"probabilidade fora de [0,1]: {valor}. Um valor como 70 em "
                f"vez de 0,70 produziria calibração silenciosamente absurda")
        p.append(valor)
        y.append(1 if res else 0)
    return p, y


def calcular_brier(previstas: Sequence[float],
                   ocorreu: Sequence[bool | int], *,
                   n_faixas: int = 10) -> Brier:
    """Brier score com decomposição de Murphy.

    A decomposição usa as mesmas faixas da curva de calibração, porque é
    assim que ela é definida: confiabilidade e resolução são somas
    ponderadas sobre grupos de previsões parecidas.
    """
    p, y = _validar(previstas, ocorreu)
    n = len(p)
    if n == 0:
        return Brier(0.0, 0.0, 0.0, 0.0, 0)

    score = sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / n
    base = sum(y) / n
    incerteza = base * (1.0 - base)
    score_referencia = sum((base - yi) ** 2 for yi in y) / n

    grupos = _agrupar(p, y, n_faixas)
    confiabilidade = 0.0
    resolucao = 0.0
    for _, _, pk, yk in grupos:
        peso = len(pk) / n
        media_prev = sum(pk) / len(pk)
        media_obs = sum(yk) / len(yk)
        confiabilidade += peso * (media_prev - media_obs) ** 2
        resolucao += peso * (media_obs - base) ** 2

    return Brier(score=score, confiabilidade=confiabilidade,
                 resolucao=resolucao, incerteza=incerteza, n=n,
                 score_referencia=score_referencia,
                 residuo_binagem=score
                 - (confiabilidade - resolucao + incerteza))


def _agrupar(p: list[float], y: list[int], n_faixas: int
             ) -> list[tuple[float, float, list[float], list[int]]]:
    """Divide as previsões em faixas de largura fixa em [0,1].

    Largura fixa, e não quantis, de propósito: o que interessa é "quando o
    modelo diz 70%, o que acontece?". Faixas por quantil mudariam de posição
    conforme a distribuição das previsões, e a pergunta deixaria de ser
    comparável entre execuções.
    """
    if n_faixas < 2:
        raise ValueError("n_faixas deve ser >= 2")
    baldes: list[tuple[float, float, list[float], list[int]]] = []
    largura = 1.0 / n_faixas
    for i in range(n_faixas):
        lo = i * largura
        hi = (i + 1) * largura
        pk: list[float] = []
        yk: list[int] = []
        for pi, yi in zip(p, y):
            # Última faixa é fechada à direita para que 1,0 caia em algum
            # lugar em vez de ser descartado.
            dentro = (lo <= pi < hi) if i < n_faixas - 1 else (lo <= pi <= hi)
            if dentro:
                pk.append(pi)
                yk.append(yi)
        if pk:
            baldes.append((lo, hi, pk, yk))
    return baldes


def avaliar_calibracao(previstas: Sequence[float],
                       ocorreu: Sequence[bool | int], *,
                       n_faixas: int = 10,
                       confianca: float = 0.95) -> RelatorioCalibracao:
    """Mede se a probabilidade dita corresponde ao que aconteceu."""
    p, y = _validar(previstas, ocorreu)
    n = len(p)
    rel = RelatorioCalibracao(n=n)
    if n == 0:
        rel.avisos.append("nenhuma previsão: nada a calibrar")
        return rel

    rel.taxa_base = sum(y) / n
    rel.prevista_media = sum(p) / n
    rel.vies = rel.taxa_base - rel.prevista_media
    rel.brier = calcular_brier(p, y, n_faixas=n_faixas)

    soma_ece = 0.0
    for lo, hi, pk, yk in _agrupar(p, y, n_faixas):
        acertos = sum(yk)
        faixa = Faixa(
            inferior=lo, superior=hi, n=len(pk),
            prevista_media=sum(pk) / len(pk),
            observada=acertos / len(yk),
            ic=wilson(acertos, len(yk), confianca))
        rel.faixas.append(faixa)
        soma_ece += (len(pk) / n) * abs(faixa.erro)

    rel.ece = soma_ece
    conclusivas = [f for f in rel.faixas if f.conclusiva]
    rel.mce = max((abs(f.erro) for f in conclusivas), default=0.0)

    # ------------------------------------------------------------- avisos
    if not rel.amostra_suficiente:
        rel.avisos.append(
            f"{n} observações, abaixo do mínimo de {MIN_AMOSTRA}: os números "
            f"acima descrevem esta amostra, não a qualidade do modelo")
    if rel.vies < -ECE_ACEITAVEL:
        rel.avisos.append(
            f"o modelo SUPERESTIMA em {abs(rel.vies):.1%} na média. É o erro "
            f"perigoso: probabilidade inflada não produz previsão ruim, "
            f"produz posição grande demais")
    elif rel.vies > ECE_ACEITAVEL:
        rel.avisos.append(
            f"o modelo subestima em {rel.vies:.1%} na média; o risco é deixar "
            f"oportunidade na mesa, não perder dinheiro")
    if rel.ece > ECE_RUIM:
        rel.avisos.append(
            f"erro de calibração de {rel.ece:.1%}: esta probabilidade NÃO "
            f"deve ser usada para dimensionar posição nem para calcular "
            f"expectativa")
    if rel.brier is not None and not rel.brier.melhor_que_taxa_base:
        rel.avisos.append(
            "o modelo não é melhor que simplesmente dizer a taxa-base "
            "histórica; a probabilidade não carrega informação")
    incoerentes = [f for f in rel.faixas if f.conclusiva and not f.coerente]
    if incoerentes:
        pior = max(incoerentes, key=lambda f: abs(f.erro))
        rel.avisos.append(
            f"{len(incoerentes)} faixa(s) com desvio além do intervalo de "
            f"confiança; a pior é [{pior.inferior:.1f}–{pior.superior:.1f}], "
            f"onde o modelo disse {pior.prevista_media:.0%} e aconteceu "
            f"{pior.observada:.0%} em {pior.n} casos")
    if not conclusivas:
        rel.avisos.append(
            f"nenhuma faixa atingiu {MIN_POR_FAIXA} observações; o erro "
            f"máximo não pôde ser medido")
    return rel


# --------------------------------------------------------------- recalibração
@dataclass(slots=True)
class ModeloCalibrado:
    """Mapa monotônico de probabilidade dita para probabilidade observada.

    Guarda em que amostra foi ajustado de propósito. Aplicar este mapa às
    mesmas previsões que o treinaram produz calibração perfeita e falsa, e
    quem lê o relatório precisa poder notar isso.
    """

    pontos: list[tuple[float, float]] = field(default_factory=list)
    n_ajuste: int = 0
    ece_antes: float = 0.0
    ece_depois: float = 0.0

    def aplicar(self, p: float) -> float:
        """Interpola linearmente entre os pontos do mapa."""
        if not self.pontos:
            return float(p)
        p = max(0.0, min(1.0, float(p)))
        if p <= self.pontos[0][0]:
            return self.pontos[0][1]
        if p >= self.pontos[-1][0]:
            return self.pontos[-1][1]
        for (x0, y0), (x1, y1) in zip(self.pontos, self.pontos[1:]):
            if x0 <= p <= x1:
                if x1 == x0:
                    return y1
                t = (p - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return self.pontos[-1][1]

    @property
    def melhorou(self) -> bool:
        return self.ece_depois < self.ece_antes

    def to_dict(self) -> dict[str, Any]:
        return {
            "pontos": [[round(x, 4), round(y, 4)] for x, y in self.pontos],
            "n_ajuste": self.n_ajuste,
            "ece_antes": round(self.ece_antes, 4),
            "ece_depois": round(self.ece_depois, 4),
            "melhorou": self.melhorou,
            "observacao": (
                "Este mapa foi ajustado nos dados informados. O ECE 'depois' "
                "é medido na MESMA amostra e por isso é otimista: para saber "
                "se a recalibração vale, aplique o mapa em dados que não "
                "participaram do ajuste."),
        }


def recalibrar_isotonico(previstas: Sequence[float],
                         ocorreu: Sequence[bool | int], *,
                         n_faixas: int = 10) -> ModeloCalibrado:
    """Regressão isotônica por pool-adjacent-violators.

    Escolhida em vez de um ajuste logístico porque não presume forma nenhuma
    da distorção — só que ela é monotônica. As distorções que aparecem em
    sinal de mercado raramente são logísticas.
    """
    p, y = _validar(previstas, ocorreu)
    if not p:
        return ModeloCalibrado()

    antes = avaliar_calibracao(p, y, n_faixas=n_faixas).ece

    # Ordena por probabilidade prevista e agrupa empates: previsões iguais
    # têm de receber o mesmo valor calibrado.
    #
    # Cada bloco guarda a SOMA dos x além do peso, para que o x
    # representativo de um bloco fundido seja a média ponderada dos x que
    # entraram nele. Guardar só o x da esquerda colocaria o degrau do mapa
    # numa posição arbitrária, deslocando toda a interpolação vizinha.
    pares = sorted(zip(p, y))
    soma_x: list[float] = []
    pesos: list[float] = []
    valores: list[float] = []
    for xi, yi in pares:
        mesmo_x = (pesos and math.isclose(soma_x[-1] / pesos[-1], xi,
                                          rel_tol=0.0, abs_tol=1e-12))
        if mesmo_x:
            total = pesos[-1] + 1
            valores[-1] = (valores[-1] * pesos[-1] + yi) / total
            soma_x[-1] += xi
            pesos[-1] = total
        else:
            soma_x.append(xi)
            pesos.append(1.0)
            valores.append(float(yi))

    # Pool adjacent violators: enquanto houver par fora de ordem, funde.
    i = 0
    while i < len(valores) - 1:
        if valores[i] > valores[i + 1] + 1e-15:
            peso = pesos[i] + pesos[i + 1]
            valores[i] = (valores[i] * pesos[i]
                          + valores[i + 1] * pesos[i + 1]) / peso
            soma_x[i] += soma_x[i + 1]
            pesos[i] = peso
            del valores[i + 1], pesos[i + 1], soma_x[i + 1]
            if i > 0:
                i -= 1          # a fusão pode ter criado violação atrás
        else:
            i += 1

    xs = [sx / w for sx, w in zip(soma_x, pesos)]
    modelo = ModeloCalibrado(pontos=list(zip(xs, valores)), n_ajuste=len(p),
                             ece_antes=antes)
    modelo.ece_depois = avaliar_calibracao(
        [modelo.aplicar(pi) for pi in p], y, n_faixas=n_faixas).ece
    return modelo


__all__ = [
    "Brier", "ECE_ACEITAVEL", "ECE_RUIM", "Faixa", "MIN_AMOSTRA",
    "MIN_POR_FAIXA", "ModeloCalibrado", "RelatorioCalibracao",
    "avaliar_calibracao", "calcular_brier", "recalibrar_isotonico",
]
