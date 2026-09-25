"""Modelo de probabilidade: regressão logística com calibração obrigatória.

Por que regressão logística
---------------------------
Não por ser a melhor: por ser a que se consegue auditar. Com treze features
e algumas milhares de observações, um modelo com muitos graus de liberdade
encontra estrutura em ruído com facilidade, e em cima disso o sistema
dimensiona posição. Aqui os coeficientes são legíveis, o número de
parâmetros é conhecido, e a regularização é explícita.

Não há dependência de biblioteca de machine learning. O treino é gradiente
descendente com regularização L2, escrito aqui, porque uma dependência
pesada para treze coeficientes acrescentaria risco de versão sem
acrescentar capacidade.

A regra que este módulo impõe
-----------------------------
**Um modelo sem calibração medida fora da amostra não informa probabilidade.**

`ModeloProbabilidade.prever` funciona sempre — é a saída crua do logístico.
`prever_calibrado` só devolve número quando existe um relatório de
calibração aprovado em dados que não participaram do treino. Quem consome
probabilidade para dimensionar posição chama o segundo.

A diferença não é estilística. A saída crua de um logístico treinado em
amostra desbalanceada costuma errar por vários pontos percentuais de forma
sistemática; usá-la em Kelly ou em expectativa propaga o erro direto para o
tamanho da posição.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..validation.calibracao import (
    ModeloCalibrado, RelatorioCalibracao, avaliar_calibracao,
    recalibrar_isotonico,
)
from .amostras import FEATURES, ConjuntoAmostras

# Amostra mínima para treinar. Abaixo disso o treino é recusado em vez de
# produzir um modelo que ninguém saberia que não vale nada.
MIN_TREINO = 200

# Observações mínimas por parâmetro. Treze features com 200 observações dão
# 15 por coeficiente, que é pouco mas auditável; abaixo de 10 o modelo
# decora.
MIN_OBS_POR_PARAMETRO = 10


def sigmoide(z: float) -> float:
    """Logística numericamente estável nos extremos.

    `1/(1+exp(-z))` estoura para z muito negativo. A forma abaixo não.
    """
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-min(z, 60.0)))
    e = math.exp(max(z, -60.0))
    return e / (1.0 + e)


@dataclass(slots=True)
class Padronizador:
    """Média e desvio de cada feature, medidos SÓ no treino.

    Padronizar com estatísticas do conjunto completo é vazamento: a média do
    teste entraria no treino. O erro é silencioso e melhora as métricas, que
    é a pior combinação possível.
    """

    medias: list[float] = field(default_factory=list)
    desvios: list[float] = field(default_factory=list)

    @classmethod
    def ajustar(cls, xs: Sequence[Sequence[float]]) -> "Padronizador":
        if not xs:
            return cls()
        d = len(xs[0])
        n = len(xs)
        medias = [sum(x[j] for x in xs) / n for j in range(d)]
        desvios = []
        for j in range(d):
            var = sum((x[j] - medias[j]) ** 2 for x in xs) / max(1, n - 1)
            # Desvio zero significa feature constante no treino: dividir por
            # ele daria infinito, então a feature é neutralizada.
            desvios.append(math.sqrt(var) if var > 1e-12 else 1.0)
        return cls(medias, desvios)

    def aplicar(self, x: Sequence[float]) -> list[float]:
        if not self.medias:
            return list(x)
        return [(x[j] - self.medias[j]) / self.desvios[j]
                for j in range(len(self.medias))]

    def to_dict(self) -> dict[str, Any]:
        return {"medias": [round(v, 6) for v in self.medias],
                "desvios": [round(v, 6) for v in self.desvios]}


@dataclass(slots=True)
class Hiperparametros:
    taxa_aprendizado: float = 0.1
    epocas: int = 400
    l2: float = 0.01
    semente: int = 17

    def to_dict(self) -> dict[str, Any]:
        return {"taxa_aprendizado": self.taxa_aprendizado,
                "epocas": self.epocas, "l2": self.l2,
                "semente": self.semente}


@dataclass(slots=True)
class ModeloProbabilidade:
    """Logístico treinado, com a procedência toda registrada."""

    coeficientes: list[float] = field(default_factory=list)
    intercepto: float = 0.0
    padronizador: Padronizador = field(default_factory=Padronizador)
    features: tuple[str, ...] = FEATURES
    hiper: Hiperparametros = field(default_factory=Hiperparametros)

    # Procedência — o que torna o número auditável meses depois.
    n_treino: int = 0
    taxa_base_treino: float = 0.0
    ts_treino_inicio: int = 0
    ts_treino_fim: int = 0
    symbol: str = ""
    timeframe: str = ""
    side: str = ""

    # Avaliação fora da amostra.
    calibracao: RelatorioCalibracao | None = None
    mapa_calibracao: ModeloCalibrado | None = None
    n_validacao: int = 0
    ts_validacao_inicio: int = 0
    ts_validacao_fim: int = 0

    @property
    def treinado(self) -> bool:
        return bool(self.coeficientes)

    @property
    def calibrado(self) -> bool:
        """Só é True com calibração medida fora da amostra e aprovada."""
        return (self.treinado and self.calibracao is not None
                and self.calibracao.calibrado)

    @property
    def motivo_nao_calibrado(self) -> str:
        if not self.treinado:
            return "modelo não treinado"
        if self.calibracao is None:
            return ("modelo treinado mas sem calibração medida fora da "
                    "amostra: a probabilidade crua não pode dimensionar "
                    "posição")
        if not self.calibracao.calibrado:
            return (f"calibração reprovada ({self.calibracao.veredicto}): "
                    + "; ".join(self.calibracao.avisos[:2]))
        return ""

    # --------------------------------------------------------- predição
    def _z(self, x: Sequence[float]) -> float:
        if len(x) != len(self.coeficientes):
            raise ValueError(
                f"vetor com {len(x)} features contra modelo de "
                f"{len(self.coeficientes)}: treinar com uma ordem de features "
                f"e prever com outra produz número sem sentido, e nada no "
                f"formato do vetor denunciaria isso")
        xs = self.padronizador.aplicar(x)
        return self.intercepto + sum(c * v for c, v in zip(self.coeficientes, xs))

    def prever(self, x: Sequence[float]) -> float:
        """Saída CRUA do logístico. Não use para dimensionar posição."""
        if not self.treinado:
            raise ValueError("modelo não treinado")
        return sigmoide(self._z(x))

    def prever_calibrado(self, x: Sequence[float]) -> float | None:
        """Probabilidade calibrada, ou None quando não há calibração válida.

        Devolver None em vez de cair de volta na saída crua é deliberado:
        um fallback silencioso faria o sistema usar probabilidade não
        calibrada sem que nada na tela indicasse isso.
        """
        if not self.calibrado:
            return None
        bruta = self.prever(x)
        if self.mapa_calibracao is not None and self.mapa_calibracao.pontos:
            return self.mapa_calibracao.aplicar(bruta)
        return bruta

    def importancias(self) -> list[tuple[str, float]]:
        """Coeficientes em escala padronizada, do maior para o menor.

        Comparáveis entre si porque as features foram padronizadas. Não são
        causalidade — são o peso que o ajuste deu a cada uma.
        """
        pares = list(zip(self.features, self.coeficientes))
        return sorted(pares, key=lambda p: abs(p[1]), reverse=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "treinado": self.treinado,
            "calibrado": self.calibrado,
            "motivo_nao_calibrado": self.motivo_nao_calibrado,
            "symbol": self.symbol, "timeframe": self.timeframe,
            "side": self.side,
            "features": list(self.features),
            "coeficientes": [round(c, 6) for c in self.coeficientes],
            "intercepto": round(self.intercepto, 6),
            "importancias": [[n, round(v, 4)] for n, v in self.importancias()],
            "hiperparametros": self.hiper.to_dict(),
            "n_treino": self.n_treino,
            "taxa_base_treino": round(self.taxa_base_treino, 4),
            "periodo_treino": [self.ts_treino_inicio, self.ts_treino_fim],
            "n_validacao": self.n_validacao,
            "periodo_validacao": [self.ts_validacao_inicio,
                                  self.ts_validacao_fim],
            "calibracao": (self.calibracao.to_dict()
                           if self.calibracao else None),
            "mapa_calibracao": (self.mapa_calibracao.to_dict()
                                if self.mapa_calibracao else None),
        }


class TreinoError(RuntimeError):
    pass


def treinar(conj: ConjuntoAmostras, *,
            hiper: Hiperparametros | None = None,
            symbol: str = "", timeframe: str = "",
            side: str = "") -> ModeloProbabilidade:
    """Treina o logístico. Recusa amostra pequena em vez de fingir.

    O gradiente é o do log-loss com penalidade L2 no vetor de coeficientes.
    O intercepto NÃO é penalizado: penalizá-lo empurraria a probabilidade
    média para 0,5, desviando o modelo da taxa-base real.
    """
    hiper = hiper or Hiperparametros()
    xs, ys = conj.xs, conj.ys
    n = len(xs)
    if n < MIN_TREINO:
        raise TreinoError(
            f"{n} amostras contra o mínimo de {MIN_TREINO}: treinar aqui "
            f"produziria um modelo que ninguém saberia que não vale nada")
    d = len(xs[0])
    if n < d * MIN_OBS_POR_PARAMETRO:
        raise TreinoError(
            f"{n} amostras para {d} parâmetros ({n / d:.1f} por parâmetro, "
            f"mínimo {MIN_OBS_POR_PARAMETRO}): com essa folga o modelo decora "
            f"em vez de generalizar")
    if len(set(ys)) < 2:
        raise TreinoError(
            "todas as amostras têm o mesmo rótulo; não há o que aprender")

    pad = Padronizador.ajustar(xs)
    xz = [pad.aplicar(x) for x in xs]

    rng = random.Random(hiper.semente)
    coef = [rng.uniform(-0.01, 0.01) for _ in range(d)]
    # O intercepto começa no log-odds da taxa-base: assim o modelo já parte
    # da frequência correta e o treino só precisa aprender o desvio dela.
    base = max(1e-6, min(1 - 1e-6, sum(ys) / n))
    b = math.log(base / (1 - base))

    for _ in range(hiper.epocas):
        grad = [0.0] * d
        grad_b = 0.0
        for x, y in zip(xz, ys):
            p = sigmoide(b + sum(c * v for c, v in zip(coef, x)))
            erro = p - y
            grad_b += erro
            for j in range(d):
                grad[j] += erro * x[j]
        for j in range(d):
            # L2 só nos coeficientes, nunca no intercepto.
            coef[j] -= hiper.taxa_aprendizado * (grad[j] / n
                                                 + hiper.l2 * coef[j])
        b -= hiper.taxa_aprendizado * (grad_b / n)

    ini, fim = conj.intervalo_ts
    return ModeloProbabilidade(
        coeficientes=coef, intercepto=b, padronizador=pad,
        features=conj.features, hiper=hiper, n_treino=n,
        taxa_base_treino=conj.taxa_base, ts_treino_inicio=ini,
        ts_treino_fim=fim, symbol=symbol, timeframe=timeframe, side=side)


def validar(modelo: ModeloProbabilidade, conj: ConjuntoAmostras, *,
            recalibrar: bool = True,
            n_faixas: int = 10) -> ModeloProbabilidade:
    """Mede a calibração FORA da amostra e anexa o resultado ao modelo.

    `conj` tem de ser um conjunto que não participou do treino. A função
    confere isso pelos instantes: sobreposição temporal entre treino e
    validação é recusada, porque o número resultante pareceria válido.
    """
    if not modelo.treinado:
        raise TreinoError("modelo não treinado")
    if not conj.amostras:
        raise TreinoError("conjunto de validação vazio")

    ini, fim = conj.intervalo_ts
    if ini <= modelo.ts_treino_fim:
        raise TreinoError(
            f"a validação começa em {ini}, dentro do período de treino que "
            f"termina em {modelo.ts_treino_fim}. Medir calibração em dados "
            f"que o modelo viu produz número otimista e indistinguível de um "
            f"número válido")

    brutas = [modelo.prever(a.x) for a in conj.amostras]
    ys = conj.ys

    mapa = recalibrar_isotonico(brutas, ys, n_faixas=n_faixas) if recalibrar \
        else None
    if mapa is not None and mapa.pontos:
        previstas = [mapa.aplicar(p) for p in brutas]
    else:
        previstas = brutas

    rel = avaliar_calibracao(previstas, ys, n_faixas=n_faixas)
    modelo.calibracao = rel
    modelo.mapa_calibracao = mapa
    modelo.n_validacao = len(conj.amostras)
    modelo.ts_validacao_inicio = ini
    modelo.ts_validacao_fim = fim
    if mapa is not None and mapa.pontos:
        rel.avisos.append(
            "o mapa de recalibração foi ajustado nestes mesmos dados de "
            "validação; o ECE acima é otimista. Para número limpo, use uma "
            "terceira janela")
    return modelo


__all__ = [
    "Hiperparametros", "MIN_OBS_POR_PARAMETRO", "MIN_TREINO",
    "ModeloProbabilidade", "Padronizador", "TreinoError", "sigmoide",
    "treinar", "validar",
]
