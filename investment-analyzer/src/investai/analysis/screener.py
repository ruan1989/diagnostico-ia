"""Varredura do universo de criptos.

Para cada símbolo:
  1. busca candles em todos os timeframes configurados;
  2. mede a estatística histórica de LONG e de SHORT separadamente, via
     backtest walk-forward no timeframe principal;
  3. calcula a confluência atual nos dois sentidos;
  4. anexa ao sinal a estatística da direção correspondente — é ela que
     decide se o sinal pode virar ordem.

O passo 2 é o que diferencia este screener de um "indicador bonito": um score
alto num par cuja estratégia historicamente perde dinheiro é rebaixado para
observação, não promovido a entrada.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ..backtest.engine import rodar_backtest
from ..config import Settings
from ..datahub import DataHub
from ..models import (
    BacktestStats, Features, MarketSnapshot, Side, Signal, SignalGrade,
)
from .confluence import avaliar
from .features import BARRAS_MINIMAS, DadosInsuficientes, SerieFeatures

log = logging.getLogger("investai.screener")

# Histórico usado para medir a estatística. Quanto maior, mais confiável a
# amostra — e mais lenta a varredura.
BARRAS_HISTORICO = 6000


@dataclass(slots=True)
class AnaliseSimbolo:
    symbol: str
    features: dict[str, Features] = field(default_factory=dict)
    snapshot: MarketSnapshot | None = None
    sinal_long: Signal | None = None
    sinal_short: Signal | None = None
    hist_long: BacktestStats | None = None
    hist_short: BacktestStats | None = None
    erro: str = ""

    @property
    def melhor(self) -> Signal | None:
        candidatos = [s for s in (self.sinal_long, self.sinal_short) if s]
        if not candidatos:
            return None
        return max(candidatos, key=lambda s: s.score)

    def to_dict(self) -> dict:
        principal = next(iter(self.features.values()), None)
        return {
            "symbol": self.symbol,
            "erro": self.erro,
            "preco": principal.close if principal else 0.0,
            "regime": principal.regime.value if principal else "",
            "atr_pct": round(principal.atr_pct, 3) if principal else 0.0,
            "rsi": round(principal.rsi, 1) if principal else 0.0,
            "adx": round(principal.adx, 1) if principal else 0.0,
            "volume_24h_usd": self.snapshot.volume_24h_usd if self.snapshot else 0.0,
            "funding_rate": self.snapshot.funding_rate if self.snapshot else 0.0,
            "features_por_tf": {tf: f.to_dict() for tf, f in self.features.items()},
            "sinal_long": self.sinal_long.to_dict() if self.sinal_long else None,
            "sinal_short": self.sinal_short.to_dict() if self.sinal_short else None,
            "melhor": self.melhor.to_dict() if self.melhor else None,
        }


@dataclass(slots=True)
class ResultadoScan:
    executado_em: int
    analises: list[AnaliseSimbolo] = field(default_factory=list)
    duracao_s: float = 0.0

    @property
    def operaveis(self) -> list[Signal]:
        """Sinais que passaram nos dois portões (técnico e estatístico)."""
        out = [
            s for a in self.analises
            if (s := a.melhor) and s.grade in (SignalGrade.A, SignalGrade.B)
        ]
        out.sort(key=lambda s: (s.retorno_esperado_r, s.score), reverse=True)
        return out

    @property
    def observacao(self) -> list[Signal]:
        out = [s for a in self.analises if (s := a.melhor) and s.grade is SignalGrade.C]
        out.sort(key=lambda s: s.score, reverse=True)
        return out

    def to_dict(self) -> dict:
        return {
            "executado_em": self.executado_em,
            "duracao_s": round(self.duracao_s, 2),
            "total_analisado": len(self.analises),
            "total_operavel": len(self.operaveis),
            "operaveis": [s.to_dict() for s in self.operaveis],
            "observacao": [s.to_dict() for s in self.observacao],
            "analises": [a.to_dict() for a in self.analises],
        }


class Screener:
    def __init__(self, hub: DataHub, settings: Settings):
        self.hub = hub
        self.settings = settings
        # Estatística histórica é cara de calcular e muda devagar: vale cache.
        self._cache_hist: dict[str, tuple[float, BacktestStats]] = {}
        self.ttl_hist_s = 3600.0

    # ---------------------------------------------------------------- histórico
    def estatistica_historica(self, symbol: str, timeframe: str, side: Side,
                              *, forcar: bool = False) -> BacktestStats | None:
        chave = f"{symbol}:{timeframe}:{side.value}"
        agora = time.monotonic()
        if not forcar and (cached := self._cache_hist.get(chave)):
            if agora - cached[0] < self.ttl_hist_s:
                return cached[1]
        try:
            velas = self.hub.historico(symbol, timeframe, barras=BARRAS_HISTORICO)
            if len(velas) < BARRAS_MINIMAS + 50:
                return None
            snap = self.hub.ticker(symbol)
            resultado = rodar_backtest(
                symbol, timeframe, velas,
                self.settings.signal, self.settings.exec,
                side_filtro=side, snapshot=snap,
                funding_rate=snap.funding_rate if snap else 0.0001,
            )
        except DadosInsuficientes as exc:
            log.info("histórico insuficiente para %s: %s", chave, exc)
            return None
        except Exception as exc:                        # noqa: BLE001
            log.warning("backtest de %s falhou: %s", chave, exc)
            return None
        self._cache_hist[chave] = (agora, resultado.stats)
        return resultado.stats

    # ------------------------------------------------------------------ análise
    def analisar(self, symbol: str, *, snapshot: MarketSnapshot | None = None,
                 com_historico: bool = True) -> AnaliseSimbolo:
        cfg = self.settings.signal
        analise = AnaliseSimbolo(symbol=symbol)
        analise.snapshot = snapshot if snapshot is not None else self.hub.ticker(symbol)

        try:
            for tf in cfg.timeframes:
                velas = self.hub.candles(symbol, tf, limit=max(400, BARRAS_MINIMAS + 50))
                analise.features[tf] = SerieFeatures(symbol, tf, velas).at(len(velas) - 1)
        except DadosInsuficientes as exc:
            analise.erro = str(exc)
            return analise
        except Exception as exc:                        # noqa: BLE001
            analise.erro = f"falha ao obter dados: {exc}"
            return analise

        if com_historico:
            analise.hist_long = self.estatistica_historica(
                symbol, cfg.timeframe_principal, Side.LONG)
            analise.hist_short = self.estatistica_historica(
                symbol, cfg.timeframe_principal, Side.SHORT)

        agora = int(time.time() * 1000)
        analise.sinal_long = avaliar(symbol, analise.features, Side.LONG, cfg,
                                     snapshot=analise.snapshot,
                                     hist=analise.hist_long, agora_ms=agora)
        analise.sinal_short = avaliar(symbol, analise.features, Side.SHORT, cfg,
                                      snapshot=analise.snapshot,
                                      hist=analise.hist_short, agora_ms=agora)
        return analise

    def scan(self, symbols: list[str] | None = None, *,
             com_historico: bool = True) -> ResultadoScan:
        inicio = time.time()
        alvos = symbols or list(self.settings.universo)
        snapshots = self.hub.tickers(alvos)
        analises: list[AnaliseSimbolo] = []
        for sym in alvos:
            try:
                analises.append(self.analisar(
                    sym, snapshot=snapshots.get(sym), com_historico=com_historico))
            except Exception as exc:                    # noqa: BLE001
                # Um símbolo problemático não pode interromper a varredura.
                log.warning("análise de %s falhou: %s", sym, exc)
                analises.append(AnaliseSimbolo(symbol=sym, erro=str(exc)))
        return ResultadoScan(
            executado_em=int(time.time() * 1000), analises=analises,
            duracao_s=time.time() - inicio,
        )
