"""Orquestrador do pipeline de validação e promoção.

Liga as peças: roda walk-forward, Monte Carlo e detecção de overfitting,
monta a evidência, consulta o gate e registra o resultado na versão da
estratégia — aprovando ou não.

Não existe atalho aqui. A função `avaliar_para_promocao` é o único caminho
pelo qual uma estratégia muda de fase, e ela sempre grava o resultado do gate
no histórico da versão, aprovada ou reprovada.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ..models import BacktestStats, Candle, Trade
from ..validation.montecarlo import monte_carlo
from ..validation.overfit import avaliar_overfitting
from ..validation.stats import calcular_ev
from ..validation.walkforward import RelatorioWalkForward, walk_forward
from .promotion import CriteriosPromocao, EvidenciaFase, ResultadoGate, avaliar_gate
from .registry import Fase, StrategyRegistry

log = logging.getLogger("investai.pipeline")

RunnerFn = Callable[[Sequence[Candle], dict[str, Any]],
                    tuple[BacktestStats, list[Trade]]]


@dataclass(slots=True)
class RelatorioValidacao:
    """Pacote completo de evidência produzida pela validação."""

    chave_estrategia: str
    walk_forward: RelatorioWalkForward | None = None
    evidencia: EvidenciaFase = field(default_factory=EvidenciaFase)
    gate: ResultadoGate | None = None
    # `promovida` significa "avançou ao menos uma fase nesta execução", NÃO
    # "passou em tudo". Uma estratégia pode subir de rascunho para
    # out_of_sample e ser barrada no gate seguinte: as duas coisas são
    # verdadeiras ao mesmo tempo. Quem precisa do veredicto da última
    # avaliação usa `gate.aprovado` — por isso ele vai explícito no payload,
    # em vez de deixar a interface inferir sucesso de `promovida`.
    promovida: bool = False
    fases_avancadas: list[str] = field(default_factory=list)
    fase_final: str = ""
    avisos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        ev = self.evidencia
        return {
            "chave_estrategia": self.chave_estrategia,
            "promovida": self.promovida,
            "fases_avancadas": self.fases_avancadas,
            "gate_aprovado": self.gate.aprovado if self.gate else False,
            "fase_final": self.fase_final,
            "gate": self.gate.to_dict() if self.gate else None,
            "walk_forward": (self.walk_forward.to_dict()
                             if self.walk_forward else None),
            "evidencia": {
                "stats_backtest": (ev.stats_backtest.to_dict()
                                   if ev.stats_backtest else None),
                "stats_oos": ev.stats_oos.to_dict() if ev.stats_oos else None,
                "degradacao_expectancy": ev.degradacao_expectancy,
                "ev_oos": ev.ev_oos.to_dict() if ev.ev_oos else None,
                "overfit": ev.overfit.to_dict() if ev.overfit else None,
                "monte_carlo": (ev.monte_carlo.to_dict()
                                if ev.monte_carlo else None),
            },
            "avisos": self.avisos,
        }


def validar_estrategia(velas: Sequence[Candle], runner: RunnerFn,
                       parametros: dict[str, Any], *,
                       n_ciclos: int = 4,
                       risco_por_trade_frac: float = 0.005,
                       n_simulacoes_mc: int = 3000,
                       ancorado: bool = True,
                       capital: float = 1000.0) -> EvidenciaFase:
    """Produz a evidência estatística completa de uma estratégia.

    Roda, nesta ordem: backtest completo, walk-forward (que separa IS de OOS),
    Monte Carlo sobre os resultados out-of-sample e detecção de overfitting.

    O Monte Carlo usa **só os trades out-of-sample** de propósito: reamostrar
    resultados in-sample mediria a distribuição de um ajuste, não de uma
    vantagem.
    """
    stats_full, _ = runner(velas, parametros)

    rel = walk_forward(velas, runner, parametros_fixos=parametros,
                       n_ciclos=n_ciclos, ancorado=ancorado, capital=capital)

    retornos_oos = [t.pnl_r for t in rel.trades_oos]
    mc = (monte_carlo(retornos_oos, n_simulacoes=n_simulacoes_mc,
                      risco_por_trade_frac=risco_por_trade_frac)
          if retornos_oos else None)

    overfit = avaliar_overfitting(
        stats_is=rel.stats_in_sample, stats_oos=rel.stats_oos,
        trades_oos=rel.trades_oos,
        stats_por_periodo=[c.stats for c in rel.ciclos],
        n_parametros=sum(1 for v in parametros.values()
                         if isinstance(v, (int, float))
                         and not isinstance(v, bool)))

    return EvidenciaFase(
        stats_backtest=stats_full,
        stats_oos=rel.stats_oos,
        degradacao_expectancy=rel.degradacao.get("expectancy_r"),
        ev_oos=calcular_ev(retornos_oos) if retornos_oos else None,
        overfit=overfit,
        monte_carlo=mc,
    ), rel                                            # type: ignore[return-value]


def avaliar_para_promocao(registry: StrategyRegistry, chave: str,
                          evidencia: EvidenciaFase, *,
                          criterios: CriteriosPromocao | None = None,
                          promover_se_aprovado: bool = True,
                          agora_ms: int | None = None) -> RelatorioValidacao:
    """Único caminho para mudar a fase de uma estratégia.

    Grava o resultado do gate no histórico da versão, aprovado ou não. Isso
    torna auditável não só o que foi promovido, mas o que foi barrado e por
    quê — informação que desaparece em sistemas que só registram sucesso.
    """
    versao = registry.obter(chave)
    gate = avaliar_gate(versao.fase, evidencia, criterios)
    registry.registrar_gate(chave, gate.to_dict(), agora_ms=agora_ms)

    rel = RelatorioValidacao(
        chave_estrategia=chave, evidencia=evidencia, gate=gate,
        fase_final=versao.fase.value)

    if gate.aprovado and promover_se_aprovado:
        registry.promover(chave, agora_ms=agora_ms)
        rel.promovida = True
        rel.fase_final = versao.fase.value
        rel.fases_avancadas.append(versao.fase.value)
        log.info("estratégia %s promovida para %s", chave, versao.fase.value)
    elif not gate.aprovado:
        rel.avisos.append(
            f"permanece em {versao.fase.value}: {gate.resumo}")

    return rel


def rodar_pipeline_completo(registry: StrategyRegistry,
                            strategy_id: str,
                            parametros: dict[str, Any],
                            velas: Sequence[Candle],
                            runner: RunnerFn, *,
                            criterios: CriteriosPromocao | None = None,
                            timeframe: str = "1H",
                            descricao: str = "",
                            n_ciclos: int = 4,
                            capital: float = 1000.0,
                            risco_por_trade_frac: float = 0.005
                            ) -> RelatorioValidacao:
    """Cria (ou recupera) a versão e a leva até onde a evidência permitir.

    Avança fase por fase enquanto os gates passarem. As fases que dependem de
    execução ao vivo (paper, shadow, assistido) **não** podem ser vencidas por
    backtest: ao chegar nelas, o pipeline para e diz o que falta medir.
    """
    versao = registry.criar(strategy_id, parametros, timeframe=timeframe,
                            descricao=descricao)
    evidencia, wf = validar_estrategia(
        velas, runner, parametros, n_ciclos=n_ciclos, capital=capital,
        risco_por_trade_frac=risco_por_trade_frac)

    relatorio = RelatorioValidacao(
        chave_estrategia=versao.chave, walk_forward=wf, evidencia=evidencia)

    # Avança enquanto houver aprovação e a fase for decidível por backtest.
    fases_offline = (Fase.RASCUNHO, Fase.BACKTEST, Fase.OUT_OF_SAMPLE)
    while versao.fase in fases_offline:
        gate = avaliar_gate(versao.fase, evidencia, criterios)
        registry.registrar_gate(versao.chave, gate.to_dict())
        relatorio.gate = gate
        if not gate.aprovado:
            relatorio.avisos.append(
                f"pipeline parou em {versao.fase.value}: {gate.resumo}")
            break
        registry.promover(versao.chave)
        relatorio.promovida = True
        relatorio.fases_avancadas.append(versao.fase.value)

    relatorio.fase_final = versao.fase.value
    if versao.fase is Fase.PAPER_TRADING:
        relatorio.avisos.append(
            "chegou a paper_trading: as fases seguintes exigem execução em "
            "tempo real e NÃO podem ser vencidas por backtest. É preciso "
            "acumular operações simuladas ao vivo antes de avançar.")
    return relatorio
