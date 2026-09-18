"""Tipos de dados centrais do sistema (puros, sem I/O)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class Regime(str, Enum):
    TENDENCIA_ALTA = "tendencia_alta"
    TENDENCIA_BAIXA = "tendencia_baixa"
    LATERAL = "lateral"
    VOLATIL_SEM_DIRECAO = "volatil_sem_direcao"


class SignalGrade(str, Enum):
    """Classificação de qualidade. Nenhuma delas significa certeza."""

    A = "A"  # confluência alta + estatística histórica válida
    B = "B"  # confluência boa, estatística aceitável
    C = "C"  # observar apenas, não operar automaticamente
    REJEITADO = "rejeitado"


@dataclass(frozen=True, slots=True)
class Candle:
    ts: int  # epoch ms do início do candle
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def dt(self) -> datetime:
        return _utc(self.ts)

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3.0


@dataclass(slots=True)
class MarketSnapshot:
    """Estado de mercado auxiliar de derivativos (não vem do candle)."""

    symbol: str
    last_price: float
    funding_rate: float = 0.0          # taxa por período de funding
    open_interest: float = 0.0
    volume_24h_usd: float = 0.0
    long_short_ratio: float | None = None
    fetched_at: int = 0


@dataclass(slots=True)
class Features:
    """Features calculadas para um símbolo/timeframe."""

    symbol: str
    timeframe: str
    close: float
    ema_fast: float
    ema_slow: float
    ema_trend: float
    rsi: float
    macd: float
    macd_signal: float
    macd_hist: float
    atr: float
    atr_pct: float
    adx: float
    di_plus: float
    di_minus: float
    bb_upper: float
    bb_lower: float
    bb_width_pct: float
    donchian_high: float
    donchian_low: float
    volume_ratio: float          # volume atual / média
    ret_pct_lookback: float      # retorno % na janela
    regime: Regime

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["regime"] = self.regime.value
        return d


@dataclass(slots=True)
class FactorScore:
    """Contribuição individual de um fator ao score final — auditável."""

    nome: str
    peso: float
    valor: float        # -1.0 .. +1.0 (positivo = favorece a direção avaliada)
    detalhe: str = ""

    @property
    def contribuicao(self) -> float:
        return self.peso * self.valor


@dataclass(slots=True)
class BacktestStats:
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0      # expectativa em múltiplos de risco (R)
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    avg_bars_held: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pnl: float = 0.0
    equity_final: float = 0.0
    fees_paid: float = 0.0
    largest_loss_r: float = 0.0
    consecutive_losses: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Signal:
    """Oportunidade de operação com plano completo de entrada e saída."""

    symbol: str
    timeframe: str
    side: Side
    grade: SignalGrade
    score: float                 # 0..100 confluência
    entry: float
    stop_loss: float
    take_profits: list[float]
    risk_reward: float
    atr: float
    regime: Regime
    fatores: list[FactorScore] = field(default_factory=list)
    # Estatística histórica medida (walk-forward) — base da probabilidade
    hist: BacktestStats | None = None
    prob_acerto_estimada: float = 0.0     # derivada do histórico, NÃO garantia
    retorno_esperado_r: float = 0.0       # expectativa em R
    invalidacao: str = ""                 # o que anula a tese
    gerado_em: int = 0
    notas: list[str] = field(default_factory=list)

    @property
    def stop_distance_pct(self) -> float:
        if self.entry == 0:
            return 0.0
        return abs(self.entry - self.stop_loss) / self.entry * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "side": self.side.value,
            "grade": self.grade.value,
            "score": round(self.score, 2),
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profits": self.take_profits,
            "risk_reward": round(self.risk_reward, 2),
            "atr": self.atr,
            "atr_pct": round(self.atr / self.entry * 100, 3) if self.entry else 0.0,
            "stop_distance_pct": round(self.stop_distance_pct, 3),
            "regime": self.regime.value,
            "prob_acerto_estimada": round(self.prob_acerto_estimada, 4),
            "retorno_esperado_r": round(self.retorno_esperado_r, 4),
            "invalidacao": self.invalidacao,
            "gerado_em": self.gerado_em,
            "notas": self.notas,
            "fatores": [
                {
                    "nome": f.nome,
                    "peso": f.peso,
                    "valor": round(f.valor, 3),
                    "contribuicao": round(f.contribuicao, 3),
                    "detalhe": f.detalhe,
                }
                for f in self.fatores
            ],
            "historico": self.hist.to_dict() if self.hist else None,
        }


@dataclass(slots=True)
class Position:
    symbol: str
    side: Side
    size: float            # em unidades do ativo base
    entry: float
    stop_loss: float
    take_profits: list[float]
    opened_at: int
    leverage: float = 1.0
    notional_usd: float = 0.0
    risk_usd: float = 0.0
    client_oid: str = ""
    modo: str = "paper"
    tps_atingidos: int = 0
    trailing_ativo: bool = False

    def unrealized_r(self, price: float) -> float:
        risk = abs(self.entry - self.stop_loss)
        if risk == 0:
            return 0.0
        delta = price - self.entry if self.side is Side.LONG else self.entry - price
        return delta / risk


@dataclass(slots=True)
class Trade:
    """Operação encerrada."""

    symbol: str
    side: Side
    entry: float
    exit: float
    size: float
    opened_at: int
    closed_at: int
    pnl_usd: float
    pnl_r: float
    motivo_saida: str
    fees_usd: float = 0.0
    bars_held: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        return d


@dataclass(slots=True)
class FiiOpportunity:
    """Oportunidade de renda passiva (FII)."""

    ticker: str
    nome: str
    segmento: str
    preco: float
    dy_12m: float               # % ao ano
    p_vp: float
    vacancia_pct: float | None
    liquidez_diaria: float      # R$/dia
    num_imoveis: int | None
    patrimonio_liquido: float | None
    score: float = 0.0
    classificacao: str = ""
    fatores: list[FactorScore] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    renda_mensal_por_1k: float = 0.0
    fonte: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "nome": self.nome,
            "segmento": self.segmento,
            "preco": self.preco,
            "dy_12m": self.dy_12m,
            "p_vp": self.p_vp,
            "vacancia_pct": self.vacancia_pct,
            "liquidez_diaria": self.liquidez_diaria,
            "num_imoveis": self.num_imoveis,
            "patrimonio_liquido": self.patrimonio_liquido,
            "score": round(self.score, 2),
            "classificacao": self.classificacao,
            "renda_mensal_por_1k": round(self.renda_mensal_por_1k, 2),
            "alertas": self.alertas,
            "fonte": self.fonte,
            "fatores": [
                {"nome": f.nome, "peso": f.peso, "valor": round(f.valor, 3),
                 "contribuicao": round(f.contribuicao, 3), "detalhe": f.detalhe}
                for f in self.fatores
            ],
        }
