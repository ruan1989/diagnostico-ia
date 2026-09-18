"""Detector de regime de mercado.

Por que isto habilita e desabilita estratégias
----------------------------------------------
A mesma configuração de indicadores tem expectativa OPOSTA em tendência e em
lateralidade. Seguir rompimento funciona em tendência e sangra em faixa;
comprar suporte funciona em faixa e é atropelado em tendência. Rodar a mesma
estratégia nos dois regimes é como manter a mesma marcha na subida e na
descida.

Por isso o regime aqui não é rótulo decorativo: cada estratégia declara em
que regimes é operável, e o motor a desliga fora deles.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from ..indicators import adx, bollinger, ema, rma, true_range
from ..models import Candle


class RegimeMercado(str, Enum):
    TENDENCIA_ALTA = "trending_up"
    TENDENCIA_BAIXA = "trending_down"
    LATERAL = "sideways"
    ALTA_VOLATILIDADE = "high_volatility"
    BAIXA_VOLATILIDADE = "low_volatility"
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    INCERTO = "uncertain"


DESCRICAO: dict[RegimeMercado, str] = {
    RegimeMercado.TENDENCIA_ALTA:
        "direção definida de alta com força medida; estratégias de "
        "seguimento têm expectativa favorável",
    RegimeMercado.TENDENCIA_BAIXA:
        "direção definida de baixa; seguir tendência funciona vendido",
    RegimeMercado.LATERAL:
        "preço oscilando em faixa; rompimentos costumam falhar e reversão "
        "à média tem vantagem",
    RegimeMercado.ALTA_VOLATILIDADE:
        "amplitude muito acima do normal; stops são atingidos por ruído e o "
        "dimensionamento precisa cair",
    RegimeMercado.BAIXA_VOLATILIDADE:
        "amplitude comprimida; costuma anteceder expansão, mas operar a "
        "compressão em si rende pouco para o custo",
    RegimeMercado.RISK_ON:
        "apetite por risco: ativos de risco sobem juntos",
    RegimeMercado.RISK_OFF:
        "aversão a risco: correlações vão a 1 e a diversificação falha",
    RegimeMercado.INCERTO:
        "sinais contraditórios ou dados insuficientes; nenhuma estratégia "
        "deve ser habilitada por padrão",
}

# Que regimes cada família de estratégia suporta.
COMPATIBILIDADE: dict[str, tuple[RegimeMercado, ...]] = {
    "seguimento_de_tendencia": (RegimeMercado.TENDENCIA_ALTA,
                                RegimeMercado.TENDENCIA_BAIXA,
                                RegimeMercado.RISK_ON),
    "reversao_a_media": (RegimeMercado.LATERAL,
                         RegimeMercado.BAIXA_VOLATILIDADE),
    "rompimento": (RegimeMercado.BAIXA_VOLATILIDADE,
                   RegimeMercado.TENDENCIA_ALTA,
                   RegimeMercado.TENDENCIA_BAIXA),
    "carrego": (RegimeMercado.LATERAL, RegimeMercado.BAIXA_VOLATILIDADE,
                RegimeMercado.RISK_ON),
}


@dataclass(slots=True)
class LeituraRegime:
    regime: RegimeMercado
    regimes_secundarios: list[RegimeMercado] = field(default_factory=list)
    confianca: float = 0.0
    descricao: str = ""
    metricas: dict[str, Any] = field(default_factory=dict)
    evidencias: list[str] = field(default_factory=list)
    estrategias_habilitadas: list[str] = field(default_factory=list)
    estrategias_desabilitadas: list[str] = field(default_factory=list)

    def habilita(self, familia: str) -> bool:
        return familia in self.estrategias_habilitadas

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "regimes_secundarios": [r.value for r in self.regimes_secundarios],
            "confianca": round(self.confianca, 3),
            "descricao": self.descricao,
            "metricas": self.metricas,
            "evidencias": self.evidencias,
            "estrategias_habilitadas": self.estrategias_habilitadas,
            "estrategias_desabilitadas": self.estrategias_desabilitadas,
        }


def detectar_regime(velas: Sequence[Candle], *,
                    janela_vol: int = 100,
                    contexto_mercado: dict[str, Any] | None = None
                    ) -> LeituraRegime:
    """Classifica o regime a partir de preço, e de macro quando disponível.

    `contexto_mercado` traz sinais externos (breadth, correlação média,
    apetite por risco). Sem eles, os regimes RISK_ON/RISK_OFF não são
    afirmados — porque risco agregado não se mede num único ativo.
    """
    if len(velas) < max(220, janela_vol + 30):
        return LeituraRegime(
            RegimeMercado.INCERTO, confianca=0.0,
            descricao=DESCRICAO[RegimeMercado.INCERTO],
            evidencias=[f"apenas {len(velas)} candles: insuficiente para "
                        f"classificar regime"],
            estrategias_desabilitadas=list(COMPATIBILIDADE))

    closes = [c.close for c in velas]
    highs = [c.high for c in velas]
    lows = [c.low for c in velas]

    ema_f = ema(closes, 21)[-1] or closes[-1]
    ema_l = ema(closes, 200)[-1] or closes[-1]
    adx_v, di_p, di_m = adx(highs, lows, closes, 14)
    adx_atual = adx_v[-1] or 0.0
    dip = di_p[-1] or 0.0
    dim = di_m[-1] or 0.0

    # Volatilidade relativa calculada como "suaviza o TR/preço", e não
    # "suaviza o TR e depois divide pelo preço". A segunda forma tem um
    # artefato: o ATR suavizado é lento, então numa queda ele reflete preços
    # antigos (mais altos) enquanto o denominador já caiu — e a razão sobe
    # sozinha, fazendo toda tendência de baixa parecer choque de
    # volatilidade.
    tr = true_range(highs, lows, closes)
    tr_rel = [(tr[i] / closes[i] * 100.0) if closes[i] else 0.0
              for i in range(len(closes))]
    atr_rel_serie = rma(tr_rel, 14)
    atr_pct = atr_rel_serie[-1] or 0.0

    # Percentil da volatilidade RELATIVA (ATR / preço), não do ATR absoluto.
    # Em série de tendência o ATR absoluto cresce junto com o preço, então o
    # último valor é quase sempre o maior da janela e o percentil satura em
    # 100% — o que classificaria qualquer ativo em alta sustentada como
    # "volatilidade extrema". A razão ATR/preço é estacionária e mede o que
    # interessa: a amplitude em relação ao próprio preço.
    atr_rel = [
        atr_rel_serie[i]
        for i in range(max(0, len(closes) - janela_vol), len(closes))
        if atr_rel_serie[i] is not None
    ]
    if atr_rel and len(atr_rel) >= 20:
        media_rel = sum(atr_rel) / len(atr_rel)
        dispersao = (max(atr_rel) - min(atr_rel)) / media_rel if media_rel else 0.0
        if dispersao < 0.10:
            # Série de volatilidade praticamente constante: o percentil vira
            # ruído de ponto flutuante (qual valor é "o maior" entre números
            # idênticos é arbitrário) e afirmaria choque de volatilidade onde
            # não houve nenhuma mudança.
            percentil_vol = 0.5
            evidencias_vol_constante = True
        else:
            abaixo = sum(1 for v in atr_rel if v <= atr_pct)
            percentil_vol = abaixo / len(atr_rel)
            evidencias_vol_constante = False
    else:
        percentil_vol = 0.5
        evidencias_vol_constante = False

    bb_u, bb_m, bb_l = bollinger(closes, 20, 2.0)
    largura = (((bb_u[-1] or 0) - (bb_l[-1] or 0)) / (bb_m[-1] or 1) * 100.0)
    larguras = [
        ((bb_u[i] or 0) - (bb_l[i] or 0)) / (bb_m[i] or 1) * 100.0
        for i in range(len(closes) - janela_vol, len(closes))
        if bb_m[i]
    ]
    percentil_largura = (sum(1 for x in larguras if x <= largura)
                         / len(larguras)) if larguras else 0.5

    metricas = {
        "adx": round(adx_atual, 2), "di_plus": round(dip, 2),
        "di_minus": round(dim, 2), "atr_pct": round(atr_pct, 3),
        "percentil_volatilidade": round(percentil_vol, 3),
        "largura_bollinger_pct": round(largura, 3),
        "percentil_largura": round(percentil_largura, 3),
        "ema21_vs_ema200_pct": round((ema_f - ema_l) / ema_l * 100.0, 3)
        if ema_l else 0.0,
    }

    evidencias: list[str] = [
        f"ADX {adx_atual:.1f} (+DI {dip:.1f} / -DI {dim:.1f})",
        f"ATR relativo de {atr_pct:.2f}% do preço, em {percentil_vol:.0%} do "
        f"percentil da janela de {janela_vol} barras",
        f"largura de Bollinger em {percentil_largura:.0%} do percentil",
    ]
    if evidencias_vol_constante:
        evidencias.append(
            "a volatilidade relativa é praticamente constante na janela: o "
            "percentil não é informativo e foi neutralizado")

    # ----------------------------------------------------- classificação
    secundarios: list[RegimeMercado] = []
    tendencia_forte = adx_atual >= 22.0
    # A direção vem do ADX/DI, que é o medidor de tendência. A posição da
    # EMA21 contra a EMA200 é um filtro de horizonte mais longo: quando ela
    # discorda, o regime continua sendo tendência (o ADX está medindo algo
    # real), mas a confiança cai — é um movimento forte contra a estrutura
    # maior. Exigir as duas coisas classificaria ADX de 34 como "incerto" e
    # desabilitaria toda estratégia num mercado que claramente tem direção.
    direcao_alta = dip > dim
    direcao_baixa = dim > dip
    ema_confirma_alta = ema_f > ema_l
    ema_confirma_baixa = ema_f < ema_l
    alta = direcao_alta
    baixa = direcao_baixa
    ema_discorda = ((direcao_alta and not ema_confirma_alta)
                    or (direcao_baixa and not ema_confirma_baixa))

    # Dois caminhos para ALTA_VOLATILIDADE, e ambos importam:
    #  - relativo: a volatilidade está no topo do que é normal PARA ESTE
    #    ativo (choque, mudança de regime);
    #  - absoluto: o ativo é violento de forma consistente, e nesse caso o
    #    percentil fica no meio da janela e não acusaria nada.
    vol_extrema_relativa = percentil_vol >= 0.85
    vol_extrema_absoluta = atr_pct >= 4.0

    if vol_extrema_relativa or vol_extrema_absoluta:
        principal = RegimeMercado.ALTA_VOLATILIDADE
        if vol_extrema_relativa:
            confianca = 0.55 + 0.35 * (percentil_vol - 0.85) / 0.15
            evidencias.append(
                f"volatilidade no topo da própria janela ({percentil_vol:.0%} "
                f"do percentil)")
        else:
            confianca = min(0.90, 0.50 + (atr_pct - 4.0) / 8.0)
            evidencias.append(
                f"ATR de {atr_pct:.2f}% do preço é alto em termos absolutos, "
                f"mesmo sendo o normal deste ativo")
        if tendencia_forte and alta:
            secundarios.append(RegimeMercado.TENDENCIA_ALTA)
        elif tendencia_forte and baixa:
            secundarios.append(RegimeMercado.TENDENCIA_BAIXA)
    elif tendencia_forte and (alta or baixa):
        principal = (RegimeMercado.TENDENCIA_ALTA if alta
                     else RegimeMercado.TENDENCIA_BAIXA)
        confianca = min(0.95, 0.45 + (adx_atual - 22.0) / 30.0)
        if ema_discorda:
            confianca *= 0.65
            evidencias.append(
                "a estrutura de médias longas NÃO confirma a direção do "
                "ADX/DI: movimento forte contra a tendência maior, o que "
                "reduz a confiança da leitura")
    elif ((percentil_largura <= 0.20 and percentil_vol <= 0.30)
          or atr_pct <= 0.35):
        # Dois caminhos, simétricos aos da alta volatilidade: compressão
        # relativa à própria janela, ou amplitude absoluta muito baixa. O
        # segundo é necessário porque a neutralização do percentil em série
        # de volatilidade constante bloquearia o primeiro.
        principal = RegimeMercado.BAIXA_VOLATILIDADE
        if atr_pct <= 0.35:
            confianca = min(0.85, 0.55 + (0.35 - atr_pct) / 0.35 * 0.3)
            evidencias.append(
                f"ATR de {atr_pct:.2f}% do preço é amplitude comprimida em "
                f"termos absolutos")
        else:
            confianca = 0.60 + 0.30 * (0.30 - percentil_vol) / 0.30
        secundarios.append(RegimeMercado.LATERAL)
    elif adx_atual < 18.0:
        principal = RegimeMercado.LATERAL
        confianca = 0.50 + 0.30 * (18.0 - adx_atual) / 18.0
    elif ((alta and ema_confirma_alta) or (baixa and ema_confirma_baixa)):
        # ADX entre 18 e 22 COM as médias longas confirmando é tendência
        # fraca, não ausência de regime. A confirmação da EMA é obrigatória
        # aqui: sem ela, qualquer oscilação em que +DI passe -DI por ruído
        # viraria "tendência", inclusive uma senoide perfeitamente lateral.
        principal = (RegimeMercado.TENDENCIA_ALTA if alta
                     else RegimeMercado.TENDENCIA_BAIXA)
        confianca = 0.35
        evidencias.append(
            f"ADX de {adx_atual:.1f} indica tendência FRACA: médias longas "
            f"confirmam a direção, mas sem força medida")
    else:
        principal = RegimeMercado.INCERTO
        confianca = 0.30
        evidencias.append("ADX intermediário sem estrutura de médias clara: "
                          "não há regime definido")

    # ---------------------------------------------- risk-on / risk-off
    ctx = contexto_mercado or {}
    if "apetite_risco" in ctx or "correlacao_media" in ctx:
        apetite = ctx.get("apetite_risco")
        corr = ctx.get("correlacao_media")
        if corr is not None and corr >= 0.75:
            secundarios.append(RegimeMercado.RISK_OFF)
            evidencias.append(
                f"correlação média de {corr:.2f} entre ativos de risco: "
                f"comportamento de risk-off, diversificação não protege")
        elif apetite is not None:
            if str(apetite).lower() == "risk_on":
                secundarios.append(RegimeMercado.RISK_ON)
                evidencias.append("apetite por risco reportado como risk-on")
            elif str(apetite).lower() == "risk_off":
                secundarios.append(RegimeMercado.RISK_OFF)
                evidencias.append("apetite por risco reportado como risk-off")
    else:
        evidencias.append(
            "risk-on/risk-off NÃO avaliado: exige dado agregado de mercado "
            "(correlação média ou apetite por risco), que não pode ser "
            "inferido de um único ativo")

    # ------------------------------------------- habilitação de estratégias
    ativos = {principal, *secundarios}
    habilitadas = [fam for fam, regimes in COMPATIBILIDADE.items()
                   if ativos & set(regimes)]
    desabilitadas = [fam for fam in COMPATIBILIDADE if fam not in habilitadas]

    # Alta volatilidade desabilita tudo que dependa de stop apertado.
    if principal is RegimeMercado.ALTA_VOLATILIDADE:
        for fam in ("reversao_a_media", "carrego"):
            if fam in habilitadas:
                habilitadas.remove(fam)
                desabilitadas.append(fam)
        evidencias.append(
            "volatilidade no topo do percentil: estratégias de stop apertado "
            "desabilitadas porque o stop vira ruído")

    if RegimeMercado.RISK_OFF in ativos:
        evidencias.append(
            "risk-off: mesmo estratégias habilitadas devem operar com "
            "tamanho reduzido, porque as posições deixam de ser independentes")

    return LeituraRegime(
        regime=principal, regimes_secundarios=secundarios,
        confianca=max(0.0, min(1.0, confianca)),
        descricao=DESCRICAO[principal], metricas=metricas,
        evidencias=evidencias,
        estrategias_habilitadas=sorted(habilitadas),
        estrategias_desabilitadas=sorted(set(desabilitadas)))
