"""Validação estatística: IC, walk-forward, Monte Carlo e overfitting."""
import random

import pytest

from investai.models import BacktestStats, Side, Trade
from investai.validation import (
    VeredictoOverfit, analise_sensibilidade, avaliar_overfitting, calcular_ev,
    comparar_modos, dividir_temporal, ic_media, kelly_fraction, monte_carlo,
    risco_de_ruina, sinal_concentracao, sinal_consistencia, sinal_degradacao,
    sinal_estabilidade, sinal_graus_de_liberdade, walk_forward, wilson,
)
from investai.validation.walkforward import SplitError


def tr(pnl_r, pnl_usd=None, closed=1, symbol="X", opened=0):
    return Trade(symbol=symbol, side=Side.LONG, entry=100.0, exit=101.0,
                 size=1.0, opened_at=opened, closed_at=closed,
                 pnl_usd=pnl_usd if pnl_usd is not None else pnl_r * 10,
                 pnl_r=pnl_r, motivo_saida="teste")


# ================================================== intervalo de confiança
def test_wilson_nao_devolve_certeza_com_dez_acertos():
    """O intervalo normal ingênuo daria [1.0, 1.0] — certeza a partir de dez
    observações. Wilson é escolhido exatamente por não fazer isso."""
    ic = wilson(10, 10)
    assert ic.estimativa == 1.0
    assert ic.inferior < 0.80
    assert not ic.informativo


def test_nove_de_dez_tem_intervalo_largo():
    ic = wilson(9, 10)
    assert ic.inferior < 0.65
    assert ic.amplitude > 0.30
    assert not ic.informativo


def test_intervalo_encolhe_com_amostra():
    amplitudes = [wilson(int(n * 0.9), n).amplitude for n in (10, 50, 200, 1000)]
    assert amplitudes == sorted(amplitudes, reverse=True)
    assert amplitudes[-1] < 0.06


def test_wilson_respeita_os_limites_zero_e_um():
    assert wilson(0, 30).inferior == 0.0
    assert wilson(30, 30).superior == 1.0


def test_wilson_sem_amostra_e_nao_informativo():
    ic = wilson(0, 0)
    assert ic.n == 0 and not ic.informativo


def test_wilson_recusa_entrada_incoerente():
    with pytest.raises(ValueError):
        wilson(15, 10)


def test_confianca_nao_suportada_recusada():
    with pytest.raises(ValueError):
        wilson(5, 10, confianca=0.80)


def test_ic_media_cobre_a_media():
    valores = [1.0, 2.0, 3.0, 4.0, 5.0]
    ic = ic_media(valores)
    assert ic.inferior < ic.estimativa < ic.superior
    assert ic.estimativa == pytest.approx(3.0)


# ============================================== expectativa matemática
def test_ev_usa_a_formula_declarada():
    """EV = P(ganho)×ganho − P(perda)×perda − custos."""
    ev = calcular_ev([2.0] * 40 + [-1.0] * 60, custos_r=0.05)
    esperado = 0.4 * 2.0 - 0.6 * 1.0 - 0.05
    assert ev.ev_liquido_r == pytest.approx(esperado)
    assert ev.n == 100


def test_ev_sem_amostra_nao_inventa():
    ev = calcular_ev([])
    assert ev.n == 0
    assert ev.ev_liquido_r == 0.0
    assert not ev.amostra_suficiente


def test_amostra_pequena_marca_confianca_insuficiente():
    ev = calcular_ev([2.0] * 5 + [-1.0] * 5)
    assert not ev.amostra_suficiente
    assert any("INSUFICIENTE" in m for m in ev.motivos)


def test_amostra_sem_perdas_e_recusada():
    """Nenhuma perda indica amostra curta, não estratégia perfeita."""
    ev = calcular_ev([2.0] * 12)
    assert not ev.amostra_suficiente
    assert any("nenhuma operação perdedora" in m for m in ev.motivos)


def test_ev_pessimista_usa_piso_do_intervalo():
    ev = calcular_ev([2.0] * 20 + [-1.0] * 20)
    assert ev.ev_pessimista_r < ev.ev_liquido_r
    assert ev.ic_win_rate.inferior < ev.p_ganho


def test_ev_positivo_mas_pessimista_negativo_e_sinalizado():
    """É o caso mais perigoso: parece bom e pode ser sorte da amostra."""
    random.seed(3)
    ev = calcular_ev([2.0 if random.random() < 0.40 else -1.0
                      for _ in range(35)])
    if ev.ev_liquido_r > 0 and ev.ev_pessimista_r <= 0:
        assert not ev.positivo_no_pior_caso
        assert any("sorte da amostra" in m for m in ev.motivos)


def test_custos_reduzem_o_ev():
    sem = calcular_ev([2.0] * 40 + [-1.0] * 60, custos_r=0.0)
    com = calcular_ev([2.0] * 40 + [-1.0] * 60, custos_r=0.10)
    assert com.ev_liquido_r < sem.ev_liquido_r


def test_win_rate_alto_com_ev_negativo():
    """95% de acerto ganhando 0,3R e perdendo 8R dá expectativa negativa."""
    ev = calcular_ev([0.3] * 95 + [-8.0] * 5)
    assert ev.p_ganho == pytest.approx(0.95)
    assert ev.ev_liquido_r < 0
    assert not ev.positivo


# ---------------------------------------------------------- kelly e ruína
def test_kelly_e_limitado_a_25_por_cento():
    """Kelly cheio assume p conhecido com exatidão; não é o caso aqui."""
    assert kelly_fraction(0.99, 5.0) <= 0.25


def test_kelly_zero_sem_vantagem():
    assert kelly_fraction(0.30, 1.0) == 0.0


def test_ruina_certa_com_ev_negativo():
    assert risco_de_ruina(0.30, 1.5, 1.0, 0.01) == 1.0


def test_ruina_cresce_com_risco_por_trade():
    baixo = risco_de_ruina(0.45, 2.0, 1.0, 0.005, 0.5, 1000)
    alto = risco_de_ruina(0.45, 2.0, 1.0, 0.10, 0.5, 1000)
    assert alto > baixo


# ==================================================== divisão temporal
def test_divisao_e_cronologica_e_ordenada():
    s = dividir_temporal(3000)
    assert s.treino.inicio == 0
    assert s.treino.fim <= s.validacao.fim <= s.out_of_sample.fim
    assert s.out_of_sample.fim == 3000


def test_divisao_reserva_aquecimento():
    s = dividir_temporal(3000, barras_aquecimento=210)
    assert s.treino.fim > 210


def test_serie_curta_e_recusada():
    with pytest.raises(SplitError):
        dividir_temporal(250)


def test_fracoes_sem_espaco_para_oos_recusadas():
    with pytest.raises(SplitError):
        dividir_temporal(3000, frac_treino=0.8, frac_validacao=0.3)


# ====================================================== walk-forward
@pytest.fixture
def runner_fake():
    """Runner determinístico: expectativa cai na segunda metade da série.

    Simula o caso real que importa — estratégia que funcionou no passado e
    para de funcionar.
    """
    def _runner(velas, params):
        meio = 1_726_000_000_000
        recentes = sum(1 for c in velas if c.ts > meio)
        antigos = len(velas) - recentes
        trades = []
        for i in range(antigos // 300):
            trades.append(tr(1.5, opened=i * 1000, symbol="OLD"))
        for i in range(recentes // 300):
            trades.append(tr(-0.5, opened=i * 1000 + 500_000, symbol="NEW"))
        stats = BacktestStats(
            trades=len(trades),
            expectancy_r=(sum(t.pnl_r for t in trades) / len(trades)
                          if trades else 0.0))
        return stats, trades
    return _runner


def test_walk_forward_separa_is_de_oos(velas, runner_fake):
    rel = walk_forward(velas[:3000], runner_fake,
                       parametros_fixos={}, n_ciclos=3)
    chaves_oos = {(t.symbol, t.opened_at, t.side) for t in rel.trades_oos}
    assert rel.n_ciclos >= 2
    # Nenhum trade out-of-sample pode repetir entre ciclos.
    total_por_ciclo = sum(len(c.trades) for c in rel.ciclos)
    assert total_por_ciclo == len(chaves_oos)


def test_walk_forward_exige_ao_menos_dois_ciclos(velas, runner_fake):
    with pytest.raises(SplitError):
        walk_forward(velas[:3000], runner_fake, n_ciclos=1)


def test_walk_forward_serie_curta_recusada(runner_fake):
    from investai.models import Candle
    curta = [Candle(ts=i * 3_600_000, open=1, high=1, low=1, close=1, volume=1)
             for i in range(300)]
    with pytest.raises(SplitError):
        walk_forward(curta, runner_fake, n_ciclos=4)


def test_walk_forward_calcula_degradacao(velas, runner_fake):
    rel = walk_forward(velas[:3000], runner_fake, n_ciclos=3)
    assert "expectancy_r" in rel.degradacao
    assert rel.ev_oos is not None


def test_modo_rolante_e_ancorado_ambos_funcionam(velas, runner_fake):
    for ancorado in (True, False):
        rel = walk_forward(velas[:3000], runner_fake, n_ciclos=3,
                           ancorado=ancorado)
        assert rel.modo == ("ancorado" if ancorado else "rolante")


def test_expectativa_oos_nao_positiva_gera_aviso(velas):
    def perdedor(velas_, params):
        trades = [tr(-0.5, opened=i * 1000) for i in range(40)]
        return BacktestStats(trades=40, expectancy_r=-0.5), trades
    rel = walk_forward(velas[:3000], perdedor, n_ciclos=3)
    assert any("não é positiva" in a for a in rel.avisos)


# ======================================================== Monte Carlo
@pytest.fixture
def retornos_com_vantagem():
    random.seed(11)
    return [2.0 if random.random() < 0.45 else -1.0 for _ in range(200)]


def test_monte_carlo_devolve_distribuicao(retornos_com_vantagem):
    r = monte_carlo(retornos_com_vantagem, n_simulacoes=500)
    assert r.n_simulacoes == 500
    assert r.max_drawdown_pct.p95 >= r.max_drawdown_pct.mediana
    assert r.retorno_final_pct.p05 <= r.retorno_final_pct.p95


def test_risco_alto_por_trade_eleva_a_ruina(retornos_com_vantagem):
    """A MESMA estratégia é segura a 0,5% e ruinosa a 10% por operação."""
    conservador = monte_carlo(retornos_com_vantagem, n_simulacoes=500,
                              risco_por_trade_frac=0.005)
    agressivo = monte_carlo(retornos_com_vantagem, n_simulacoes=500,
                            risco_por_trade_frac=0.10)
    assert agressivo.prob_ruina > conservador.prob_ruina
    assert agressivo.drawdown_p95 > conservador.drawdown_p95
    assert any("inaceitável" in a for a in agressivo.avisos)


def test_armadilha_de_win_rate_alto_aparece_no_monte_carlo():
    """95% de acerto com perda grande na exceção: maioria das simulações
    termina no prejuízo."""
    r = monte_carlo([0.3] * 95 + [-8.0] * 5, n_simulacoes=1000,
                    risco_por_trade_frac=0.005)
    assert r.prob_prejuizo > 0.4
    assert r.retorno_final_pct.mediana < 0


def test_bootstrap_em_blocos_e_mais_pessimista_quando_perdas_agrupam():
    """Reamostrar operação a operação destrói o agrupamento de perdas e
    subestima o drawdown real."""
    agrupada = ([2.0] * 12 + [-1.0] * 8) * 10
    c = comparar_modos(agrupada, n_simulacoes=800, risco_por_trade_frac=0.01)
    assert c["agrupamento"]["drawdown_p95_blocos"] > c["agrupamento"]["drawdown_p95_iid"]
    assert c["agrupamento"]["piora_relativa"] > 0.20


def test_amostra_pequena_gera_aviso_de_limitacao():
    r = monte_carlo([2.0, -1.0, 2.0, -1.0, 1.0], n_simulacoes=200)
    assert any("herda a limitação da amostra" in a for a in r.avisos)


def test_monte_carlo_sem_amostra_nao_simula():
    r = monte_carlo([], n_simulacoes=100)
    assert r.n_simulacoes == 0
    assert r.avisos


def test_monte_carlo_e_reprodutivel(retornos_com_vantagem):
    a = monte_carlo(retornos_com_vantagem, n_simulacoes=300, seed=42)
    b = monte_carlo(retornos_com_vantagem, n_simulacoes=300, seed=42)
    assert a.drawdown_p95 == b.drawdown_p95


def test_modo_invalido_recusado(retornos_com_vantagem):
    with pytest.raises(ValueError):
        monte_carlo(retornos_com_vantagem, modo="mágico")


def test_risco_fora_da_faixa_recusado(retornos_com_vantagem):
    with pytest.raises(ValueError):
        monte_carlo(retornos_com_vantagem, risco_por_trade_frac=1.5)


# ========================================================= overfitting
def test_estrategia_robusta_e_aprovada():
    r = avaliar_overfitting(
        stats_is=BacktestStats(trades=120, expectancy_r=0.35,
                               profit_factor=1.8, win_rate=0.52),
        stats_oos=BacktestStats(trades=60, expectancy_r=0.30,
                                profit_factor=1.7, win_rate=0.50),
        trades_oos=[tr(1.0)] * 30 + [tr(-0.5)] * 30,
        stats_por_periodo=[BacktestStats(trades=20, expectancy_r=x)
                           for x in (0.3, 0.25, 0.4, 0.2)],
        n_parametros=4,
        resultados_vizinhos=[0.28, 0.32, 0.26, 0.31], resultado_central=0.30)
    assert r.veredicto is VeredictoOverfit.ROBUSTO
    assert r.aprovado


def test_overfitting_classico_e_reprovado():
    r = avaliar_overfitting(
        stats_is=BacktestStats(trades=200, expectancy_r=0.90,
                               profit_factor=3.5, win_rate=0.65),
        stats_oos=BacktestStats(trades=50, expectancy_r=0.05,
                                profit_factor=1.05, win_rate=0.40),
        trades_oos=[tr(20, 200)] + [tr(15, 150)] + [tr(12, 120)]
                   + [tr(0.2, 2)] * 20 + [tr(-0.8, -8)] * 27,
        stats_por_periodo=[BacktestStats(trades=12, expectancy_r=x)
                           for x in (0.9, -0.3, -0.2, 0.1)],
        n_parametros=9,
        resultados_vizinhos=[0.1, -0.2, 0.05, -0.1], resultado_central=0.90)
    assert r.veredicto is VeredictoOverfit.PROVAVEL_OVERFITTING
    assert not r.aprovado
    assert r.n_disparados >= 2


def test_amostra_pequena_e_indeterminada_nao_aprovada():
    """Ausência de evidência não é evidência de robustez."""
    r = avaliar_overfitting(
        stats_is=BacktestStats(trades=40, expectancy_r=1.2),
        stats_oos=BacktestStats(trades=9, expectancy_r=1.5, win_rate=0.9),
        trades_oos=[tr(3.0)] * 8 + [tr(-0.5)], n_parametros=3)
    assert r.veredicto is VeredictoOverfit.INDETERMINADO
    assert not r.aprovado
    assert "NÃO é aprovação" in r.resumo


def test_um_sinal_isolado_e_suspeito_nao_condena():
    r = avaliar_overfitting(
        stats_is=BacktestStats(trades=200, expectancy_r=1.0),
        stats_oos=BacktestStats(trades=60, expectancy_r=0.30,
                                profit_factor=1.6, win_rate=0.5),
        trades_oos=[tr(1.0)] * 30 + [tr(-0.5)] * 30,
        stats_por_periodo=[BacktestStats(trades=20, expectancy_r=x)
                           for x in (0.3, 0.25, 0.4, 0.2)],
        n_parametros=4,
        resultados_vizinhos=[0.28, 0.31], resultado_central=0.30)
    assert r.veredicto is VeredictoOverfit.SUSPEITO


def test_sinal_de_concentracao_detecta_lucro_de_poucos_trades():
    s = sinal_concentracao([tr(10, 1000)] + [tr(0.1, 10)] * 30)
    assert s.disparou
    assert "3 melhores" in s.explicacao


def test_concentracao_nao_se_aplica_a_resultado_negativo():
    assert not sinal_concentracao([tr(-1, -100)] * 10).disparou


def test_sinal_de_graus_de_liberdade():
    assert sinal_graus_de_liberdade(10, 50).disparou        # 5 por parâmetro
    assert not sinal_graus_de_liberdade(2, 100).disparou    # 50 por parâmetro


def test_graus_de_liberdade_sem_parametros_nao_dispara():
    assert not sinal_graus_de_liberdade(0, 10).disparou


def test_sinal_de_consistencia_precisa_de_tres_periodos():
    assert not sinal_consistencia([BacktestStats(expectancy_r=1.0)]).disparou


def test_sinal_de_consistencia_detecta_instabilidade():
    s = sinal_consistencia([BacktestStats(expectancy_r=x)
                            for x in (1.0, -0.3, -0.2, -0.1)])
    assert s.disparou


def test_sinal_de_degradacao():
    s = sinal_degradacao(BacktestStats(expectancy_r=1.0),
                         BacktestStats(expectancy_r=0.1))
    assert s.disparou
    assert "não sobreviveu" in s.explicacao


# -------------------------------------------------- análise de sensibilidade
def test_pico_isolado_dispara_instabilidade():
    def pico(p):
        return 0.9 if (p["ema"] == 9 and p["rsi"] == 14) else -0.1
    a = analise_sensibilidade({"ema": 9, "rsi": 14}, pico)
    assert sinal_estabilidade(a["resultados_vizinhos"],
                              a["resultado_central"]).disparou


def test_plato_nao_dispara_instabilidade():
    def plato(p):
        return 0.30 - abs(p["ema"] - 9) * 0.005
    a = analise_sensibilidade({"ema": 9, "rsi": 14}, plato)
    assert not sinal_estabilidade(a["resultados_vizinhos"],
                                  a["resultado_central"]).disparou


def test_sensibilidade_ignora_parametros_nao_numericos():
    a = analise_sensibilidade({"ema": 9, "modo": "trend", "ativo": True},
                              lambda p: 1.0)
    assert a["n_parametros_testados"] == 1


def test_estabilidade_sem_vizinhos_nao_dispara():
    assert not sinal_estabilidade([], 0.5).disparou
