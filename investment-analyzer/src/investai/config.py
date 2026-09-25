"""Configuração central. Nada de segredo em código — tudo por ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Any


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "sim", "on"}


@dataclass(slots=True)
class RiskConfig:
    """Limites de risco. Existem para impedir ruína, não para maximizar lucro."""

    risco_por_trade_pct: float = 0.5        # % do capital arriscado por entrada
    perda_diaria_max_pct: float = 2.0       # circuit breaker diário
    perda_semanal_max_pct: float = 5.0
    drawdown_max_pct: float = 10.0          # desliga o robô
    max_posicoes_simultaneas: int = 3
    max_exposicao_notional_pct: float = 300.0   # soma de notional / capital
    alavancagem_max: float = 5.0
    perdas_consecutivas_max: int = 4        # pausa após sequência ruim
    correlacao_max_posicoes: float = 0.85   # evita 3 posições no mesmo "trade"
    min_risk_reward: float = 1.8
    cooldown_minutos_pos_stop: int = 60

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SignalConfig:
    """Portões de qualidade. Quanto mais restritivo, menos sinais e melhores."""

    score_min_grade_a: float = 78.0
    score_min_grade_b: float = 66.0
    score_min_operavel: float = 66.0        # abaixo disso não executa
    min_trades_historico: int = 20           # amostra mínima do backtest
    min_win_rate_historico: float = 0.50
    min_profit_factor_historico: float = 1.35
    min_expectancy_r: float = 0.15
    max_atr_pct: float = 9.0                 # evita volatilidade extrema
    min_volume_24h_usd: float = 20_000_000.0
    max_funding_abs: float = 0.0012          # funding muito alto = crowded trade
    # Escada de alvos em múltiplos de risco (R). O PRIMEIRO alvo precisa ser
    # >= RiskConfig.min_risk_reward, senão a gestão de risco recusaria todo
    # sinal que o próprio motor gera — `Settings.validar` cobre isso.
    alvos_r: tuple[float, ...] = (1.8, 3.0, 4.5)
    atr_mult_stop: float = 1.6
    timeframes: tuple[str, ...] = ("15m", "1H", "4H")
    timeframe_principal: str = "1H"
    exigir_alinhamento_multi_tf: bool = True


@dataclass(slots=True)
class ExecutionConfig:
    modo: str = "paper"                      # paper | live
    taxa_taker_pct: float = 0.06             # Bitget futures taker ~0.06%
    taxa_maker_pct: float = 0.02
    slippage_pct: float = 0.03
    capital_inicial_usd: float = 1000.0
    intervalo_scan_segundos: int = 300
    margin_coin: str = "USDT"
    product_type: str = "USDT-FUTURES"
    margin_mode: str = "isolated"
    parciais: tuple[float, ...] = (0.5, 0.3, 0.2)   # fração fechada em cada TP
    trailing_apos_tp1: bool = True
    mover_stop_breakeven_em_r: float = 1.0


@dataclass(slots=True)
class Settings:
    risk: RiskConfig = field(default_factory=RiskConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    exec: ExecutionConfig = field(default_factory=ExecutionConfig)
    data_dir: str = "data"
    db_path: str = "data/investai.db"
    universo: tuple[str, ...] = (
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOGEUSDT", "TONUSDT",
        "ARBUSDT", "OPUSDT", "APTUSDT", "SUIUSDT", "NEARUSDT",
        "INJUSDT", "ATOMUSDT", "DOTUSDT", "LTCUSDT", "MATICUSDT",
    )
    fii_universo: tuple[str, ...] = (
        "MXRF11", "HGLG11", "XPML11", "KNRI11", "VISC11", "BTLG11",
        "HGRU11", "XPLG11", "VGIP11", "RECT11", "KNCR11", "HSML11",
        "TRXF11", "RBRR11", "CPTS11", "GGRC11", "VILG11", "BCFF11",
    )

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.exec.modo = os.environ.get("INVESTAI_MODE", s.exec.modo).lower()
        s.exec.capital_inicial_usd = _env_float(
            "INVESTAI_CAPITAL_USD", s.exec.capital_inicial_usd)
        s.exec.intervalo_scan_segundos = _env_int(
            "INVESTAI_SCAN_INTERVAL", s.exec.intervalo_scan_segundos)
        s.risk.risco_por_trade_pct = _env_float(
            "INVESTAI_RISK_PER_TRADE_PCT", s.risk.risco_por_trade_pct)
        s.risk.perda_diaria_max_pct = _env_float(
            "INVESTAI_MAX_DAILY_LOSS_PCT", s.risk.perda_diaria_max_pct)
        s.risk.drawdown_max_pct = _env_float(
            "INVESTAI_MAX_DRAWDOWN_PCT", s.risk.drawdown_max_pct)
        s.risk.alavancagem_max = _env_float(
            "INVESTAI_MAX_LEVERAGE", s.risk.alavancagem_max)
        s.risk.max_posicoes_simultaneas = _env_int(
            "INVESTAI_MAX_POSITIONS", s.risk.max_posicoes_simultaneas)
        s.signal.score_min_operavel = _env_float(
            "INVESTAI_MIN_SCORE", s.signal.score_min_operavel)
        s.data_dir = os.environ.get("INVESTAI_DATA_DIR", s.data_dir)
        s.db_path = os.environ.get("INVESTAI_DB_PATH", os.path.join(s.data_dir, "investai.db"))
        uni = os.environ.get("INVESTAI_UNIVERSE")
        if uni:
            s.universo = tuple(x.strip().upper() for x in uni.split(",") if x.strip())
        if s.exec.modo not in {"paper", "live"}:
            raise ValueError(f"INVESTAI_MODE inválido: {s.exec.modo!r} (use paper|live)")
        s.validar()
        return s

    def validar(self) -> None:
        """Checa coerência entre blocos de configuração.

        Estes erros são silenciosos e caros: um primeiro alvo menor que o R:R
        mínimo exigido faz o motor gerar sinais que a gestão de risco recusa
        100% das vezes — o sistema parece funcionar e nunca opera.
        """
        alvos = self.signal.alvos_r
        if not alvos:
            raise ValueError("signal.alvos_r não pode ser vazio")
        if list(alvos) != sorted(alvos):
            raise ValueError(f"signal.alvos_r deve ser crescente: {alvos}")
        if alvos[0] < self.risk.min_risk_reward:
            raise ValueError(
                f"incoerência: primeiro alvo {alvos[0]}R é menor que o mínimo de "
                f"risco/retorno exigido ({self.risk.min_risk_reward}R). "
                f"Aumente signal.alvos_r[0] ou reduza risk.min_risk_reward.")
        if len(self.exec.parciais) > len(alvos):
            raise ValueError(
                f"{len(self.exec.parciais)} frações de saída para apenas "
                f"{len(alvos)} alvos")
        soma = sum(self.exec.parciais)
        if abs(soma - 1.0) > 1e-6:
            raise ValueError(f"exec.parciais deve somar 1.0 (soma atual: {soma})")
        if self.risk.risco_por_trade_pct <= 0 or self.risk.risco_por_trade_pct > 5:
            raise ValueError(
                f"risco_por_trade_pct={self.risk.risco_por_trade_pct} fora da faixa "
                f"sensata (0 a 5%). Acima de 2% por operação, uma sequência de "
                f"perdas normal já compromete a conta.")
        if self.signal.timeframe_principal not in self.signal.timeframes:
            raise ValueError(
                f"timeframe_principal '{self.signal.timeframe_principal}' não está "
                f"em timeframes {self.signal.timeframes}")

    @property
    def is_live(self) -> bool:
        return self.exec.modo == "live"

    def to_dict(self) -> dict[str, Any]:
        return {
            "modo": self.exec.modo,
            "risk": self.risk.to_dict(),
            "signal": {
                "score_min_operavel": self.signal.score_min_operavel,
                "score_min_grade_a": self.signal.score_min_grade_a,
                "min_trades_historico": self.signal.min_trades_historico,
                "min_win_rate_historico": self.signal.min_win_rate_historico,
                "min_profit_factor_historico": self.signal.min_profit_factor_historico,
                "min_expectancy_r": self.signal.min_expectancy_r,
                "timeframes": list(self.signal.timeframes),
                "timeframe_principal": self.signal.timeframe_principal,
                "alvos_r": list(self.signal.alvos_r),
                "atr_mult_stop": self.signal.atr_mult_stop,
                "min_risk_reward": self.risk.min_risk_reward,
            },
            "exec": {
                "capital_inicial_usd": self.exec.capital_inicial_usd,
                "taxa_taker_pct": self.exec.taxa_taker_pct,
                "slippage_pct": self.exec.slippage_pct,
                "intervalo_scan_segundos": self.exec.intervalo_scan_segundos,
                "parciais": list(self.exec.parciais),
            },
            "universo": list(self.universo),
            "fii_universo": list(self.fii_universo),
        }


TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1H": 3_600_000, "4H": 14_400_000, "6H": 21_600_000,
    "12H": 43_200_000, "1D": 86_400_000,
}


def tf_ms(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"timeframe desconhecido: {timeframe}")
    return TIMEFRAME_MS[timeframe]
