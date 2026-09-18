"""Detecção de overfitting.

O sintoma que este módulo procura
---------------------------------
Uma estratégia superajustada não parece ruim — parece *excelente*. É
exatamente isso que a torna perigosa. Os cinco sinais abaixo são o que
distingue vantagem real de memorização do passado:

1. **Degradação IS → OOS** — o resultado encolhe quando a estratégia deixa de
   ver os dados que a ajustaram.
2. **Concentração de P&L** — o lucro todo vem de 2 ou 3 operações. Sem elas, a
   estratégia é neutra ou negativa. Isso não é vantagem, é sorte localizada.
3. **Instabilidade de parâmetros** — mexer 10% num parâmetro destrói o
   resultado. Vantagem real é um platô, não um pico.
4. **Inconsistência entre períodos** — ganha num regime e devolve em outro.
5. **Excesso de graus de liberdade** — muitos parâmetros para poucas
   operações. Com 8 parâmetros e 40 trades, é possível ajustar qualquer coisa.

Nenhum sinal isolado condena. Vários juntos condenam.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence

from ..models import BacktestStats, Trade


class VeredictoOverfit(str, Enum):
    ROBUSTO = "robusto"
    SUSPEITO = "suspeito"
    PROVAVEL_OVERFITTING = "provavel_overfitting"
    INDETERMINADO = "indeterminado"      # amostra insuficiente para julgar


@dataclass(slots=True)
class SinalOverfit:
    nome: str
    disparou: bool
    valor: float
    limiar: float
    explicacao: str

    def to_dict(self) -> dict[str, Any]:
        return {"nome": self.nome, "disparou": self.disparou,
                "valor": round(self.valor, 4), "limiar": self.limiar,
                "explicacao": self.explicacao}


@dataclass(slots=True)
class RelatorioOverfit:
    veredicto: VeredictoOverfit
    sinais: list[SinalOverfit] = field(default_factory=list)
    n_disparados: int = 0
    resumo: str = ""

    @property
    def aprovado(self) -> bool:
        return self.veredicto is VeredictoOverfit.ROBUSTO

    def to_dict(self) -> dict[str, Any]:
        return {
            "veredicto": self.veredicto.value,
            "aprovado": self.aprovado,
            "n_disparados": self.n_disparados,
            "resumo": self.resumo,
            "sinais": [s.to_dict() for s in self.sinais],
        }


# ---------------------------------------------------------------- sinais
def sinal_degradacao(is_: BacktestStats, oos: BacktestStats,
                     limiar: float = 0.50) -> SinalOverfit:
    if is_.expectancy_r == 0:
        valor = 0.0
    else:
        valor = (is_.expectancy_r - oos.expectancy_r) / abs(is_.expectancy_r)
    return SinalOverfit(
        "degradacao_is_oos", valor > limiar, valor, limiar,
        f"expectativa caiu de {is_.expectancy_r:+.3f}R (treino) para "
        f"{oos.expectancy_r:+.3f}R (out-of-sample), queda de {valor:.0%}"
        + (" — a vantagem não sobreviveu a dados novos" if valor > limiar
           else ""),
    )


def sinal_concentracao(trades: Sequence[Trade],
                       limiar: float = 0.50) -> SinalOverfit:
    """Fração do lucro total que vem das 3 melhores operações."""
    if not trades:
        return SinalOverfit("concentracao_pnl", False, 0.0, limiar,
                            "sem operações")
    pnls = sorted((t.pnl_usd for t in trades), reverse=True)
    total = sum(pnls)
    if total <= 0:
        return SinalOverfit(
            "concentracao_pnl", False, 0.0, limiar,
            "resultado total não é positivo; concentração não se aplica")
    top3 = sum(pnls[:3])
    valor = top3 / total
    sem_top3 = total - top3
    return SinalOverfit(
        "concentracao_pnl", valor > limiar, valor, limiar,
        f"as 3 melhores operações respondem por {valor:.0%} do lucro; sem "
        f"elas o resultado seria {sem_top3:+.2f} em vez de {total:+.2f}"
        + (" — o resultado depende de poucos eventos, não de vantagem "
           "repetível" if valor > limiar else ""),
    )


def sinal_consistencia(stats_por_periodo: Sequence[BacktestStats],
                       limiar: float = 0.50) -> SinalOverfit:
    """Fração de períodos com expectativa positiva."""
    if len(stats_por_periodo) < 3:
        return SinalOverfit(
            "consistencia_periodos", False, 1.0, limiar,
            f"apenas {len(stats_por_periodo)} períodos — insuficiente para "
            f"avaliar consistência")
    positivos = sum(1 for s in stats_por_periodo if s.expectancy_r > 0)
    valor = positivos / len(stats_por_periodo)
    return SinalOverfit(
        "consistencia_periodos", valor < limiar, valor, limiar,
        f"{positivos} de {len(stats_por_periodo)} períodos com expectativa "
        f"positiva ({valor:.0%})"
        + (" — desempenho não se repete entre períodos" if valor < limiar
           else ""),
    )


def sinal_graus_de_liberdade(n_parametros: int, n_trades: int,
                             limiar: float = 10.0) -> SinalOverfit:
    """Operações por parâmetro ajustado.

    Com menos de ~10 operações por parâmetro é possível ajustar a estratégia
    para quase qualquer histórico sem que isso signifique nada.
    """
    if n_parametros <= 0:
        return SinalOverfit("graus_de_liberdade", False, 999.0, limiar,
                            "nenhum parâmetro ajustado")
    valor = n_trades / n_parametros
    return SinalOverfit(
        "graus_de_liberdade", valor < limiar, valor, limiar,
        f"{n_trades} operações para {n_parametros} parâmetros ajustados "
        f"({valor:.1f} operações por parâmetro)"
        + (f" — abaixo de {limiar:.0f} por parâmetro, o ajuste explica o "
           f"resultado" if valor < limiar else ""),
    )


def sinal_estabilidade(resultados_vizinhos: Sequence[float],
                       resultado_central: float,
                       limiar: float = 0.40) -> SinalOverfit:
    """Queda média ao perturbar parâmetros em torno do valor escolhido.

    Vantagem real forma platô: vizinhos rendem parecido. Pico isolado é
    assinatura de ajuste ao ruído.
    """
    if not resultados_vizinhos:
        return SinalOverfit(
            "estabilidade_parametros", False, 0.0, limiar,
            "análise de sensibilidade não executada")
    media_viz = statistics.fmean(resultados_vizinhos)
    if resultado_central == 0:
        valor = 0.0
    else:
        valor = (resultado_central - media_viz) / abs(resultado_central)
    piores = sum(1 for v in resultados_vizinhos if v <= 0)
    return SinalOverfit(
        "estabilidade_parametros", valor > limiar, valor, limiar,
        f"parâmetro escolhido rende {resultado_central:+.3f}R e a média dos "
        f"{len(resultados_vizinhos)} vizinhos rende {media_viz:+.3f}R "
        f"(queda de {valor:.0%}; {piores} vizinhos não-positivos)"
        + (" — pico isolado, não platô" if valor > limiar else ""),
    )


# ------------------------------------------------------------- consolidação
def avaliar_overfitting(*, stats_is: BacktestStats, stats_oos: BacktestStats,
                        trades_oos: Sequence[Trade],
                        stats_por_periodo: Sequence[BacktestStats] = (),
                        n_parametros: int = 0,
                        resultados_vizinhos: Sequence[float] = (),
                        resultado_central: float = 0.0,
                        n_minimo_trades: int = 30) -> RelatorioOverfit:
    """Consolida os cinco sinais num veredicto."""
    sinais = [
        sinal_degradacao(stats_is, stats_oos),
        sinal_concentracao(trades_oos),
        sinal_consistencia(stats_por_periodo),
        sinal_graus_de_liberdade(n_parametros, stats_oos.trades),
        sinal_estabilidade(resultados_vizinhos, resultado_central),
    ]
    disparados = [s for s in sinais if s.disparou]

    # Amostra pequena não permite concluir nem a favor nem contra.
    if stats_oos.trades < n_minimo_trades:
        return RelatorioOverfit(
            VeredictoOverfit.INDETERMINADO, sinais, len(disparados),
            f"apenas {stats_oos.trades} operações out-of-sample (mínimo "
            f"{n_minimo_trades}): não é possível distinguir vantagem de ruído. "
            f"Isso NÃO é aprovação — é ausência de evidência.")

    if len(disparados) >= 2:
        veredicto = VeredictoOverfit.PROVAVEL_OVERFITTING
        resumo = (f"{len(disparados)} sinais de overfitting disparados: "
                  + "; ".join(s.nome for s in disparados)
                  + ". Estratégia deve ser rejeitada ou reformulada.")
    elif len(disparados) == 1:
        veredicto = VeredictoOverfit.SUSPEITO
        resumo = (f"1 sinal disparado ({disparados[0].nome}). Não condena, "
                  f"mas exige verificação antes de avançar de fase.")
    else:
        veredicto = VeredictoOverfit.ROBUSTO
        resumo = ("nenhum sinal de overfitting disparado nos testes "
                  "aplicados; a estratégia se manteve fora da amostra.")

    return RelatorioOverfit(veredicto, sinais, len(disparados), resumo)


def analise_sensibilidade(parametros: dict[str, Any],
                          avaliar: Callable[[dict[str, Any]], float],
                          *, perturbacao: float = 0.15,
                          chaves: Sequence[str] | None = None
                          ) -> dict[str, Any]:
    """Perturba cada parâmetro numérico ±`perturbacao` e mede o impacto.

    Devolve os resultados dos vizinhos para alimentar `sinal_estabilidade`.
    """
    central = avaliar(dict(parametros))
    alvos = [k for k in (chaves or parametros)
             if isinstance(parametros.get(k), (int, float))
             and not isinstance(parametros.get(k), bool)]

    vizinhos: list[float] = []
    detalhe: dict[str, dict[str, float]] = {}
    for chave in alvos:
        base = float(parametros[chave])
        linha: dict[str, float] = {}
        for direcao, rotulo in ((1 + perturbacao, "acima"),
                                (1 - perturbacao, "abaixo")):
            variante = dict(parametros)
            novo = base * direcao
            variante[chave] = (int(round(novo))
                               if isinstance(parametros[chave], int) else novo)
            valor = avaliar(variante)
            linha[rotulo] = valor
            vizinhos.append(valor)
        detalhe[chave] = linha

    return {
        "resultado_central": central,
        "resultados_vizinhos": vizinhos,
        "detalhe_por_parametro": {
            k: {r: round(v, 4) for r, v in linha.items()}
            for k, linha in detalhe.items()
        },
        "perturbacao": perturbacao,
        "n_parametros_testados": len(alvos),
    }
