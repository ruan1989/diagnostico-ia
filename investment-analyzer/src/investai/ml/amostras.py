"""Construção de amostras de treino: features no instante t, rótulo depois de t.

O rótulo
--------
A pergunta que o modelo aprende a responder é `P(alvo antes do stop)`. O
rótulo vem do método de barreira tripla: a partir da barra seguinte à
decisão, o que aconteceu primeiro?

    alvo atingido      → rótulo 1
    stop atingido      → rótulo 0
    nem um nem outro   → descartado, NÃO rotulado como 0

O descarte importa. Rotular um trade que expirou como "0" ensinaria o modelo
que ficar de lado é igual a perder, o que é falso e enviesa a probabilidade
para baixo justamente nos casos em que o mercado não andou.

A garantia contra lookahead
---------------------------
Duas regras, ambas estruturais em vez de confiadas à atenção de quem lê:

1. as features do índice `i` vêm de `SerieFeatures`, que só usa dados até
   `i` inclusive — propriedade das próprias funções de indicador;
2. o rótulo é avaliado em `velas[i+1:]`, uma fatia que exclui a barra da
   decisão. Usar a barra `i` para rotular olharia dentro da vela que gerou o
   sinal: o máximo dela já é informação do futuro no instante da decisão.

Empate dentro da mesma vela resolve para o stop. Não é pessimismo gratuito:
sem tick data não há como saber a ordem dos toques, e a suposição que
favorece o resultado é a que produz backtest bonito e conta vazia.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..analysis.features import BARRAS_MINIMAS, SerieFeatures
from ..models import Candle, Side

# Nomes das features, na ordem em que entram no vetor. A ordem é parte do
# contrato do modelo: treinar com uma ordem e prever com outra produz
# previsão sem sentido, e nada no formato do vetor denunciaria isso. Por
# isso o modelo guarda esta lista e confere na predição.
FEATURES: tuple[str, ...] = (
    "rsi_norm",            # RSI centrado e escalado para ~[-1, 1]
    "ema_fast_rel",        # distância da EMA rápida ao preço, em ATR
    "ema_slow_rel",
    "ema_trend_rel",
    "macd_hist_atr",       # histograma do MACD em unidades de ATR
    "adx_norm",
    "di_spread",           # (DI+ - DI-) normalizado
    "bb_pos",              # posição no canal de Bollinger, -1 a +1
    "bb_width_norm",
    "atr_pct_norm",
    "volume_ratio_log",
    "ret_lookback_atr",    # retorno recente em unidades de ATR
    "donchian_pos",        # posição no canal de Donchian, -1 a +1
)


def _seguro(valor: float, limite: float = 10.0) -> float:
    """Trunca valores extremos e troca NaN/inf por zero.

    Um indicador degenerado (ATR zero em série travada, por exemplo) pode
    produzir divisão por quase-zero. Deixar isso entrar no treino faria uma
    única barra estranha dominar todos os pesos.
    """
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return 0.0
    if v != v or v in (float("inf"), float("-inf")):
        return 0.0
    return max(-limite, min(limite, v))


def vetor_features(f: Any) -> list[float]:
    """Converte as features de uma barra no vetor que o modelo consome.

    Tudo é normalizado por ATR ou por escala própria, nunca em preço
    absoluto. Um modelo treinado em dólares de BTC não serviria para ETH, e
    pior: pareceria servir.
    """
    close = float(getattr(f, "close", 0.0)) or 1.0
    atr = float(getattr(f, "atr", 0.0))
    # ATR de referência: se o ATR é zero (série sem variação), usa uma
    # fração do preço para não dividir por zero.
    ref = atr if atr > 0 else close * 0.001

    bb_u = float(getattr(f, "bb_upper", close))
    bb_l = float(getattr(f, "bb_lower", close))
    meio_bb = (bb_u + bb_l) / 2.0
    semi_bb = (bb_u - bb_l) / 2.0 or ref

    d_hi = float(getattr(f, "donchian_high", close))
    d_lo = float(getattr(f, "donchian_low", close))
    meio_d = (d_hi + d_lo) / 2.0
    semi_d = (d_hi - d_lo) / 2.0 or ref

    import math
    razao_vol = float(getattr(f, "volume_ratio", 1.0)) or 1.0

    return [
        _seguro((float(getattr(f, "rsi", 50.0)) - 50.0) / 25.0),
        _seguro((close - float(getattr(f, "ema_fast", close))) / ref),
        _seguro((close - float(getattr(f, "ema_slow", close))) / ref),
        _seguro((close - float(getattr(f, "ema_trend", close))) / ref),
        _seguro(float(getattr(f, "macd_hist", 0.0)) / ref),
        _seguro(float(getattr(f, "adx", 0.0)) / 25.0),
        _seguro((float(getattr(f, "di_plus", 0.0))
                 - float(getattr(f, "di_minus", 0.0))) / 25.0),
        _seguro((close - meio_bb) / semi_bb),
        _seguro(float(getattr(f, "bb_width_pct", 0.0)) / 5.0),
        _seguro(float(getattr(f, "atr_pct", 0.0)) / 2.0),
        _seguro(math.log(max(0.05, razao_vol))),
        _seguro(float(getattr(f, "ret_pct_lookback", 0.0)) / 5.0),
        _seguro((close - meio_d) / semi_d),
    ]


@dataclass(slots=True)
class Amostra:
    """Uma observação de treino, com o instante para auditoria temporal."""

    ts: int                       # instante da DECISÃO (início da vela i)
    indice: int
    x: list[float]
    y: int                        # 1 = alvo antes do stop, 0 = stop antes
    symbol: str
    side: str
    barras_ate_desfecho: int
    entry: float
    stop: float
    alvo: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts, "indice": self.indice, "y": self.y,
            "symbol": self.symbol, "side": self.side,
            "barras_ate_desfecho": self.barras_ate_desfecho,
            "entry": self.entry, "stop": self.stop, "alvo": self.alvo,
        }


@dataclass(slots=True)
class ConjuntoAmostras:
    amostras: list[Amostra] = field(default_factory=list)
    descartadas_por_tempo: int = 0
    features: tuple[str, ...] = FEATURES

    def __len__(self) -> int:
        return len(self.amostras)

    @property
    def xs(self) -> list[list[float]]:
        return [a.x for a in self.amostras]

    @property
    def ys(self) -> list[int]:
        return [a.y for a in self.amostras]

    @property
    def taxa_base(self) -> float:
        return (sum(self.ys) / len(self.ys)) if self.amostras else 0.0

    @property
    def intervalo_ts(self) -> tuple[int, int]:
        if not self.amostras:
            return (0, 0)
        return (self.amostras[0].ts, self.amostras[-1].ts)

    def to_dict(self) -> dict[str, Any]:
        ini, fim = self.intervalo_ts
        return {
            "n": len(self.amostras),
            "taxa_base": round(self.taxa_base, 4),
            "descartadas_por_tempo": self.descartadas_por_tempo,
            "features": list(self.features),
            "ts_inicio": ini, "ts_fim": fim,
            "observacao": (
                "Amostras cujo alvo e stop não foram tocados dentro do "
                "horizonte são DESCARTADAS, não rotuladas como perda: "
                "rotulá-las 0 ensinaria que ficar de lado é igual a perder."),
        }


def rotular(velas: Sequence[Candle], i: int, *, side: Side, entry: float,
            stop: float, alvo: float,
            max_barras: int) -> tuple[int | None, int]:
    """Aplica a barreira tripla a partir da barra SEGUINTE a `i`.

    Devolve `(rótulo, barras)`, com rótulo None quando nem alvo nem stop
    foram tocados no horizonte.
    """
    long = side is Side.LONG
    posteriores = velas[i + 1: i + 1 + max_barras]
    for n, v in enumerate(posteriores, start=1):
        bateu_stop = v.low <= stop if long else v.high >= stop
        bateu_alvo = v.high >= alvo if long else v.low <= alvo
        # Empate na mesma vela resolve para o stop: sem tick data não há como
        # saber a ordem, e supor o favorável produz backtest bonito.
        if bateu_stop:
            return 0, n
        if bateu_alvo:
            return 1, n
    return None, len(posteriores)


def montar_amostras(symbol: str, timeframe: str, velas: Sequence[Candle], *,
                    side: Side = Side.LONG,
                    atr_stop: float = 1.5,
                    rr: float = 2.0,
                    max_barras: int = 48,
                    passo: int = 1) -> ConjuntoAmostras:
    """Constrói o conjunto de treino percorrendo a série uma vez.

    O plano de cada amostra (stop a `atr_stop` ATRs e alvo a `rr` vezes o
    risco) é o mesmo que o gerador de sinais usa, para que a probabilidade
    aprendida se refira ao trade que o sistema de fato faria — e não a um
    trade hipotético com outra geometria.
    """
    conj = ConjuntoAmostras()
    if len(velas) < BARRAS_MINIMAS + max_barras + 2:
        return conj

    serie = SerieFeatures(symbol, timeframe, velas)
    # O último índice rotulável precisa de `max_barras` barras à frente.
    ultimo = len(velas) - max_barras - 1
    for i in range(serie.primeiro_indice_valido, ultimo, max(1, passo)):
        f = serie.at(i)
        atr = float(getattr(f, "atr", 0.0))
        if atr <= 0:
            continue
        entry = float(f.close)
        risco = atr * atr_stop
        if side is Side.LONG:
            stop = entry - risco
            alvo = entry + risco * rr
        else:
            stop = entry + risco
            alvo = entry - risco * rr
        if stop <= 0 or alvo <= 0:
            continue

        y, barras = rotular(velas, i, side=side, entry=entry, stop=stop,
                            alvo=alvo, max_barras=max_barras)
        if y is None:
            conj.descartadas_por_tempo += 1
            continue
        conj.amostras.append(Amostra(
            ts=velas[i].ts, indice=i, x=vetor_features(f), y=y,
            symbol=symbol, side=side.value, barras_ate_desfecho=barras,
            entry=entry, stop=stop, alvo=alvo))
    return conj


def dividir_no_tempo(conj: ConjuntoAmostras, *, frac_treino: float = 0.7
                     ) -> tuple[ConjuntoAmostras, ConjuntoAmostras]:
    """Separa treino e teste por TEMPO, nunca por sorteio.

    Sortear observações misturaria passado e futuro dentro do mesmo trade e
    dentro da mesma janela de indicador. O modelo aprenderia com barras
    vizinhas às que vai prever, e a métrica out-of-sample ficaria otimista
    sem que nada no código parecesse errado.
    """
    if not 0.0 < frac_treino < 1.0:
        raise ValueError("frac_treino deve estar entre 0 e 1")
    ordenadas = sorted(conj.amostras, key=lambda a: a.ts)
    corte = int(len(ordenadas) * frac_treino)
    treino = ConjuntoAmostras(ordenadas[:corte], features=conj.features)
    teste = ConjuntoAmostras(ordenadas[corte:], features=conj.features)
    return treino, teste


__all__ = [
    "Amostra", "ConjuntoAmostras", "FEATURES", "dividir_no_tempo",
    "montar_amostras", "rotular", "vetor_features",
]
