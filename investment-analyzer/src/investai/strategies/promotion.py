"""Gates de promoção entre fases.

Princípio de projeto
--------------------
Um resultado bom no histórico, sozinho, **nunca** autoriza o robô a arriscar
dinheiro. Cada fase tem critérios próprios, e a evidência exigida aumenta a
cada passo:

    BACKTEST → precisa de amostra e expectativa positiva
    OUT_OF_SAMPLE → precisa sobreviver a dados que não a ajustaram
    PAPER_TRADING → precisa funcionar em tempo real, com custo real
    SHADOW → precisa que a decisão teórica bata com a execução simulada
    ASSISTIDO → precisa que o humano confirme que as propostas fazem sentido
    REAL_LIMITADO → capital reduzido, monitorado, sem promessa de liberação

Cada gate devolve a lista completa de reprovações, com o valor medido ao lado
do exigido. Não existe "aprovado com ressalva": ou os critérios foram
atendidos, ou a estratégia fica onde está.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import BacktestStats
from ..validation.montecarlo import RelatorioMonteCarlo
from ..validation.overfit import RelatorioOverfit, VeredictoOverfit
from ..validation.stats import ExpectedValue
from .registry import Fase


@dataclass(slots=True)
class Criterio:
    nome: str
    exigido: Any
    medido: Any
    passou: bool
    explicacao: str = ""

    def to_dict(self) -> dict[str, Any]:
        def _r(v: Any) -> Any:
            return round(v, 4) if isinstance(v, float) else v
        return {"nome": self.nome, "exigido": _r(self.exigido),
                "medido": _r(self.medido), "passou": self.passou,
                "explicacao": self.explicacao}


@dataclass(slots=True)
class ResultadoGate:
    fase_atual: Fase
    fase_alvo: Fase | None
    aprovado: bool
    criterios: list[Criterio] = field(default_factory=list)
    reprovacoes: list[str] = field(default_factory=list)
    dados_faltando: list[str] = field(default_factory=list)
    resumo: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fase_atual": self.fase_atual.value,
            "fase_alvo": self.fase_alvo.value if self.fase_alvo else None,
            "aprovado": self.aprovado,
            "criterios": [c.to_dict() for c in self.criterios],
            "reprovacoes": self.reprovacoes,
            "dados_faltando": self.dados_faltando,
            "resumo": self.resumo,
        }


@dataclass(slots=True)
class CriteriosPromocao:
    """Limiares por fase. Todos configuráveis, todos conservadores."""

    # ------------------------------------------------- BACKTEST → OOS
    backtest_min_trades: int = 40
    backtest_min_expectancy_r: float = 0.10
    backtest_min_profit_factor: float = 1.25
    backtest_max_drawdown_pct: float = 25.0

    # --------------------------------- OUT_OF_SAMPLE → PAPER_TRADING
    oos_min_trades: int = 30
    oos_min_expectancy_r: float = 0.10
    oos_min_profit_factor: float = 1.20
    oos_max_degradacao: float = 0.50
    oos_max_drawdown_pct: float = 20.0
    # O piso do IC da expectativa tem de ser positivo: não basta a média ser
    # boa, a amostra tem de excluir o cenário de vantagem zero.
    oos_exige_ic_positivo: bool = True
    oos_exige_overfit_aprovado: bool = True
    # Monte Carlo
    mc_max_prob_ruina: float = 0.01
    mc_max_drawdown_p95_pct: float = 25.0
    mc_max_prob_prejuizo: float = 0.35

    # ------------------------------------- PAPER_TRADING → SHADOW
    paper_min_trades: int = 40
    paper_min_expectancy_r: float = 0.05
    paper_min_dias: int = 21
    # Desvio tolerado entre a expectativa do paper e a do out-of-sample. Se o
    # paper for muito pior, o custo real (spread/slippage/latência) estava
    # subestimado no backtest.
    paper_max_desvio_vs_oos: float = 0.60

    # ----------------------------------------- SHADOW → ASSISTIDO
    shadow_min_decisoes: int = 60
    shadow_min_dias: int = 14
    # Fração das decisões em que a execução simulada bateu com a teórica.
    shadow_min_fidelidade: float = 0.85
    # Demo mede o ENCANAMENTO, não a vantagem. Por isso os critérios são
    # sobre execução: ordens aceitas, preenchimento coerente com o plano e
    # zero divergência de reconciliação. Exigir expectativa positiva aqui
    # confundiria "o sistema sabe enviar ordem" com "a estratégia dá
    # dinheiro", que é o que as fases anteriores já mediram.
    demo_min_ordens: int = 20
    demo_min_dias: int = 5
    demo_min_taxa_aceite: float = 0.98
    demo_max_divergencias: int = 0
    demo_max_desvio_preenchimento_pct: float = 0.20

    # --------------------------------- ASSISTIDO → REAL_LIMITADO
    assistido_min_operacoes: int = 25
    assistido_min_dias: int = 21
    # Fração das propostas que o humano confirmou. Se o operador recusa
    # metade, o sistema não está pronto.
    assistido_min_taxa_confirmacao: float = 0.70
    assistido_min_expectancy_r: float = 0.05

    def to_dict(self) -> dict[str, Any]:
        return {
            "backtest": {
                "min_trades": self.backtest_min_trades,
                "min_expectancy_r": self.backtest_min_expectancy_r,
                "min_profit_factor": self.backtest_min_profit_factor,
                "max_drawdown_pct": self.backtest_max_drawdown_pct,
            },
            "out_of_sample": {
                "min_trades": self.oos_min_trades,
                "min_expectancy_r": self.oos_min_expectancy_r,
                "min_profit_factor": self.oos_min_profit_factor,
                "max_degradacao": self.oos_max_degradacao,
                "max_drawdown_pct": self.oos_max_drawdown_pct,
                "exige_ic_positivo": self.oos_exige_ic_positivo,
                "exige_overfit_aprovado": self.oos_exige_overfit_aprovado,
                "mc_max_prob_ruina": self.mc_max_prob_ruina,
                "mc_max_drawdown_p95_pct": self.mc_max_drawdown_p95_pct,
                "mc_max_prob_prejuizo": self.mc_max_prob_prejuizo,
            },
            "paper_trading": {
                "min_trades": self.paper_min_trades,
                "min_expectancy_r": self.paper_min_expectancy_r,
                "min_dias": self.paper_min_dias,
                "max_desvio_vs_oos": self.paper_max_desvio_vs_oos,
            },
            "demo": {
                "min_ordens": self.demo_min_ordens,
                "min_dias": self.demo_min_dias,
                "min_taxa_aceite": self.demo_min_taxa_aceite,
                "max_divergencias": self.demo_max_divergencias,
                "max_desvio_preenchimento_pct":
                    self.demo_max_desvio_preenchimento_pct,
            },
            "shadow": {
                "min_decisoes": self.shadow_min_decisoes,
                "min_dias": self.shadow_min_dias,
                "min_fidelidade": self.shadow_min_fidelidade,
            },
            "assistido": {
                "min_operacoes": self.assistido_min_operacoes,
                "min_dias": self.assistido_min_dias,
                "min_taxa_confirmacao": self.assistido_min_taxa_confirmacao,
                "min_expectancy_r": self.assistido_min_expectancy_r,
            },
        }


@dataclass(slots=True)
class EvidenciaFase:
    """Tudo o que o gate pode consultar. Campos ausentes são reprovação.

    Deixar um campo vazio não passa o gate por omissão: `dados_faltando`
    registra a ausência e o gate reprova. Isso impede o caminho mais fácil de
    burlar o processo, que é simplesmente não medir.
    """

    stats_backtest: BacktestStats | None = None
    stats_oos: BacktestStats | None = None
    degradacao_expectancy: float | None = None
    ev_oos: ExpectedValue | None = None
    overfit: RelatorioOverfit | None = None
    monte_carlo: RelatorioMonteCarlo | None = None

    stats_paper: BacktestStats | None = None
    dias_paper: int | None = None

    decisoes_shadow: int | None = None
    dias_shadow: int | None = None
    fidelidade_shadow: float | None = None
    # Demo trading: execução no ambiente de teste da corretora.
    ordens_demo: int | None = None
    dias_demo: int | None = None
    taxa_aceite_demo: float | None = None
    divergencias_demo: int | None = None
    desvio_preenchimento_demo_pct: float | None = None

    operacoes_assistido: int | None = None
    dias_assistido: int | None = None
    taxa_confirmacao: float | None = None
    stats_assistido: BacktestStats | None = None


def _c(nome: str, exigido: Any, medido: Any, passou: bool,
       explicacao: str = "") -> Criterio:
    return Criterio(nome, exigido, medido, passou, explicacao)


def avaliar_gate(fase: Fase, ev: EvidenciaFase,
                 crit: CriteriosPromocao | None = None) -> ResultadoGate:
    """Avalia se a estratégia pode sair de `fase` para a próxima."""
    crit = crit or CriteriosPromocao()
    if fase is Fase.RASCUNHO:
        return ResultadoGate(
            fase, Fase.BACKTEST, True,
            resumo="rascunho avança para backtest sem gate: a fase de "
                   "backtest é onde a medição começa")

    if fase is Fase.BACKTEST:
        return _gate_backtest(ev, crit)
    if fase is Fase.OUT_OF_SAMPLE:
        return _gate_oos(ev, crit)
    if fase is Fase.PAPER_TRADING:
        return _gate_paper(ev, crit)
    if fase is Fase.SHADOW:
        return _gate_shadow(ev, crit)
    if fase is Fase.DEMO:
        return _gate_demo(ev, crit)
    if fase is Fase.ASSISTIDO:
        return _gate_assistido(ev, crit)

    return ResultadoGate(
        fase, None, False,
        reprovacoes=[f"fase {fase.value} não tem promoção definida"],
        resumo=f"{fase.value} é terminal neste pipeline")


def _finalizar(fase: Fase, alvo: Fase, criterios: list[Criterio],
               faltando: list[str]) -> ResultadoGate:
    reprovacoes = [
        f"{c.nome}: medido {c.medido} contra exigido {c.exigido}"
        + (f" — {c.explicacao}" if c.explicacao else "")
        for c in criterios if not c.passou
    ]
    aprovado = not reprovacoes and not faltando
    if faltando:
        resumo = (f"NÃO AVANÇA: faltam medições obrigatórias "
                  f"({', '.join(faltando)}). Ausência de dado não é "
                  f"aprovação.")
    elif reprovacoes:
        resumo = (f"NÃO AVANÇA de {fase.value} para {alvo.value}: "
                  f"{len(reprovacoes)} de {len(criterios)} critérios "
                  f"reprovados.")
    else:
        resumo = (f"apto a avançar de {fase.value} para {alvo.value}: "
                  f"{len(criterios)} critérios atendidos.")
    return ResultadoGate(fase, alvo, aprovado, criterios, reprovacoes,
                         faltando, resumo)


def _gate_backtest(ev: EvidenciaFase,
                   crit: CriteriosPromocao) -> ResultadoGate:
    faltando: list[str] = []
    if ev.stats_backtest is None:
        faltando.append("stats_backtest")
        return _finalizar(Fase.BACKTEST, Fase.OUT_OF_SAMPLE, [], faltando)

    s = ev.stats_backtest
    criterios = [
        _c("trades", crit.backtest_min_trades, s.trades,
           s.trades >= crit.backtest_min_trades,
           "amostra menor não distingue vantagem de ruído"),
        _c("expectancy_r", crit.backtest_min_expectancy_r, s.expectancy_r,
           s.expectancy_r >= crit.backtest_min_expectancy_r),
        _c("profit_factor", crit.backtest_min_profit_factor, s.profit_factor,
           s.profit_factor >= crit.backtest_min_profit_factor),
        _c("max_drawdown_pct", crit.backtest_max_drawdown_pct,
           s.max_drawdown_pct,
           s.max_drawdown_pct <= crit.backtest_max_drawdown_pct,
           "drawdown acima disso é intolerável mesmo com expectativa boa"),
    ]
    return _finalizar(Fase.BACKTEST, Fase.OUT_OF_SAMPLE, criterios, faltando)


def _gate_oos(ev: EvidenciaFase, crit: CriteriosPromocao) -> ResultadoGate:
    """O gate mais rigoroso do pipeline — de propósito.

    É aqui que se decide se a estratégia tem vantagem ou apenas descreveu o
    passado. Exige, simultaneamente: amostra out-of-sample, expectativa
    positiva, degradação contida, piso do IC acima de zero, ausência de
    sinais de overfitting e Monte Carlo com ruína aceitável.
    """
    faltando: list[str] = []
    if ev.stats_oos is None:
        faltando.append("stats_oos")
    if ev.ev_oos is None:
        faltando.append("ev_oos")
    if crit.oos_exige_overfit_aprovado and ev.overfit is None:
        faltando.append("relatorio_overfit")
    if ev.monte_carlo is None:
        faltando.append("monte_carlo")
    if faltando:
        return _finalizar(Fase.OUT_OF_SAMPLE, Fase.PAPER_TRADING, [], faltando)

    s = ev.stats_oos
    evo = ev.ev_oos
    mc = ev.monte_carlo
    assert s is not None and evo is not None and mc is not None

    criterios = [
        _c("oos_trades", crit.oos_min_trades, s.trades,
           s.trades >= crit.oos_min_trades),
        _c("oos_expectancy_r", crit.oos_min_expectancy_r, s.expectancy_r,
           s.expectancy_r >= crit.oos_min_expectancy_r),
        _c("oos_profit_factor", crit.oos_min_profit_factor, s.profit_factor,
           s.profit_factor >= crit.oos_min_profit_factor),
        _c("oos_max_drawdown_pct", crit.oos_max_drawdown_pct,
           s.max_drawdown_pct,
           s.max_drawdown_pct <= crit.oos_max_drawdown_pct),
        _c("mc_prob_ruina", crit.mc_max_prob_ruina, mc.prob_ruina,
           mc.prob_ruina <= crit.mc_max_prob_ruina,
           "probabilidade de perder metade do capital nas simulações"),
        _c("mc_drawdown_p95", crit.mc_max_drawdown_p95_pct, mc.drawdown_p95,
           mc.drawdown_p95 <= crit.mc_max_drawdown_p95_pct,
           "1 em 20 sequências passa deste drawdown"),
        _c("mc_prob_prejuizo", crit.mc_max_prob_prejuizo, mc.prob_prejuizo,
           mc.prob_prejuizo <= crit.mc_max_prob_prejuizo),
    ]

    if ev.degradacao_expectancy is not None:
        criterios.append(_c(
            "degradacao_is_oos", crit.oos_max_degradacao,
            ev.degradacao_expectancy,
            ev.degradacao_expectancy <= crit.oos_max_degradacao,
            "quanto da expectativa desapareceu fora da amostra"))
    else:
        faltando.append("degradacao_expectancy")

    if crit.oos_exige_ic_positivo:
        criterios.append(_c(
            "ic_expectativa_inferior", "> 0",
            evo.ic_expectativa.inferior,
            evo.ic_expectativa.inferior > 0,
            "o piso do intervalo de confiança precisa excluir vantagem zero; "
            "média positiva com piso negativo pode ser sorte da amostra"))
        criterios.append(_c(
            "ev_pessimista_r", "> 0", evo.ev_pessimista_r,
            evo.ev_pessimista_r > 0,
            "EV recalculado com a taxa de acerto no piso do IC"))

    if crit.oos_exige_overfit_aprovado and ev.overfit is not None:
        ok = ev.overfit.veredicto is VeredictoOverfit.ROBUSTO
        criterios.append(_c(
            "overfit_veredicto", VeredictoOverfit.ROBUSTO.value,
            ev.overfit.veredicto.value, ok, ev.overfit.resumo))

    return _finalizar(Fase.OUT_OF_SAMPLE, Fase.PAPER_TRADING, criterios,
                      faltando)


def _gate_paper(ev: EvidenciaFase, crit: CriteriosPromocao) -> ResultadoGate:
    faltando: list[str] = []
    if ev.stats_paper is None:
        faltando.append("stats_paper")
    if ev.dias_paper is None:
        faltando.append("dias_paper")
    if faltando:
        return _finalizar(Fase.PAPER_TRADING, Fase.SHADOW, [], faltando)

    s = ev.stats_paper
    assert s is not None and ev.dias_paper is not None
    criterios = [
        _c("paper_trades", crit.paper_min_trades, s.trades,
           s.trades >= crit.paper_min_trades),
        _c("paper_dias", crit.paper_min_dias, ev.dias_paper,
           ev.dias_paper >= crit.paper_min_dias,
           "tempo em mercado importa: 40 operações em 2 dias testam um "
           "único regime"),
        _c("paper_expectancy_r", crit.paper_min_expectancy_r, s.expectancy_r,
           s.expectancy_r >= crit.paper_min_expectancy_r),
    ]

    # Comparação com o out-of-sample: se o paper for muito pior, os custos
    # reais estavam subestimados no backtest.
    if ev.stats_oos is not None and ev.stats_oos.expectancy_r > 0:
        desvio = ((ev.stats_oos.expectancy_r - s.expectancy_r)
                  / abs(ev.stats_oos.expectancy_r))
        criterios.append(_c(
            "desvio_paper_vs_oos", crit.paper_max_desvio_vs_oos, desvio,
            desvio <= crit.paper_max_desvio_vs_oos,
            "queda grande do out-of-sample para o paper indica que spread, "
            "slippage ou latência estavam subestimados"))

    return _finalizar(Fase.PAPER_TRADING, Fase.SHADOW, criterios, faltando)


def _gate_shadow(ev: EvidenciaFase, crit: CriteriosPromocao) -> ResultadoGate:
    faltando: list[str] = []
    if ev.decisoes_shadow is None:
        faltando.append("decisoes_shadow")
    if ev.dias_shadow is None:
        faltando.append("dias_shadow")
    if ev.fidelidade_shadow is None:
        faltando.append("fidelidade_shadow")
    if faltando:
        return _finalizar(Fase.SHADOW, Fase.DEMO, [], faltando)

    assert (ev.decisoes_shadow is not None and ev.dias_shadow is not None
            and ev.fidelidade_shadow is not None)
    criterios = [
        _c("shadow_decisoes", crit.shadow_min_decisoes, ev.decisoes_shadow,
           ev.decisoes_shadow >= crit.shadow_min_decisoes),
        _c("shadow_dias", crit.shadow_min_dias, ev.dias_shadow,
           ev.dias_shadow >= crit.shadow_min_dias),
        _c("shadow_fidelidade", crit.shadow_min_fidelidade,
           ev.fidelidade_shadow,
           ev.fidelidade_shadow >= crit.shadow_min_fidelidade,
           "fração das decisões em que a execução simulada reproduziu a "
           "decisão teórica; abaixo disso o sistema decide uma coisa e "
           "executa outra"),
    ]
    return _finalizar(Fase.SHADOW, Fase.DEMO, criterios, faltando)


def _gate_demo(ev: EvidenciaFase, crit: CriteriosPromocao) -> ResultadoGate:
    """Demo → assistido: o encanamento funciona?

    Shadow já provou que as decisões valem alguma coisa. O que demo mede é
    outra coisa inteiramente: assinatura aceita, tamanho arredondado ao passo
    do contrato, stop anexado à ordem de abertura, `clientOid` impedindo
    duplicata, reconciliação sem divergência. Nada disso aparece em
    simulação, e tudo isso quebra na primeira ordem real.

    Por isso o critério de preenchimento é sobre DESVIO em relação ao preço
    planejado, e não sobre lucro: uma ordem que executa 0,5% longe do plano
    revela um problema de execução mesmo quando dá lucro por sorte.
    """
    faltando: list[str] = []
    if ev.ordens_demo is None:
        faltando.append("ordens_demo")
    if ev.dias_demo is None:
        faltando.append("dias_demo")
    if ev.taxa_aceite_demo is None:
        faltando.append("taxa_aceite_demo")
    if ev.divergencias_demo is None:
        faltando.append("divergencias_demo")
    if faltando:
        return _finalizar(Fase.DEMO, Fase.ASSISTIDO, [], faltando)

    assert (ev.ordens_demo is not None and ev.dias_demo is not None
            and ev.taxa_aceite_demo is not None
            and ev.divergencias_demo is not None)
    criterios = [
        _c("demo_ordens", crit.demo_min_ordens, ev.ordens_demo,
           ev.ordens_demo >= crit.demo_min_ordens),
        _c("demo_dias", crit.demo_min_dias, ev.dias_demo,
           ev.dias_demo >= crit.demo_min_dias),
        _c("demo_taxa_aceite", crit.demo_min_taxa_aceite,
           ev.taxa_aceite_demo,
           ev.taxa_aceite_demo >= crit.demo_min_taxa_aceite,
           "fração das ordens que a corretora aceitou; rejeição aqui é "
           "defeito de montagem da ordem, e ele se repete igual no real"),
        _c("demo_divergencias", f"<= {crit.demo_max_divergencias}",
           ev.divergencias_demo,
           ev.divergencias_demo <= crit.demo_max_divergencias,
           "divergências entre o estado local e o da corretora; qualquer "
           "uma aqui vira posição sem gestão no real"),
    ]
    if ev.desvio_preenchimento_demo_pct is not None:
        criterios.append(_c(
            "demo_desvio_preenchimento",
            f"<= {crit.demo_max_desvio_preenchimento_pct}%",
            ev.desvio_preenchimento_demo_pct,
            (ev.desvio_preenchimento_demo_pct
             <= crit.demo_max_desvio_preenchimento_pct),
            "distância média entre o preço planejado e o executado; desvio "
            "alto revela problema de execução mesmo quando dá lucro"))
    return _finalizar(Fase.DEMO, Fase.ASSISTIDO, criterios, faltando)


def _gate_assistido(ev: EvidenciaFase,
                    crit: CriteriosPromocao) -> ResultadoGate:
    faltando: list[str] = []
    if ev.operacoes_assistido is None:
        faltando.append("operacoes_assistido")
    if ev.dias_assistido is None:
        faltando.append("dias_assistido")
    if ev.taxa_confirmacao is None:
        faltando.append("taxa_confirmacao")
    if ev.stats_assistido is None:
        faltando.append("stats_assistido")
    if faltando:
        return _finalizar(Fase.ASSISTIDO, Fase.REAL_LIMITADO, [], faltando)

    assert (ev.operacoes_assistido is not None and ev.dias_assistido is not None
            and ev.taxa_confirmacao is not None
            and ev.stats_assistido is not None)
    criterios = [
        _c("assistido_operacoes", crit.assistido_min_operacoes,
           ev.operacoes_assistido,
           ev.operacoes_assistido >= crit.assistido_min_operacoes),
        _c("assistido_dias", crit.assistido_min_dias, ev.dias_assistido,
           ev.dias_assistido >= crit.assistido_min_dias),
        _c("taxa_confirmacao", crit.assistido_min_taxa_confirmacao,
           ev.taxa_confirmacao,
           ev.taxa_confirmacao >= crit.assistido_min_taxa_confirmacao,
           "fração das propostas que o operador confirmou; se ele recusa "
           "boa parte, o sistema ainda não está propondo o que faz sentido"),
        _c("assistido_expectancy_r", crit.assistido_min_expectancy_r,
           ev.stats_assistido.expectancy_r,
           ev.stats_assistido.expectancy_r >= crit.assistido_min_expectancy_r),
    ]
    return _finalizar(Fase.ASSISTIDO, Fase.REAL_LIMITADO, criterios, faltando)
