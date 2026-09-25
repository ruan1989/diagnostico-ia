"""Distância até a liquidação em futuros alavancados.

Por que isto precisa de módulo próprio
--------------------------------------
Em futuros, perder a tese é ruim; ser liquidado é terminal. A diferença é que
o stop fecha a posição no preço que você escolheu, e a liquidação fecha no
preço em que a corretora zera a margem — depois de consumir tudo o que estava
alocado, sem chance de recuperação.

O erro clássico é raciocinar "meu stop é 2%, então estou arriscando 2%". Com
alavancagem 25x, 2% de movimento contrário já é 50% da margem, e a liquidação
chega antes do stop em qualquer pavio. Nesse cenário o stop é decorativo.

A regra deste módulo: **o stop tem que ficar significativamente antes da zona
de liquidação**. Se não ficar, a operação é rejeitada — não ajustada.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import Side

# Taxa de manutenção de margem típica em futuros USDT-M para posições
# pequenas. Exchanges usam tabelas por faixa de notional; este é o piso
# conservador. O valor real deve vir da API quando disponível.
MMR_PADRAO = 0.005          # 0,5%

# O stop precisa estar a esta distância mínima da liquidação, medida como
# fração do caminho entrada → liquidação. 0.35 significa: o stop deve disparar
# antes de 65% do caminho até a liquidação ter sido percorrido.
FOLGA_MINIMA = 0.35

# A folga relativa sozinha subpenaliza alavancagem alta. A 25x com stop de 2%,
# a folga é 43% — parece confortável, mas em termos absolutos a liquidação
# está a 3,5% da entrada, distância que cripto percorre em minutos. Por isso
# existe um segundo critério, ancorado em volatilidade: a liquidação tem de
# estar a pelo menos este número de ATRs da entrada.
MIN_ATR_ATE_LIQUIDACAO = 4.0

# Piso absoluto para quando não há ATR disponível.
MIN_DIST_LIQUIDACAO_PCT = 6.0


@dataclass(slots=True)
class AnaliseLiquidacao:
    side: Side
    entry: float
    leverage: float
    stop_loss: float
    preco_liquidacao: float
    mmr: float
    # Distâncias em % do preço de entrada.
    dist_stop_pct: float
    dist_liquidacao_pct: float
    # Fração do caminho até a liquidação que o stop consome. 0.5 = o stop
    # dispara na metade do caminho.
    folga: float
    # Distância até a liquidação medida em ATRs (None se ATR não informado).
    liquidacao_em_atrs: float | None
    aprovado: bool
    motivo: str = ""
    # Movimento adverso, em %, que zera a margem alocada.
    margem_alocada_usd: float = 0.0
    perda_no_stop_usd: float = 0.0
    perda_na_liquidacao_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side.value, "entry": self.entry,
            "leverage": round(self.leverage, 2),
            "stop_loss": self.stop_loss,
            "preco_liquidacao": round(self.preco_liquidacao, 8),
            "mmr": self.mmr,
            "dist_stop_pct": round(self.dist_stop_pct, 4),
            "dist_liquidacao_pct": round(self.dist_liquidacao_pct, 4),
            "folga": round(self.folga, 4),
            "liquidacao_em_atrs": (round(self.liquidacao_em_atrs, 2)
                                   if self.liquidacao_em_atrs is not None
                                   else None),
            "aprovado": self.aprovado, "motivo": self.motivo,
            "margem_alocada_usd": round(self.margem_alocada_usd, 2),
            "perda_no_stop_usd": round(self.perda_no_stop_usd, 2),
            "perda_na_liquidacao_usd": round(self.perda_na_liquidacao_usd, 2),
        }


def preco_liquidacao(entry: float, leverage: float, side: Side,
                     mmr: float = MMR_PADRAO) -> float:
    """Preço estimado de liquidação em margem isolada.

    Para long:  P_liq = entry × (1 − 1/L + mmr)
    Para short: P_liq = entry × (1 + 1/L − mmr)

    É uma ESTIMATIVA. A exchange aplica tabela de margem por faixa de
    notional, taxa de funding acumulada e taxa de fechamento, o que empurra a
    liquidação um pouco mais perto da entrada do que esta fórmula indica. Por
    isso a folga exigida é grande: ela absorve a imprecisão do modelo.
    """
    if entry <= 0:
        raise ValueError("entry deve ser > 0")
    if leverage < 1:
        raise ValueError("leverage deve ser >= 1")
    fator = 1.0 / leverage - mmr
    if side is Side.LONG:
        return max(entry * (1.0 - fator), 0.0)
    return entry * (1.0 + fator)


def analisar(entry: float, stop_loss: float, side: Side, leverage: float,
             *, notional_usd: float = 0.0, size: float = 0.0,
             mmr: float = MMR_PADRAO,
             folga_minima: float = FOLGA_MINIMA,
             atr_pct: float | None = None,
             min_atr_liquidacao: float = MIN_ATR_ATE_LIQUIDACAO,
             min_dist_liquidacao_pct: float = MIN_DIST_LIQUIDACAO_PCT
             ) -> AnaliseLiquidacao:
    """Avalia se o stop está suficientemente antes da liquidação.

    Aplica DOIS critérios independentes, e ambos têm de passar:

    1. **folga relativa** — o stop não pode consumir mais que
       `1 - folga_minima` do caminho até a liquidação;
    2. **distância absoluta** — a liquidação tem de estar a pelo menos
       `min_atr_liquidacao` ATRs (ou `min_dist_liquidacao_pct`, se não houver
       ATR) da entrada.

    O segundo critério existe porque o primeiro, isolado, aprova alavancagem
    absurda desde que o stop seja apertado na mesma proporção — e stop
    apertado em ativo volátil não protege, só garante que você sai no ruído
    antes de ser liquidado no movimento.
    """
    if entry <= 0:
        raise ValueError("entry deve ser > 0")
    if stop_loss <= 0:
        raise ValueError("stop_loss deve ser > 0")

    long = side is Side.LONG
    # Stop do lado errado da entrada é erro de construção do plano.
    if (long and stop_loss >= entry) or (not long and stop_loss <= entry):
        return AnaliseLiquidacao(
            side=side, entry=entry, leverage=leverage, stop_loss=stop_loss,
            preco_liquidacao=0.0, mmr=mmr, dist_stop_pct=0.0,
            dist_liquidacao_pct=0.0, folga=0.0, liquidacao_em_atrs=None,
            aprovado=False,
            motivo=f"stop {stop_loss:.6g} está do lado errado da entrada "
                   f"{entry:.6g} para uma posição {side.value}")

    p_liq = preco_liquidacao(entry, leverage, side, mmr)
    dist_stop = abs(entry - stop_loss) / entry * 100.0
    dist_liq = abs(entry - p_liq) / entry * 100.0

    if dist_liq <= 0:
        return AnaliseLiquidacao(
            side=side, entry=entry, leverage=leverage, stop_loss=stop_loss,
            preco_liquidacao=p_liq, mmr=mmr, dist_stop_pct=dist_stop,
            dist_liquidacao_pct=0.0, folga=0.0, liquidacao_em_atrs=None,
            aprovado=False,
            motivo=f"alavancagem {leverage:.1f}x coloca a liquidação na "
                   f"própria entrada")

    # Fração do caminho até a liquidação consumida pelo stop.
    consumo = dist_stop / dist_liq
    folga = 1.0 - consumo

    notional = notional_usd or (size * entry)
    margem = notional / leverage if leverage else 0.0
    perda_stop = notional * dist_stop / 100.0
    perda_liq = notional * dist_liq / 100.0

    em_atrs = (dist_liq / atr_pct) if (atr_pct and atr_pct > 0) else None

    analise = AnaliseLiquidacao(
        side=side, entry=entry, leverage=leverage, stop_loss=stop_loss,
        preco_liquidacao=p_liq, mmr=mmr, dist_stop_pct=dist_stop,
        dist_liquidacao_pct=dist_liq, folga=folga, liquidacao_em_atrs=em_atrs,
        aprovado=True, margem_alocada_usd=margem,
        perda_no_stop_usd=perda_stop, perda_na_liquidacao_usd=perda_liq,
    )

    # ------------------------------------------------------------ veredicto
    stop_depois_da_liquidacao = (
        (long and stop_loss <= p_liq) or (not long and stop_loss >= p_liq))
    if stop_depois_da_liquidacao:
        analise.aprovado = False
        analise.motivo = (
            f"OPERAÇÃO REJEITADA: com alavancagem {leverage:.1f}x a liquidação "
            f"acontece em {p_liq:.6g} ({dist_liq:.2f}% da entrada), ANTES do "
            f"stop em {stop_loss:.6g} ({dist_stop:.2f}%). O stop nunca seria "
            f"executado — a posição seria liquidada primeiro, com perda total "
            f"da margem.")
        return analise

    if folga < folga_minima:
        analise.aprovado = False
        analise.motivo = (
            f"OPERAÇÃO REJEITADA: o stop consome {consumo:.0%} do caminho até "
            f"a liquidação (folga de apenas {folga:.0%}, mínimo "
            f"{folga_minima:.0%}). Um pavio de {dist_liq - dist_stop:.2f}% "
            f"além do stop já liquida a posição. Reduza a alavancagem para "
            f"~{sugerir_alavancagem(dist_stop, mmr, folga_minima):.1f}x ou "
            f"aproxime o stop.")
        return analise

    # --------------------------- critério 2: distância absoluta (volatilidade)
    if em_atrs is not None:
        if em_atrs < min_atr_liquidacao:
            analise.aprovado = False
            analise.motivo = (
                f"OPERAÇÃO REJEITADA: a liquidação está a apenas "
                f"{em_atrs:.1f} ATRs da entrada ({dist_liq:.2f}% com ATR de "
                f"{atr_pct:.2f}%), abaixo do mínimo de "
                f"{min_atr_liquidacao:.1f} ATRs. Um movimento normal para "
                f"este ativo liquida a posição. Reduza a alavancagem para "
                f"~{sugerir_alavancagem_por_atr(atr_pct, mmr, min_atr_liquidacao):.1f}x.")
            return analise
    elif dist_liq < min_dist_liquidacao_pct:
        analise.aprovado = False
        analise.motivo = (
            f"OPERAÇÃO REJEITADA: liquidação a {dist_liq:.2f}% da entrada, "
            f"abaixo do piso de {min_dist_liquidacao_pct:.1f}% exigido quando "
            f"não há ATR disponível para calibrar. Reduza a alavancagem.")
        return analise

    analise.motivo = (
        f"stop a {dist_stop:.2f}% e liquidação a {dist_liq:.2f}% da entrada; "
        f"folga de {folga:.0%} do caminho"
        + (f"; liquidação a {em_atrs:.1f} ATRs" if em_atrs is not None else ""))
    return analise


def sugerir_alavancagem_por_atr(atr_pct: float, mmr: float = MMR_PADRAO,
                                min_atr: float = MIN_ATR_ATE_LIQUIDACAO
                                ) -> float:
    """Maior alavancagem que mantém a liquidação a `min_atr` ATRs da entrada."""
    if atr_pct <= 0:
        return 1.0
    dist_necessaria = (atr_pct * min_atr) / 100.0
    denom = dist_necessaria + mmr
    return max(1.0, 1.0 / denom) if denom > 0 else 1.0


def sugerir_alavancagem(dist_stop_pct: float, mmr: float = MMR_PADRAO,
                        folga_minima: float = FOLGA_MINIMA) -> float:
    """Maior alavancagem que ainda respeita a folga exigida.

    Inverte a relação: dado onde o stop precisa ficar (definido pela
    estrutura do mercado, não pela alavancagem desejada), calcula o teto de
    alavancagem. É a ordem correta — a alavancagem é consequência do stop.
    """
    if dist_stop_pct <= 0:
        return 1.0
    # Queremos dist_liq >= dist_stop / (1 - folga_minima)
    dist_liq_necessaria = (dist_stop_pct / (1.0 - folga_minima)) / 100.0
    denom = dist_liq_necessaria + mmr
    if denom <= 0:
        return 1.0
    return max(1.0, 1.0 / denom)


def stop_maximo_seguro(entry: float, side: Side, leverage: float,
                       mmr: float = MMR_PADRAO,
                       folga_minima: float = FOLGA_MINIMA) -> float:
    """Stop mais distante que ainda respeita a folga, dada a alavancagem."""
    p_liq = preco_liquidacao(entry, leverage, side, mmr)
    dist_liq = abs(entry - p_liq)
    dist_max = dist_liq * (1.0 - folga_minima)
    return entry - dist_max if side is Side.LONG else entry + dist_max
