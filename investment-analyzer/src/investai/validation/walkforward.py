"""Validação temporal: treino, validação e out-of-sample.

Por que isto não é opcional
---------------------------
Ajustar parâmetros e depois medir o desempenho nos mesmos dados responde à
pergunta errada. A pergunta certa é: *com os parâmetros escolhidos olhando
apenas o passado, como a estratégia se comportou no futuro que ela não viu?*

Este módulo implementa duas coisas:

1. **Split temporal simples** — treino / validação / out-of-sample em blocos
   cronológicos. Nunca aleatório: embaralhar série temporal vaza o futuro para
   o treino e é uma das formas mais comuns de data leakage.
2. **Walk-forward ancorado ou rolante** — repete o ciclo "otimiza numa janela,
   testa na janela seguinte" ao longo de todo o histórico, e agrega só os
   resultados out-of-sample. O número final é a concatenação de trechos em que
   a estratégia estava, a cada momento, operando às cegas.

A degradação entre treino e out-of-sample é a métrica mais informativa do
sistema. Estratégia que ganha no treino e empata fora dele está descrevendo
ruído.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ..models import BacktestStats, Candle, Trade
from .stats import ExpectedValue, calcular_ev, wilson


class SplitError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Janela:
    nome: str
    inicio: int          # índice inicial (inclusivo)
    fim: int             # índice final (exclusivo)

    @property
    def tamanho(self) -> int:
        return self.fim - self.inicio

    def fatiar(self, velas: Sequence[Candle]) -> list[Candle]:
        return list(velas[self.inicio:self.fim])

    def to_dict(self) -> dict[str, Any]:
        return {"nome": self.nome, "inicio": self.inicio, "fim": self.fim,
                "tamanho": self.tamanho}


@dataclass(slots=True)
class SplitTemporal:
    treino: Janela
    validacao: Janela
    out_of_sample: Janela
    total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "treino": self.treino.to_dict(),
            "validacao": self.validacao.to_dict(),
            "out_of_sample": self.out_of_sample.to_dict(),
            "total": self.total,
            "observacao": "divisão cronológica; nunca aleatória, para não "
                          "vazar o futuro para o treino",
        }


def dividir_temporal(n: int, *, frac_treino: float = 0.5,
                     frac_validacao: float = 0.2,
                     barras_aquecimento: int = 210) -> SplitTemporal:
    """Divide uma série em treino / validação / out-of-sample.

    `barras_aquecimento` é reservado no início para os indicadores terem
    valor válido. Sem esse cuidado, a primeira janela mediria a estratégia num
    período em que a EMA de 200 ainda não existe.
    """
    if frac_treino <= 0 or frac_validacao < 0:
        raise SplitError("frações devem ser positivas")
    if frac_treino + frac_validacao >= 1.0:
        raise SplitError(
            f"treino ({frac_treino}) + validação ({frac_validacao}) não deixa "
            f"espaço para out-of-sample")
    util = n - barras_aquecimento
    if util < 90:
        raise SplitError(
            f"série de {n} barras (menos {barras_aquecimento} de aquecimento) "
            f"é curta demais para dividir com sentido")

    fim_treino = barras_aquecimento + int(util * frac_treino)
    fim_validacao = fim_treino + int(util * frac_validacao)
    return SplitTemporal(
        treino=Janela("treino", 0, fim_treino),
        validacao=Janela("validacao", fim_treino - barras_aquecimento,
                         fim_validacao),
        out_of_sample=Janela("out_of_sample", fim_validacao - barras_aquecimento,
                             n),
        total=n,
    )


@dataclass(slots=True)
class ResultadoJanela:
    janela: Janela
    stats: BacktestStats
    trades: list[Trade] = field(default_factory=list)
    parametros: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"janela": self.janela.to_dict(), "stats": self.stats.to_dict(),
                "parametros": self.parametros, "n_trades": len(self.trades)}


@dataclass(slots=True)
class RelatorioWalkForward:
    """Resultado agregado. `oos` é o único número que conta para promoção."""

    modo: str
    ciclos: list[ResultadoJanela] = field(default_factory=list)
    stats_in_sample: BacktestStats = field(default_factory=BacktestStats)
    stats_oos: BacktestStats = field(default_factory=BacktestStats)
    trades_oos: list[Trade] = field(default_factory=list)
    ev_oos: ExpectedValue | None = None
    degradacao: dict[str, float] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)

    @property
    def n_ciclos(self) -> int:
        return len(self.ciclos)

    def to_dict(self) -> dict[str, Any]:
        return {
            "modo": self.modo,
            "n_ciclos": self.n_ciclos,
            "ciclos": [c.to_dict() for c in self.ciclos],
            "stats_in_sample": self.stats_in_sample.to_dict(),
            "stats_oos": self.stats_oos.to_dict(),
            "n_trades_oos": len(self.trades_oos),
            "ev_oos": self.ev_oos.to_dict() if self.ev_oos else None,
            "degradacao": {k: round(v, 4) for k, v in self.degradacao.items()},
            "avisos": self.avisos,
            "observacao": "apenas stats_oos vale para decisão; in-sample é "
                          "referência para medir degradação",
        }


# Assinatura da função que o chamador fornece: recebe as velas de uma janela e
# os parâmetros, e devolve (stats, trades).
RunnerFn = Callable[[Sequence[Candle], dict[str, Any]],
                    tuple[BacktestStats, list[Trade]]]
# Função opcional de otimização: recebe as velas de treino e devolve os
# parâmetros escolhidos. Sem ela, o walk-forward apenas revalida parâmetros
# fixos em janelas sucessivas.
OptimizerFn = Callable[[Sequence[Candle]], dict[str, Any]]


def _agregar(trades: Sequence[Trade], capital: float) -> BacktestStats:
    from ..backtest.metrics import calcular
    equity = [capital]
    acc = capital
    for t in sorted(trades, key=lambda x: x.closed_at):
        acc += t.pnl_usd
        equity.append(acc)
    return calcular(list(trades), equity, capital)


def _degradacao(is_: BacktestStats, oos: BacktestStats) -> dict[str, float]:
    """Quanto o desempenho piorou fora da amostra.

    Valores próximos de 0 são bons. Acima de 0,5 significa que metade do
    resultado desapareceu quando a estratégia deixou de ver os dados que a
    ajustaram — sinal clássico de overfitting.
    """
    def queda(dentro: float, fora: float) -> float:
        if dentro == 0:
            return 0.0 if fora >= 0 else 1.0
        return (dentro - fora) / abs(dentro)

    return {
        "expectancy_r": queda(is_.expectancy_r, oos.expectancy_r),
        "profit_factor": queda(is_.profit_factor, oos.profit_factor),
        "win_rate": queda(is_.win_rate, oos.win_rate),
        "drawdown_piorou": (oos.max_drawdown_pct - is_.max_drawdown_pct)
                           / max(is_.max_drawdown_pct, 1e-9),
    }


def walk_forward(velas: Sequence[Candle], runner: RunnerFn, *,
                 otimizador: OptimizerFn | None = None,
                 parametros_fixos: dict[str, Any] | None = None,
                 n_ciclos: int = 4,
                 barras_aquecimento: int = 210,
                 ancorado: bool = True,
                 capital: float = 1000.0) -> RelatorioWalkForward:
    """Executa walk-forward em `n_ciclos` blocos.

    `ancorado=True`: a janela de treino cresce a partir do início (usa toda a
    história disponível até ali). `ancorado=False`: janela rolante de tamanho
    fixo, que reage mais rápido a mudança de regime e é mais exigente.

    Em cada ciclo, o treino só serve para escolher parâmetros; a medição vem
    exclusivamente do bloco seguinte, que o treino não viu.
    """
    n = len(velas)
    if n_ciclos < 2:
        raise SplitError("walk-forward exige ao menos 2 ciclos")
    util = n - barras_aquecimento
    if util < n_ciclos * 60:
        raise SplitError(
            f"série de {n} barras é curta para {n_ciclos} ciclos; são "
            f"necessárias ~{n_ciclos * 60 + barras_aquecimento} barras")

    bloco = util // (n_ciclos + 1)
    relatorio = RelatorioWalkForward(modo="ancorado" if ancorado else "rolante")
    trades_is: list[Trade] = []
    vistos_is: set[tuple[str, int, Any]] = set()
    vistos_oos: set[tuple[str, int, Any]] = set()

    for ciclo in range(n_ciclos):
        fim_treino = barras_aquecimento + bloco * (ciclo + 1)
        inicio_teste = fim_treino - barras_aquecimento
        fim_teste = min(n, fim_treino + bloco)
        if fim_teste - inicio_teste <= barras_aquecimento:
            break

        inicio_treino = 0 if ancorado else max(
            0, fim_treino - bloco - barras_aquecimento)
        janela_treino = Janela(f"treino_{ciclo + 1}", inicio_treino, fim_treino)
        janela_teste = Janela(f"oos_{ciclo + 1}", inicio_teste, fim_teste)

        velas_treino = janela_treino.fatiar(velas)
        params = dict(parametros_fixos or {})
        if otimizador is not None:
            params.update(otimizador(velas_treino))

        stats_treino, trades_treino = runner(velas_treino, params)
        # Deduplicação é obrigatória com janela ancorada: cada ciclo treina
        # sobre todo o histórico anterior, então as mesmas operações antigas
        # reaparecem em todos os ciclos. Contá-las repetidamente infla o
        # in-sample e faz a degradação parecer maior do que é.
        for t in trades_treino:
            chave = (t.symbol, t.opened_at, t.side)
            if chave not in vistos_is:
                vistos_is.add(chave)
                trades_is.append(t)

        stats_teste, trades_teste = runner(janela_teste.fatiar(velas), params)
        # As janelas de teste carregam barras de aquecimento que se sobrepõem
        # ao bloco anterior, então um trade pode aparecer em dois ciclos.
        # Só o primeiro conta.
        novos_oos = []
        for t in trades_teste:
            chave = (t.symbol, t.opened_at, t.side)
            # Um trade já visto no treino NÃO é out-of-sample, por definição.
            if chave not in vistos_oos and chave not in vistos_is:
                vistos_oos.add(chave)
                novos_oos.append(t)
        relatorio.ciclos.append(ResultadoJanela(
            janela=janela_teste, stats=stats_teste, trades=novos_oos,
            parametros=params))
        relatorio.trades_oos.extend(novos_oos)

    if not relatorio.ciclos:
        relatorio.avisos.append("nenhum ciclo pôde ser executado")
        return relatorio

    relatorio.stats_in_sample = _agregar(trades_is, capital)
    relatorio.stats_oos = _agregar(relatorio.trades_oos, capital)
    relatorio.ev_oos = calcular_ev([t.pnl_r for t in relatorio.trades_oos])
    relatorio.degradacao = _degradacao(relatorio.stats_in_sample,
                                       relatorio.stats_oos)

    # ------------------------------------------------------------- avisos
    oos = relatorio.stats_oos
    if oos.trades == 0:
        relatorio.avisos.append(
            "nenhuma operação out-of-sample — impossível validar")
    elif oos.trades < 30:
        ic = wilson(int(round(oos.win_rate * oos.trades)), oos.trades)
        relatorio.avisos.append(
            f"apenas {oos.trades} operações out-of-sample; taxa de acerto "
            f"entre {ic.inferior:.1%} e {ic.superior:.1%} — amostra "
            f"insuficiente para promoção")
    if relatorio.degradacao.get("expectancy_r", 0) > 0.5:
        relatorio.avisos.append(
            f"expectativa caiu {relatorio.degradacao['expectancy_r']:.0%} do "
            f"treino para o out-of-sample — indício forte de overfitting")
    if oos.expectancy_r <= 0:
        relatorio.avisos.append(
            f"expectativa out-of-sample de {oos.expectancy_r:+.3f}R não é "
            f"positiva: a estratégia não se sustentou fora da amostra")
    ciclos_positivos = sum(1 for c in relatorio.ciclos
                           if c.stats.expectancy_r > 0)
    if relatorio.n_ciclos >= 3 and ciclos_positivos <= relatorio.n_ciclos // 2:
        relatorio.avisos.append(
            f"apenas {ciclos_positivos} de {relatorio.n_ciclos} ciclos com "
            f"expectativa positiva — resultado instável entre períodos")

    return relatorio
