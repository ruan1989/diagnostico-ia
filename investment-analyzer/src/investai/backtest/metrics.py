"""Métricas de desempenho calculadas a partir das operações encerradas."""
from __future__ import annotations

from math import sqrt
from typing import Sequence

from ..models import BacktestStats, Trade

# Valor reportado quando não houve nenhuma operação perdedora na amostra.
PF_SEM_PERDAS = 99.0


def max_drawdown_pct(equity: Sequence[float]) -> float:
    """Maior queda percentual do pico ao vale da curva de capital."""
    if not equity:
        return 0.0
    pico = equity[0]
    pior = 0.0
    for v in equity:
        pico = max(pico, v)
        if pico > 0:
            pior = max(pior, (pico - v) / pico * 100.0)
    return pior


def sharpe_ratio(retornos: Sequence[float], periodos_por_ano: float = 365 * 24) -> float:
    """Sharpe anualizado sobre retornos por operação (taxa livre de risco = 0).

    Com poucas operações o número é instável; por isso exige amostra mínima.
    """
    n = len(retornos)
    if n < 5:
        return 0.0
    media = sum(retornos) / n
    var = sum((r - media) ** 2 for r in retornos) / (n - 1)
    sd = sqrt(var)
    # Comparar sd com zero exato não funciona: somar e subtrair floats iguais
    # deixa resíduo da ordem de 1e-18, e dividir por ele produziria um Sharpe
    # gigante a partir de uma série sem nenhuma variação real.
    escala = max(abs(media), 1e-12)
    if sd <= escala * 1e-9:
        return 0.0
    # Escala pela raiz do número de operações por ano estimado (conservador:
    # usa a própria frequência observada, não a frequência de barras).
    return (media / sd) * sqrt(min(periodos_por_ano, n * 12))


def maior_sequencia_perdas(trades: Sequence[Trade]) -> int:
    pior = atual = 0
    for t in trades:
        if t.pnl_usd < 0:
            atual += 1
            pior = max(pior, atual)
        else:
            atual = 0
    return pior


def calcular(trades: Sequence[Trade], equity: Sequence[float],
             capital_inicial: float) -> BacktestStats:
    """Consolida as estatísticas. Sem operações, devolve zeros — nunca estima."""
    if not trades:
        return BacktestStats(equity_final=capital_inicial)

    ganhos = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    perdas = [t.pnl_usd for t in trades if t.pnl_usd <= 0]
    lucro_bruto = sum(ganhos)
    prejuizo_bruto = abs(sum(perdas))
    n = len(trades)

    # Profit factor é uma RAZÃO. Sem nenhuma operação perdedora ela é
    # matematicamente infinita — o que significa "amostra pequena demais",
    # não "estratégia perfeita". Reportamos o teto PF_SEM_PERDAS para deixar
    # explícito que o número é um marcador, não uma medição. O portão de
    # `min_trades_historico` é o que impede esse caso de virar sinal operável.
    if prejuizo_bruto > 0:
        pf = lucro_bruto / prejuizo_bruto
    elif lucro_bruto > 0:
        pf = PF_SEM_PERDAS
    else:
        pf = 0.0

    retornos_r = [t.pnl_r for t in trades]
    return BacktestStats(
        trades=n,
        win_rate=len(ganhos) / n,
        profit_factor=pf,
        expectancy_r=sum(retornos_r) / n,
        max_drawdown_pct=max_drawdown_pct(equity),
        sharpe=sharpe_ratio([t.pnl_usd / capital_inicial for t in trades]),
        avg_bars_held=sum(t.bars_held for t in trades) / n,
        gross_profit=lucro_bruto,
        gross_loss=prejuizo_bruto,
        net_pnl=sum(t.pnl_usd for t in trades),
        equity_final=equity[-1] if equity else capital_inicial,
        fees_paid=sum(t.fees_usd for t in trades),
        largest_loss_r=min(retornos_r) if retornos_r else 0.0,
        consecutive_losses=maior_sequencia_perdas(trades),
    )
