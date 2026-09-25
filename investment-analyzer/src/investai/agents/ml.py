"""Agente de aprendizado de máquina: P(alvo antes do stop).

O que ele acrescenta
--------------------
Os outros agentes olham um fator cada: tendência, derivativos, liquidez,
estatística histórica. Este olha a combinação das features e responde uma
pergunta diferente — dada esta configuração exata de mercado, qual a chance
de o alvo vir antes do stop?

Quando ele se cala
------------------
Sempre que não puder responder com probabilidade calibrada. Isso inclui:

* não há modelo treinado para este par/timeframe/direção;
* há modelo, mas a calibração dele foi reprovada fora da amostra;
* as features da barra atual não puderam ser calculadas.

Nos três casos o parecer é `SEM_DADOS`, que fica FORA do cálculo de consenso
em vez de entrar como "neutro". A diferença importa: um agente de peso 0,18
entrando como neutro puxa o score para o meio e faz o sistema parecer ter
consultado o modelo quando não consultou.

Por que não usar a saída crua quando falta calibração
-----------------------------------------------------
Porque ela seria usada. A probabilidade deste agente entra na expectativa e
no dimensionamento; um logístico descalibrado erra de forma sistemática, e o
erro vira tamanho de posição errado — não previsão errada. Preferir silêncio
a número não confiável é a única escolha coerente com o resto do sistema.
"""
from __future__ import annotations

from typing import Any, Callable

from ..data import AssetClass
from ..ml.amostras import vetor_features
from ..ml.modelo import ModeloProbabilidade
from .base import AgenteBase, ContextoAnalise, ParecerAgente

TODAS = (AssetClass.CRIPTO, AssetClass.ACAO, AssetClass.FII, AssetClass.ETF,
         AssetClass.RENDA_FIXA)

# Probabilidade de referência: abaixo dela o modelo está dizendo que o trade
# é ruim, acima que é bom. Não é 0,50 porque um plano com RR 2 não precisa
# de 50% para valer: precisa de mais que 1/(1+RR).
def limiar_neutro(rr: float) -> float:
    """Probabilidade de equilíbrio para um dado risco-retorno.

    Com RR 2, acertar 33,3% empata (ignorando custos). Comparar a
    probabilidade do modelo com 0,50 trataria um plano perfeitamente
    razoável como ruim.
    """
    if rr <= 0:
        return 0.5
    return 1.0 / (1.0 + rr)


class AgenteML(AgenteBase):
    """Oitavo agente: probabilidade condicionada às features da barra."""

    nome = "ml"
    peso = 0.18
    classes_suportadas = TODAS

    def __init__(self, buscar_modelo:
                 Callable[[str, str, str], ModeloProbabilidade | None] | None = None,
                 *, rr: float = 2.0):
        # O agente não treina nem guarda modelo: ele consulta. Assim o
        # treino fica em um lugar só, e trocar o modelo em produção não
        # exige tocar no agente.
        self._buscar = buscar_modelo
        # Risco-retorno do plano que o gerador de sinais monta. Precisa ser
        # o MESMO usado para rotular o treino, senão a probabilidade
        # aprendida se refere a um trade com outra geometria e a comparação
        # com o limiar de equilíbrio não significa nada.
        self.rr = rr

    def _modelo(self, ctx: ContextoAnalise,
                direcao: int) -> ModeloProbabilidade | None:
        if self._buscar is None:
            return None
        lado = "long" if direcao >= 0 else "short"
        try:
            return self._buscar(ctx.symbol, ctx.timeframe, lado)
        except Exception:                               # noqa: BLE001
            return None

    def analisar(self, ctx: ContextoAnalise, direcao: int) -> ParecerAgente:
        modelo = self._modelo(ctx, direcao)
        lado = "long" if direcao >= 0 else "short"

        if modelo is None:
            return self._sem_dados(
                ["modelo_probabilidade"],
                f"nenhum modelo treinado para {ctx.symbol} {ctx.timeframe} "
                f"{lado}: o agente se abstém em vez de opinar sem base")

        if not modelo.calibrado:
            return self._sem_dados(
                ["calibracao_do_modelo"],
                f"modelo existe mas não tem calibração válida "
                f"({modelo.motivo_nao_calibrado}). Probabilidade não "
                f"calibrada vira tamanho de posição errado, então o agente "
                f"se cala")

        # `ctx.features` é um dicionário indexado por timeframe.
        f = (ctx.features or {}).get(ctx.timeframe)
        if f is None:
            return self._sem_dados(
                ["features"],
                f"features de {ctx.symbol} {ctx.timeframe} indisponíveis "
                f"nesta barra")

        try:
            x = vetor_features(f)
            p = modelo.prever_calibrado(x)
        except Exception as exc:                        # noqa: BLE001
            return self._sem_dados(
                ["features"],
                f"não foi possível montar o vetor de features: {exc}")

        if p is None:
            return self._sem_dados(
                ["calibracao_do_modelo"],
                "o modelo recusou devolver probabilidade calibrada")

        rr = self.rr
        equilibrio = limiar_neutro(rr)

        # O valor vai de -1 a +1 conforme a probabilidade se afasta do
        # equilíbrio, saturando quando ela é o dobro (ou metade) dele.
        if p >= equilibrio:
            valor = min(1.0, (p - equilibrio) / max(1e-9, equilibrio))
        else:
            valor = max(-1.0, (p - equilibrio) / max(1e-9, equilibrio))

        cal = modelo.calibracao
        ev = [
            f"probabilidade calibrada de alvo antes do stop: {p:.1%}",
            f"equilíbrio para RR {rr:.1f} é {equilibrio:.1%}",
            f"calibração medida em {modelo.n_validacao} casos fora da "
            f"amostra: ECE {cal.ece:.1%}, skill {cal.brier.skill:.2f}",
        ]
        contra: list[str] = []
        if p < equilibrio:
            contra.append(
                f"a probabilidade estimada ({p:.1%}) fica abaixo do que este "
                f"RR exige para empatar ({equilibrio:.1%}), antes de custos")
        if cal.vies < -0.02:
            contra.append(
                f"o modelo superestimou {abs(cal.vies):.1%} na validação; "
                f"a probabilidade acima já passou pelo mapa de recalibração, "
                f"mas o viés original é motivo para cautela")

        # A confiança vem do tamanho da validação e da qualidade da
        # calibração — não da probabilidade em si. Um modelo muito confiante
        # e mal calibrado tem de pesar pouco.
        confianca = min(0.90, 0.20 + 0.70 * min(1.0, modelo.n_validacao / 500.0))
        confianca *= max(0.3, 1.0 - cal.ece / 0.10)
        if not cal.brier.melhor_que_taxa_base:
            confianca *= 0.4

        return self._parecer(
            valor, confianca, evidencias=ev, contraindicacoes=contra,
            metricas={
                "p_alvo_antes_do_stop": round(p, 4),
                "equilibrio_rr": round(equilibrio, 4),
                "ece": round(cal.ece, 4),
                "skill": round(cal.brier.skill, 4),
                "n_validacao": modelo.n_validacao,
                "modelo_periodo_treino": [modelo.ts_treino_inicio,
                                          modelo.ts_treino_fim],
            })


def to_dict_config(agente: AgenteML) -> dict[str, Any]:
    return {"nome": agente.nome, "peso": agente.peso,
            "tem_fonte_de_modelo": agente._buscar is not None}


__all__ = ["AgenteML", "limiar_neutro"]
