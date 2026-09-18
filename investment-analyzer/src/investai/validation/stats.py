"""Estatística honesta sobre resultados de operações.

O erro que este módulo existe para impedir
------------------------------------------
"Acertei 9 de 10, logo minha probabilidade é 90%." Não é. Com 10 observações,
o intervalo de confiança de 95% para uma taxa observada de 90% vai de ~60% a
~98%. A estimativa pontual sozinha é quase informação nenhuma.

Por isso toda taxa de acerto aqui vem com:

* **intervalo de confiança** (Wilson, que se comporta bem em amostra pequena e
  perto dos extremos, ao contrário do intervalo normal ingênuo);
* **tamanho da amostra** sempre visível;
* **limite inferior do IC** usado nas decisões, não a estimativa pontual —
  porque decidir pela melhor leitura possível de uma amostra pequena é como
  planejar a viagem pelo melhor trânsito imaginável.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

# z para 95% bilateral. Mantido explícito em vez de calculado para deixar
# claro qual nível de confiança está em uso.
Z_95 = 1.959963984540054
Z_90 = 1.6448536269514722
Z_99 = 2.5758293035489004


def z_para(confianca: float) -> float:
    if abs(confianca - 0.95) < 1e-9:
        return Z_95
    if abs(confianca - 0.90) < 1e-9:
        return Z_90
    if abs(confianca - 0.99) < 1e-9:
        return Z_99
    raise ValueError("confiança suportada: 0.90, 0.95 ou 0.99")


@dataclass(frozen=True, slots=True)
class IntervaloConfianca:
    estimativa: float
    inferior: float
    superior: float
    n: int
    confianca: float = 0.95
    # True quando a grandeza é uma PROPORÇÃO (0..1). O critério de amplitude
    # de `informativo` só faz sentido nesse caso: 0,30 de amplitude numa taxa
    # de acerto é largo demais, mas 0,30 de amplitude numa expectativa medida
    # em múltiplos de risco (R) é perfeitamente normal. Aplicar o mesmo
    # limiar aos dois produziria alarme falso constante.
    e_proporcao: bool = True

    @property
    def amplitude(self) -> float:
        return self.superior - self.inferior

    @property
    def informativo(self) -> bool:
        """Se a amostra sustenta uma conclusão.

        Para proporção: amplitude acima de 0,30 significa amostra pequena
        demais — é o caso de "9 de 10". Para grandeza sem escala fixa (como
        expectativa em R), amplitude não tem limiar universal, então o
        critério é apenas o tamanho da amostra, e quem julga o intervalo é o
        chamador (tipicamente conferindo se o piso exclui zero).
        """
        if not self.e_proporcao:
            return self.n >= 30
        return self.n >= 20 and self.amplitude <= 0.30

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimativa": round(self.estimativa, 4),
            "inferior": round(self.inferior, 4),
            "superior": round(self.superior, 4),
            "amplitude": round(self.amplitude, 4),
            "n": self.n, "confianca": self.confianca,
            "e_proporcao": self.e_proporcao,
            "informativo": self.informativo,
        }


def wilson(acertos: int, n: int, confianca: float = 0.95) -> IntervaloConfianca:
    """Intervalo de Wilson para uma proporção.

    Escolhido em vez do intervalo normal (`p ± z·√(p(1-p)/n)`) porque este
    último produz absurdos em amostra pequena e perto de 0 ou 1: com 10
    acertos em 10, ele devolve [1,0; 1,0] — certeza a partir de dez
    observações. Wilson devolve [0,72; 1,0], que é a leitura correta.
    """
    if n < 0 or acertos < 0 or acertos > n:
        raise ValueError(f"acertos={acertos} incoerente com n={n}")
    if n == 0:
        return IntervaloConfianca(0.0, 0.0, 1.0, 0, confianca)

    z = z_para(confianca)
    p = acertos / n
    denom = 1.0 + z * z / n
    centro = (p + z * z / (2 * n)) / denom
    margem = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    # Arredondar em 12 casas antes de travar nos limites: sem isso, um caso
    # como 30/30 devolve 0.9999999999999999 em vez de 1.0 por resíduo de
    # ponto flutuante, e qualquer comparação com o limite falha.
    inferior = max(0.0, round(centro - margem, 12))
    superior = min(1.0, round(centro + margem, 12))
    return IntervaloConfianca(estimativa=p, inferior=inferior,
                              superior=superior, n=n, confianca=confianca,
                              e_proporcao=True)


def ic_media(valores: Sequence[float],
             confianca: float = 0.95) -> IntervaloConfianca:
    """IC da média via erro padrão. Usado para expectativa em R.

    Marca `e_proporcao=False`: a grandeza não está em 0..1, então o critério
    de amplitude de `informativo` não se aplica.
    """
    n = len(valores)
    if n == 0:
        return IntervaloConfianca(0.0, 0.0, 0.0, 0, confianca,
                                  e_proporcao=False)
    media = sum(valores) / n
    if n < 2:
        return IntervaloConfianca(media, media, media, n, confianca,
                                  e_proporcao=False)
    var = sum((v - media) ** 2 for v in valores) / (n - 1)
    erro = math.sqrt(var / n)
    z = z_para(confianca)
    return IntervaloConfianca(media, media - z * erro, media + z * erro,
                              n, confianca, e_proporcao=False)


@dataclass(slots=True)
class ExpectedValue:
    """Expectativa matemática decomposta e auditável."""

    n: int
    p_ganho: float
    ganho_medio: float          # em unidades de risco (R)
    p_perda: float
    perda_media: float          # positivo, em R
    custos_r: float             # custos já convertidos para R
    ev_bruto_r: float
    ev_liquido_r: float
    ic_win_rate: IntervaloConfianca
    ic_expectativa: IntervaloConfianca
    # EV recalculado com o limite INFERIOR do IC da taxa de acerto: é o
    # cenário que a amostra ainda não permite descartar.
    ev_pessimista_r: float = 0.0
    amostra_suficiente: bool = False
    motivos: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.motivos is None:
            self.motivos = []

    @property
    def positivo(self) -> bool:
        return self.ev_liquido_r > 0

    @property
    def positivo_no_pior_caso(self) -> bool:
        """O teste que realmente importa antes de arriscar capital."""
        return self.ev_pessimista_r > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "p_ganho": round(self.p_ganho, 4),
            "ganho_medio_r": round(self.ganho_medio, 4),
            "p_perda": round(self.p_perda, 4),
            "perda_media_r": round(self.perda_media, 4),
            "custos_r": round(self.custos_r, 5),
            "ev_bruto_r": round(self.ev_bruto_r, 4),
            "ev_liquido_r": round(self.ev_liquido_r, 4),
            "ev_pessimista_r": round(self.ev_pessimista_r, 4),
            "positivo": self.positivo,
            "positivo_no_pior_caso": self.positivo_no_pior_caso,
            "amostra_suficiente": self.amostra_suficiente,
            "ic_win_rate": self.ic_win_rate.to_dict(),
            "ic_expectativa": self.ic_expectativa.to_dict(),
            "motivos": self.motivos,
            "formula": "EV = P(ganho) × ganho médio − P(perda) × perda média − custos",
        }


def calcular_ev(retornos_r: Sequence[float], *, custos_r: float = 0.0,
                n_minimo: int = 30,
                confianca: float = 0.95) -> ExpectedValue:
    """Calcula expectativa matemática a partir dos resultados em R.

    `retornos_r` são os resultados já em múltiplos de risco (+2.0 = ganhou
    2R). `custos_r` é o custo médio por operação, também em R, para quando
    ele não estiver embutido nos retornos.
    """
    n = len(retornos_r)
    if n == 0:
        vazio = wilson(0, 0, confianca)
        return ExpectedValue(
            n=0, p_ganho=0.0, ganho_medio=0.0, p_perda=0.0, perda_media=0.0,
            custos_r=custos_r, ev_bruto_r=0.0, ev_liquido_r=0.0,
            ic_win_rate=vazio, ic_expectativa=ic_media([], confianca),
            ev_pessimista_r=0.0, amostra_suficiente=False,
            motivos=["nenhuma operação na amostra"])

    ganhos = [r for r in retornos_r if r > 0]
    perdas = [r for r in retornos_r if r <= 0]
    p_ganho = len(ganhos) / n
    p_perda = len(perdas) / n
    ganho_medio = (sum(ganhos) / len(ganhos)) if ganhos else 0.0
    perda_media = (abs(sum(perdas)) / len(perdas)) if perdas else 0.0

    ev_bruto = p_ganho * ganho_medio - p_perda * perda_media
    ev_liquido = ev_bruto - custos_r

    ic_wr = wilson(len(ganhos), n, confianca)
    ic_exp = ic_media(list(retornos_r), confianca)

    # Cenário que a amostra não descarta: taxa de acerto no piso do IC.
    p_pess = ic_wr.inferior
    ev_pess = p_pess * ganho_medio - (1.0 - p_pess) * perda_media - custos_r

    motivos: list[str] = []
    suficiente = True
    if n < n_minimo:
        suficiente = False
        motivos.append(
            f"amostra de {n} operações abaixo do mínimo de {n_minimo} — "
            f"CONFIANÇA ESTATÍSTICA INSUFICIENTE")
    if not ic_wr.informativo:
        suficiente = False
        motivos.append(
            f"intervalo de confiança da taxa de acerto vai de "
            f"{ic_wr.inferior:.1%} a {ic_wr.superior:.1%} — amplitude de "
            f"{ic_wr.amplitude:.1%} é larga demais para sustentar decisão")
    if not perdas:
        suficiente = False
        motivos.append(
            "nenhuma operação perdedora na amostra — impossível estimar a "
            "perda média; isso indica amostra curta, não estratégia perfeita")
    if ev_liquido > 0 and ev_pess <= 0:
        motivos.append(
            f"EV é positivo na estimativa central ({ev_liquido:+.3f}R) mas "
            f"negativo no piso do IC ({ev_pess:+.3f}R) — o resultado pode ser "
            f"sorte da amostra")

    return ExpectedValue(
        n=n, p_ganho=p_ganho, ganho_medio=ganho_medio, p_perda=p_perda,
        perda_media=perda_media, custos_r=custos_r, ev_bruto_r=ev_bruto,
        ev_liquido_r=ev_liquido, ic_win_rate=ic_wr, ic_expectativa=ic_exp,
        ev_pessimista_r=ev_pess, amostra_suficiente=suficiente,
        motivos=motivos)


def kelly_fraction(p: float, ganho_r: float, perda_r: float = 1.0) -> float:
    """Fração de Kelly — devolvida apenas como referência teórica.

    Kelly maximiza crescimento logarítmico assumindo que `p` é conhecido com
    exatidão. Como `p` aqui é estimado com incerteza, apostar Kelly cheio é
    uma forma eficiente de ir à ruína: erro de 10% na estimativa de `p` já
    torna a fração agressiva demais. O sistema usa risco fixo pequeno; este
    número existe para comparação, e é limitado a 25% (Kelly fracionário).
    """
    if ganho_r <= 0 or perda_r <= 0:
        return 0.0
    b = ganho_r / perda_r
    f = (p * (b + 1) - 1) / b
    return max(0.0, min(f, 0.25))


def risco_de_ruina(p_ganho: float, ganho_r: float, perda_r: float,
                   risco_por_trade_frac: float, capital_ruina_frac: float = 0.5,
                   n_trades: int = 200) -> float:
    """Probabilidade aproximada de perder `capital_ruina_frac` do capital.

    ATENÇÃO — este número é OTIMISTA por construção, e não deve ser usado
    como aprovação. A fórmula de gambler's ruin assume que a vantagem (`p`,
    ganho e perda) é conhecida com exatidão e permanece constante para
    sempre. As três premissas são falsas na prática: `p` é estimado com
    incerteza, vantagem decai quando o regime muda, e perdas se agrupam
    porque posições são correlacionadas. Por isso ela devolve ~0% para
    configurações razoáveis — o que reflete a matemática do modelo, não a
    realidade do mercado.

    Serve só como filtro na direção contrária: se ESTE número já é alto, a
    configuração é indefensável. O gate de promoção usa o Monte Carlo sobre a
    distribuição empírica (`validation/montecarlo.py`), que reamostra os
    resultados observados e captura agrupamento de perdas.
    """
    if not 0 < risco_por_trade_frac < 1 or not 0 < capital_ruina_frac < 1:
        return 0.0
    if p_ganho <= 0:
        return 1.0

    ev = p_ganho * ganho_r - (1 - p_ganho) * perda_r
    if ev <= 0:
        return 1.0    # expectativa negativa: a ruína é questão de tempo

    # Aproximação de Gambler's ruin em unidades de R.
    unidades_ate_ruina = capital_ruina_frac / risco_por_trade_frac
    q_p = ((1 - p_ganho) * perda_r) / (p_ganho * ganho_r)
    if q_p >= 1.0:
        return 1.0
    prob = q_p ** unidades_ate_ruina
    # Limita pelo horizonte: não dá para arruinar em menos trades que o
    # número de unidades de risco disponíveis.
    if n_trades < unidades_ate_ruina:
        return 0.0
    return max(0.0, min(1.0, prob))
