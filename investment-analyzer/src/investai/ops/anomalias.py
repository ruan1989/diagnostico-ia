"""Detector de anomalias.

Regra central: **anomalia abre investigação, não operação.**

Um volume 8x acima da média pode ser acumulação institucional ou pode ser a
notícia que você ainda não leu. Um funding que triplicou pode ser oportunidade
contrária ou o início de uma cascata de liquidação. O detector não decide qual
é — ele marca o evento, classifica a severidade e, quando a anomalia indica
risco, pede ao motor que NÃO abra posição até entender.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from ..models import Candle


class TipoAnomalia(str, Enum):
    VOLUME = "volume_anormal"
    VOLATILIDADE = "volatilidade_anormal"
    GAP = "gap_de_preco"
    DIVERGENCIA = "divergencia_preco_volume"
    FUNDING = "mudanca_abrupta_de_funding"
    OPEN_INTEREST = "salto_de_open_interest"
    LIQUIDACOES = "onda_de_liquidacoes"
    SPREAD = "spread_anormal"
    LIQUIDEZ = "queda_de_liquidez"
    MOVIMENTO_INCOMPATIVEL = "movimento_incompativel_com_o_historico"


class Severidade(str, Enum):
    INFO = "info"
    ATENCAO = "atencao"
    ALTA = "alta"
    CRITICA = "critica"


# Anomalias que, por si, bloqueiam abertura de posição nova até investigação.
BLOQUEIAM_ENTRADA = {
    TipoAnomalia.LIQUIDACOES, TipoAnomalia.SPREAD, TipoAnomalia.LIQUIDEZ,
    TipoAnomalia.GAP,
}


@dataclass(slots=True)
class Anomalia:
    tipo: TipoAnomalia
    severidade: Severidade
    symbol: str
    mensagem: str
    valor: float
    referencia: float
    desvios: float = 0.0
    ts: int = 0
    acao_recomendada: str = ""
    metricas: dict[str, Any] = field(default_factory=dict)

    @property
    def bloqueia_entrada(self) -> bool:
        return (self.tipo in BLOQUEIAM_ENTRADA
                and self.severidade in (Severidade.ALTA, Severidade.CRITICA))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tipo": self.tipo.value, "severidade": self.severidade.value,
            "symbol": self.symbol, "mensagem": self.mensagem,
            "valor": round(self.valor, 6),
            "referencia": round(self.referencia, 6),
            "desvios": round(self.desvios, 2), "ts": self.ts,
            "bloqueia_entrada": self.bloqueia_entrada,
            "acao_recomendada": self.acao_recomendada,
            "metricas": self.metricas,
        }


@dataclass(slots=True)
class LimiaresAnomalia:
    volume_desvios: float = 3.0
    volatilidade_desvios: float = 3.0
    gap_pct: float = 1.5
    funding_variacao_relativa: float = 2.0
    oi_variacao_pct: float = 20.0
    spread_desvios: float = 3.0
    queda_liquidez_pct: float = 60.0
    liquidacoes_desvios: float = 3.0
    janela: int = 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "volume_desvios": self.volume_desvios,
            "volatilidade_desvios": self.volatilidade_desvios,
            "gap_pct": self.gap_pct,
            "funding_variacao_relativa": self.funding_variacao_relativa,
            "oi_variacao_pct": self.oi_variacao_pct,
            "spread_desvios": self.spread_desvios,
            "queda_liquidez_pct": self.queda_liquidez_pct,
            "janela": self.janela,
        }


def _severidade_por_desvios(desvios: float) -> Severidade:
    if desvios >= 6.0:
        return Severidade.CRITICA
    if desvios >= 4.0:
        return Severidade.ALTA
    if desvios >= 3.0:
        return Severidade.ATENCAO
    return Severidade.INFO


def _zscore_robusto(valores: Sequence[float], atual: float) -> float:
    """Desvios em unidades de MAD, não de desvio-padrão.

    O desvio-padrão é ele mesmo inflado pelos outliers que se quer detectar:
    um pico de volume entra no cálculo do sigma e reduz o próprio z-score.
    O desvio absoluto mediano (MAD) não sofre desse problema.
    """
    if len(valores) < 10:
        return 0.0
    mediana = statistics.median(valores)
    desvios_abs = [abs(v - mediana) for v in valores]
    mad = statistics.median(desvios_abs)
    if mad == 0:
        media = statistics.fmean(valores)
        return 0.0 if media == 0 else abs(atual - media) / max(media, 1e-9)
    # 1.4826 converte MAD em estimativa consistente de sigma na normal.
    return (atual - mediana) / (mad * 1.4826)


def detectar_anomalias(symbol: str, velas: Sequence[Candle], *,
                       derivativos: dict[str, Any] | None = None,
                       historico_derivativos: dict[str, Sequence[float]]
                       | None = None,
                       limiares: LimiaresAnomalia | None = None
                       ) -> list[Anomalia]:
    """Varre a série e os dados de derivativos em busca de eventos atípicos."""
    lim = limiares or LimiaresAnomalia()
    out: list[Anomalia] = []
    if len(velas) < 20:
        return out

    janela = velas[-lim.janela:] if len(velas) > lim.janela else velas
    ts = velas[-1].ts

    # --------------------------------------------------------- volume
    volumes = [c.volume for c in janela[:-1]]
    vol_atual = velas[-1].volume
    if volumes:
        z = _zscore_robusto(volumes, vol_atual)
        if abs(z) >= lim.volume_desvios:
            mediana = statistics.median(volumes)
            out.append(Anomalia(
                TipoAnomalia.VOLUME, _severidade_por_desvios(abs(z)), symbol,
                f"volume de {vol_atual:,.0f} está {abs(z):.1f} desvios "
                f"{'acima' if z > 0 else 'abaixo'} da mediana "
                f"({mediana:,.0f})",
                vol_atual, mediana, z, ts,
                acao_recomendada=(
                    "INVESTIGAR antes de operar: volume atípico pode ser "
                    "fluxo institucional ou reação a notícia ainda não lida. "
                    "A anomalia não indica direção."
                    if z > 0 else
                    "volume muito abaixo do normal: liquidez reduzida piora "
                    "a execução e o stop")))

    # ---------------------------------------------------- volatilidade
    amplitudes = [(c.high - c.low) / c.close * 100.0
                  for c in janela[:-1] if c.close]
    if amplitudes and velas[-1].close:
        atual = (velas[-1].high - velas[-1].low) / velas[-1].close * 100.0
        z = _zscore_robusto(amplitudes, atual)
        if z >= lim.volatilidade_desvios:
            out.append(Anomalia(
                TipoAnomalia.VOLATILIDADE, _severidade_por_desvios(z), symbol,
                f"amplitude de {atual:.2f}% está {z:.1f} desvios acima da "
                f"mediana ({statistics.median(amplitudes):.2f}%)",
                atual, statistics.median(amplitudes), z, ts,
                acao_recomendada="reduzir tamanho: stops calibrados para a "
                                 "volatilidade normal viram ruído aqui"))

    # ------------------------------------------------------------ gap
    if len(velas) >= 2 and velas[-2].close:
        gap = (velas[-1].open - velas[-2].close) / velas[-2].close * 100.0
        if abs(gap) >= lim.gap_pct:
            desvios = abs(gap) / lim.gap_pct * 3.0
            out.append(Anomalia(
                TipoAnomalia.GAP, _severidade_por_desvios(desvios), symbol,
                f"abertura {gap:+.2f}% distante do fechamento anterior: "
                f"movimento em que o stop não teria sido executado no preço "
                f"definido",
                gap, 0.0, desvios, ts,
                acao_recomendada="NÃO abrir posição até entender a causa: "
                                 "gap indica evento, e o stop não protege "
                                 "contra gap"))

    # ------------------------------------------ divergência preço/volume
    if len(janela) >= 20:
        closes = [c.close for c in janela]
        ret = (closes[-1] / closes[-6] - 1.0) * 100.0 if closes[-6] else 0.0
        vol_recente = statistics.fmean([c.volume for c in janela[-5:]])
        vol_antes = statistics.fmean([c.volume for c in janela[-25:-5]])
        if vol_antes > 0:
            razao = vol_recente / vol_antes
            if abs(ret) >= 3.0 and razao < 0.6:
                out.append(Anomalia(
                    TipoAnomalia.DIVERGENCIA, Severidade.ATENCAO, symbol,
                    f"movimento de {ret:+.2f}% com volume {razao:.2f}x o "
                    f"período anterior: preço andando sem participação",
                    razao, 1.0, 3.0, ts,
                    acao_recomendada="movimento sem volume tende a ser "
                                     "revertido; exigir confirmação"))

    # ----------------------------------------------------- derivativos
    deriv = derivativos or {}
    hist = historico_derivativos or {}

    funding = deriv.get("funding_rate")
    hist_funding = hist.get("funding_rate")
    if funding is not None and hist_funding and len(hist_funding) >= 10:
        mediana = statistics.median([abs(f) for f in hist_funding])
        if mediana > 0 and abs(funding) / mediana >= lim.funding_variacao_relativa:
            razao = abs(funding) / mediana
            out.append(Anomalia(
                TipoAnomalia.FUNDING,
                Severidade.ALTA if razao >= 4 else Severidade.ATENCAO, symbol,
                f"funding de {funding * 100:+.4f}% é {razao:.1f}x a mediana "
                f"recente ({mediana * 100:.4f}%): posicionamento aglomerado",
                funding, mediana, razao, ts,
                acao_recomendada="funding extremo antecede cascata de "
                                 "liquidação; entrar do lado da multidão é "
                                 "entrar no fim da fila"))

    oi_var = deriv.get("open_interest_variacao_pct")
    if oi_var is not None and abs(oi_var) >= lim.oi_variacao_pct:
        desvios = abs(oi_var) / lim.oi_variacao_pct * 3.0
        out.append(Anomalia(
            TipoAnomalia.OPEN_INTEREST, _severidade_por_desvios(desvios),
            symbol,
            f"open interest variou {oi_var:+.1f}%: entrada ou saída rápida de "
            f"alavancagem",
            oi_var, 0.0, desvios, ts,
            acao_recomendada="alavancagem nova concentrada é combustível de "
                             "cascata; verificar funding e liquidações"))

    liq = deriv.get("liquidacoes_24h_usd")
    hist_liq = hist.get("liquidacoes_24h_usd")
    if liq is not None and hist_liq and len(hist_liq) >= 10:
        z = _zscore_robusto(list(hist_liq), liq)
        if z >= lim.liquidacoes_desvios:
            out.append(Anomalia(
                TipoAnomalia.LIQUIDACOES, _severidade_por_desvios(z), symbol,
                f"liquidações de US$ {liq:,.0f} em 24h, {z:.1f} desvios acima "
                f"do normal",
                liq, statistics.median(hist_liq), z, ts,
                acao_recomendada="onda de liquidação em curso: preço está "
                                 "sendo movido por fechamento forçado, não "
                                 "por fluxo com tese. NÃO abrir posição."))

    spread = deriv.get("spread_pct")
    hist_spread = hist.get("spread_pct")
    if spread is not None and hist_spread and len(hist_spread) >= 10:
        z = _zscore_robusto(list(hist_spread), spread)
        if z >= lim.spread_desvios:
            out.append(Anomalia(
                TipoAnomalia.SPREAD, _severidade_por_desvios(z), symbol,
                f"spread de {spread:.3f}% está {z:.1f} desvios acima do "
                f"normal ({statistics.median(hist_spread):.3f}%)",
                spread, statistics.median(hist_spread), z, ts,
                acao_recomendada="o custo de entrar e sair consome a "
                                 "vantagem esperada; aguardar normalização"))

    vol_24h = deriv.get("volume_24h_usd")
    hist_vol = hist.get("volume_24h_usd")
    if vol_24h is not None and hist_vol and len(hist_vol) >= 10:
        mediana = statistics.median(hist_vol)
        if mediana > 0:
            queda = (1.0 - vol_24h / mediana) * 100.0
            if queda >= lim.queda_liquidez_pct:
                out.append(Anomalia(
                    TipoAnomalia.LIQUIDEZ,
                    Severidade.ALTA if queda >= 75 else Severidade.ATENCAO,
                    symbol,
                    f"liquidez caiu {queda:.0f}% em relação à mediana "
                    f"recente",
                    vol_24h, mediana, queda / lim.queda_liquidez_pct * 3.0,
                    ts,
                    acao_recomendada="mercado sem liquidez: a saída pode não "
                                     "existir no preço do stop"))

    return sorted(out, key=lambda a: abs(a.desvios), reverse=True)


@dataclass(slots=True)
class RelatorioAnomalias:
    symbol: str
    anomalias: list[Anomalia] = field(default_factory=list)

    @property
    def bloqueia_entrada(self) -> bool:
        return any(a.bloqueia_entrada for a in self.anomalias)

    @property
    def pior_severidade(self) -> Severidade:
        ordem = {Severidade.INFO: 0, Severidade.ATENCAO: 1,
                 Severidade.ALTA: 2, Severidade.CRITICA: 3}
        if not self.anomalias:
            return Severidade.INFO
        return max((a.severidade for a in self.anomalias),
                   key=lambda s: ordem[s])

    def motivos_de_bloqueio(self) -> list[str]:
        return [a.mensagem for a in self.anomalias if a.bloqueia_entrada]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "total": len(self.anomalias),
            "bloqueia_entrada": self.bloqueia_entrada,
            "pior_severidade": self.pior_severidade.value,
            "motivos_de_bloqueio": self.motivos_de_bloqueio(),
            "anomalias": [a.to_dict() for a in self.anomalias],
            "observacao": "anomalia abre investigação, não operação: o "
                          "detector marca o evento e não afirma direção",
        }
