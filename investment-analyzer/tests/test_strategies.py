"""Versionamento de estratégias e gates de promoção."""
import pytest

from investai.models import BacktestStats
from investai.strategies import (
    ORDEM, CriteriosPromocao, EstrategiaError, EvidenciaFase, Fase,
    StrategyRegistry, avaliar_gate, hash_parametros,
)
from investai.strategies.pipeline import avaliar_para_promocao
from investai.validation import calcular_ev, monte_carlo
from investai.validation.overfit import RelatorioOverfit, VeredictoOverfit


@pytest.fixture
def reg():
    return StrategyRegistry()


@pytest.fixture
def params():
    return {"score_minimo": 66.0, "atr_mult_stop": 1.6, "alvos_r": [1.8, 3.0]}


# ================================================== hash e versionamento
def test_hash_e_estavel_e_independe_da_ordem():
    a = hash_parametros({"x": 1, "y": 2})
    b = hash_parametros({"y": 2, "x": 1})
    assert a == b


def test_hash_muda_com_o_valor():
    assert hash_parametros({"x": 1}) != hash_parametros({"x": 1.01})


def test_parametros_identicos_devolvem_a_mesma_versao(reg, params):
    v1 = reg.criar("s", params)
    v2 = reg.criar("s", dict(reversed(list(params.items()))))
    assert v1.chave == v2.chave
    assert len(reg.listar()) == 1


def test_derivar_cria_nova_versao_preservando_a_anterior(reg, params):
    v1 = reg.criar("s", params)
    reg.promover(v1.chave)
    v2 = reg.derivar(v1.chave, {"score_minimo": 72.0})
    assert v2.version == v1.version + 1
    assert v2.params_hash != v1.params_hash
    # A versão anterior mantém fase e histórico.
    assert reg.obter(v1.chave).fase is Fase.BACKTEST
    assert len(reg.listar(strategy_id="s")) == 2


def test_derivar_sem_mudanca_real_e_recusado(reg, params):
    v1 = reg.criar("s", params)
    with pytest.raises(EstrategiaError, match="não alteram"):
        reg.derivar(v1.chave, {"score_minimo": 66.0})


def test_versao_desconhecida_e_erro(reg):
    with pytest.raises(EstrategiaError):
        reg.obter("inexistente@v9")


# ==================================================== transições de fase
def test_progressao_passa_por_todas_as_fases(reg, params):
    v = reg.criar("s", params)
    visitadas = [v.fase]
    for _ in range(len(ORDEM) - 1):
        reg.promover(v.chave)
        visitadas.append(v.fase)
    assert visitadas == list(ORDEM)


def test_nao_existe_fase_apos_real_limitado(reg, params):
    v = reg.criar("s", params)
    for _ in range(len(ORDEM) - 1):
        reg.promover(v.chave)
    assert v.fase is Fase.REAL_LIMITADO
    with pytest.raises(EstrategiaError, match="última fase"):
        reg.promover(v.chave)


def test_estrategia_reprovada_nao_avanca(reg, params):
    v = reg.criar("s", params)
    reg.reprovar(v.chave, "degradação grave")
    with pytest.raises(EstrategiaError):
        reg.promover(v.chave)
    assert v.motivo_reprovacao == "degradação grave"


def test_rebaixar_quando_a_vantagem_acaba(reg, params):
    v = reg.criar("s", params)
    for _ in range(3):
        reg.promover(v.chave)
    assert v.fase is Fase.PAPER_TRADING
    reg.rebaixar(v.chave, "expectativa realizada negativa")
    assert v.fase is Fase.OUT_OF_SAMPLE
    assert v.historico[-1]["evento"] == "rebaixada"


def test_historico_registra_toda_transicao(reg, params):
    v = reg.criar("s", params)
    reg.promover(v.chave)
    reg.rebaixar(v.chave, "teste")
    reg.aposentar(v.chave, "fim")
    eventos = [h["evento"] for h in v.historico]
    assert eventos == ["criada", "promovida", "rebaixada", "aposentada"]


def test_apenas_real_limitado_e_operavel_em_real(reg, params):
    v = reg.criar("s", params)
    for _ in range(len(ORDEM) - 1):
        assert not v.operavel_real
        reg.promover(v.chave)
    assert v.operavel_real
    assert reg.operaveis_em_real() == [v]


def test_rascunho_nao_conta_como_ativa(reg, params):
    v = reg.criar("s", params)
    assert not v.ativa
    reg.promover(v.chave)
    assert v.ativa


# ========================================================= gates
def _ev_oos_boa(n=120, p=0.45):
    import random
    random.seed(5)
    ret = [2.0 if random.random() < p else -1.0 for _ in range(n)]
    return EvidenciaFase(
        stats_oos=BacktestStats(trades=n, expectancy_r=0.35,
                                profit_factor=1.6, max_drawdown_pct=9.0),
        degradacao_expectancy=0.20,
        ev_oos=calcular_ev(ret),
        overfit=RelatorioOverfit(VeredictoOverfit.ROBUSTO, [], 0, "ok"),
        monte_carlo=monte_carlo(ret, n_simulacoes=800,
                                risco_por_trade_frac=0.005))


def test_rascunho_avanca_sem_gate():
    r = avaliar_gate(Fase.RASCUNHO, EvidenciaFase())
    assert r.aprovado


def test_gate_backtest_aprova_caso_bom():
    r = avaliar_gate(Fase.BACKTEST, EvidenciaFase(
        stats_backtest=BacktestStats(trades=60, expectancy_r=0.25,
                                     profit_factor=1.5,
                                     max_drawdown_pct=12.0)))
    assert r.aprovado
    assert r.fase_alvo is Fase.OUT_OF_SAMPLE


def test_gate_backtest_lista_todas_as_reprovacoes():
    r = avaliar_gate(Fase.BACKTEST, EvidenciaFase(
        stats_backtest=BacktestStats(trades=15, expectancy_r=0.05,
                                     profit_factor=1.1,
                                     max_drawdown_pct=30.0)))
    assert not r.aprovado
    assert len(r.reprovacoes) == 4


def test_dado_faltando_nao_e_aprovacao():
    """O caminho mais fácil de burlar o processo é não medir."""
    r = avaliar_gate(Fase.OUT_OF_SAMPLE, EvidenciaFase())
    assert not r.aprovado
    assert r.dados_faltando
    assert "não é aprovação" in r.resumo


def test_gate_oos_aprova_evidencia_completa():
    assert avaliar_gate(Fase.OUT_OF_SAMPLE, _ev_oos_boa()).aprovado


def test_gate_oos_reprova_piso_de_ic_negativo():
    """Média positiva com piso do IC negativo pode ser sorte da amostra."""
    import random
    random.seed(9)
    ret = [2.0 if random.random() < 0.42 else -1.0 for _ in range(34)]
    ev = EvidenciaFase(
        stats_oos=BacktestStats(trades=34, expectancy_r=0.26,
                                profit_factor=1.4, max_drawdown_pct=8.0),
        degradacao_expectancy=0.30, ev_oos=calcular_ev(ret),
        overfit=RelatorioOverfit(VeredictoOverfit.ROBUSTO, [], 0, "ok"),
        monte_carlo=monte_carlo(ret, n_simulacoes=800,
                                risco_por_trade_frac=0.005))
    r = avaliar_gate(Fase.OUT_OF_SAMPLE, ev)
    assert not r.aprovado
    assert any("ic_expectativa_inferior" in x for x in r.reprovacoes)


def test_gate_oos_reprova_overfitting():
    ev = _ev_oos_boa()
    ev.overfit = RelatorioOverfit(VeredictoOverfit.PROVAVEL_OVERFITTING, [],
                                  3, "3 sinais")
    r = avaliar_gate(Fase.OUT_OF_SAMPLE, ev)
    assert not r.aprovado
    assert any("overfit" in x for x in r.reprovacoes)


def test_gate_oos_reprova_degradacao_alta():
    ev = _ev_oos_boa()
    ev.degradacao_expectancy = 0.85
    r = avaliar_gate(Fase.OUT_OF_SAMPLE, ev)
    assert not r.aprovado
    assert any("degradacao" in x for x in r.reprovacoes)


def test_gate_oos_reprova_ruina_no_monte_carlo():
    import random
    random.seed(3)
    ret = [2.0 if random.random() < 0.45 else -1.0 for _ in range(120)]
    ev = _ev_oos_boa()
    ev.monte_carlo = monte_carlo(ret, n_simulacoes=800,
                                 risco_por_trade_frac=0.15)
    r = avaliar_gate(Fase.OUT_OF_SAMPLE, ev)
    assert not r.aprovado
    assert any("ruina" in x or "drawdown" in x for x in r.reprovacoes)


def test_gate_paper_exige_tempo_em_mercado():
    """40 operações em 2 dias testam um único regime."""
    r = avaliar_gate(Fase.PAPER_TRADING, EvidenciaFase(
        stats_paper=BacktestStats(trades=50, expectancy_r=0.2),
        dias_paper=2))
    assert not r.aprovado
    assert any("dias" in x for x in r.reprovacoes)


def test_gate_paper_aprova_caso_completo():
    r = avaliar_gate(Fase.PAPER_TRADING, EvidenciaFase(
        stats_paper=BacktestStats(trades=50, expectancy_r=0.2),
        dias_paper=30))
    assert r.aprovado


def test_gate_paper_detecta_custo_subestimado_no_backtest():
    """Queda grande do OOS para o paper indica custo real subestimado."""
    r = avaliar_gate(Fase.PAPER_TRADING, EvidenciaFase(
        stats_paper=BacktestStats(trades=50, expectancy_r=0.06),
        dias_paper=30,
        stats_oos=BacktestStats(trades=100, expectancy_r=0.50)))
    assert not r.aprovado
    assert any("desvio_paper_vs_oos" in x for x in r.reprovacoes)


def test_gate_shadow_exige_fidelidade():
    r = avaliar_gate(Fase.SHADOW, EvidenciaFase(
        decisoes_shadow=100, dias_shadow=20, fidelidade_shadow=0.60))
    assert not r.aprovado
    assert any("fidelidade" in x for x in r.reprovacoes)


def test_gate_shadow_aprova_caso_completo():
    assert avaliar_gate(Fase.SHADOW, EvidenciaFase(
        decisoes_shadow=100, dias_shadow=20,
        fidelidade_shadow=0.95)).aprovado


def test_gate_assistido_exige_confirmacao_humana():
    """Se o operador recusa metade das propostas, o sistema não está pronto."""
    r = avaliar_gate(Fase.ASSISTIDO, EvidenciaFase(
        operacoes_assistido=40, dias_assistido=30, taxa_confirmacao=0.45,
        stats_assistido=BacktestStats(trades=40, expectancy_r=0.2)))
    assert not r.aprovado
    assert any("taxa_confirmacao" in x for x in r.reprovacoes)


def test_gate_assistido_aprova_caso_completo():
    ev = EvidenciaFase(
        operacoes_assistido=40, dias_assistido=30, taxa_confirmacao=0.85,
        stats_assistido=BacktestStats(trades=40, expectancy_r=0.2))
    r = avaliar_gate(Fase.ASSISTIDO, ev)
    assert r.aprovado
    assert r.fase_alvo is Fase.REAL_LIMITADO


def test_fase_terminal_nao_promove():
    r = avaliar_gate(Fase.REAL_LIMITADO, EvidenciaFase())
    assert not r.aprovado
    assert "terminal" in r.resumo


def test_criterios_customizados_sao_respeitados():
    frouxo = CriteriosPromocao(backtest_min_trades=5,
                               backtest_min_expectancy_r=0.0,
                               backtest_min_profit_factor=1.0,
                               backtest_max_drawdown_pct=99.0)
    ev = EvidenciaFase(stats_backtest=BacktestStats(
        trades=6, expectancy_r=0.01, profit_factor=1.01,
        max_drawdown_pct=50.0))
    assert avaliar_gate(Fase.BACKTEST, ev, frouxo).aprovado
    assert not avaliar_gate(Fase.BACKTEST, ev).aprovado


# ============================================ integração com o registro
def test_gate_reprovado_e_registrado_no_historico(reg, params):
    v = reg.criar("s", params)
    reg.promover(v.chave)       # -> backtest
    rel = avaliar_para_promocao(reg, v.chave, EvidenciaFase(
        stats_backtest=BacktestStats(trades=5, expectancy_r=-0.5)))
    assert not rel.promovida
    assert v.fase is Fase.BACKTEST
    assert any(h["evento"] == "gate_avaliado" and not h["aprovado"]
               for h in v.historico)


def test_gate_aprovado_promove_e_registra(reg, params):
    v = reg.criar("s", params)
    reg.promover(v.chave)
    rel = avaliar_para_promocao(reg, v.chave, EvidenciaFase(
        stats_backtest=BacktestStats(trades=60, expectancy_r=0.25,
                                     profit_factor=1.5,
                                     max_drawdown_pct=12.0)))
    assert rel.promovida
    assert v.fase is Fase.OUT_OF_SAMPLE
    assert v.ultimo_gate["aprovado"] is True


def test_promocao_pode_ser_apenas_avaliada(reg, params):
    v = reg.criar("s", params)
    reg.promover(v.chave)
    rel = avaliar_para_promocao(
        reg, v.chave,
        EvidenciaFase(stats_backtest=BacktestStats(
            trades=60, expectancy_r=0.25, profit_factor=1.5,
            max_drawdown_pct=12.0)),
        promover_se_aprovado=False)
    assert rel.gate.aprovado
    assert not rel.promovida
    assert v.fase is Fase.BACKTEST


def test_serializacao_do_registro(reg, params):
    reg.criar("s", params)
    d = reg.to_dict()
    assert d["total"] == 1
    assert "ordem_das_fases" in d
    assert len(d["descricao_das_fases"]) >= len(ORDEM)
