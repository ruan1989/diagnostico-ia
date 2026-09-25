"""Features, regime e confluência — incluindo a garantia contra lookahead."""
import pytest

from investai.analysis import (
    BARRAS_MINIMAS, DadosInsuficientes, SerieFeatures, avaliar, classificar,
    classificar_regime, extrair_features, melhor_direcao, montar_plano,
    prob_acerto_ajustada, PESOS,
)
from investai.analysis.confluence import PRIOR_WIN_RATE
from investai.config import SignalConfig
from investai.models import BacktestStats, MarketSnapshot, Regime, Side, SignalGrade


def test_pesos_somam_um():
    """Se os pesos não somam 1, o score deixa de ser comparável entre pares."""
    assert sum(PESOS.values()) == pytest.approx(1.0)


def test_features_exigem_historico_minimo(provider):
    curtas = provider.candles("BTCUSDT", "1H", limit=50)
    with pytest.raises(DadosInsuficientes):
        extrair_features("BTCUSDT", "1H", curtas)


def test_sem_lookahead_features_do_indice_nao_veem_o_futuro(velas):
    """Esta é a garantia central do sistema.

    As features calculadas no candle i têm que ser idênticas quer existam ou
    não candles depois de i. Se falhar, todo backtest é fantasia.
    """
    serie_completa = SerieFeatures("BTCUSDT", "1H", velas)
    for i in (400, 800, 1200):
        do_completo = serie_completa.at(i)
        so_ate_i = SerieFeatures("BTCUSDT", "1H", velas[:i + 1]).at(i)
        assert do_completo.to_dict() == so_ate_i.to_dict()


def test_caminho_rapido_equivale_ao_lento(velas):
    """SerieFeatures é otimização; não pode mudar nenhum número."""
    serie = SerieFeatures("BTCUSDT", "1H", velas)
    for i in (300, 700, len(velas) - 1):
        assert serie.at(i).to_dict() == extrair_features(
            "BTCUSDT", "1H", velas, indice=i).to_dict()


def test_indice_invalido_rejeitado(velas):
    serie = SerieFeatures("BTCUSDT", "1H", velas)
    with pytest.raises(DadosInsuficientes):
        serie.at(BARRAS_MINIMAS - 2)
    with pytest.raises(DadosInsuficientes):
        serie.at(len(velas))


@pytest.mark.parametrize("ef,es,et,adx_v,dip,dim,esperado", [
    (110, 105, 100, 30, 30, 10, Regime.TENDENCIA_ALTA),
    (90, 95, 100, 30, 10, 30, Regime.TENDENCIA_BAIXA),
    (100, 100, 100, 10, 15, 15, Regime.LATERAL),
])
def test_classificacao_de_regime(ef, es, et, adx_v, dip, dim, esperado):
    assert classificar_regime(ef, es, et, adx_v, dip, dim, 4.0, 1.0) is esperado


def test_volatil_sem_direcao_exige_atr_e_bandas_largas():
    assert classificar_regime(100, 100, 100, 12, 15, 15,
                              bb_width_pct=8.0, atr_pct=4.0) is Regime.VOLATIL_SEM_DIRECAO


def test_plano_stop_sempre_do_lado_certo(velas):
    f = SerieFeatures("BTCUSDT", "1H", velas).at(len(velas) - 1)
    plano_long = montar_plano(f, Side.LONG)
    assert plano_long["stop_loss"] < plano_long["entry"]
    assert all(t > plano_long["entry"] for t in plano_long["take_profits"])
    plano_short = montar_plano(f, Side.SHORT)
    assert plano_short["stop_loss"] > plano_short["entry"]
    assert all(t < plano_short["entry"] for t in plano_short["take_profits"])


def test_alvos_respeitam_multiplos_de_risco(velas):
    f = SerieFeatures("BTCUSDT", "1H", velas).at(len(velas) - 1)
    plano = montar_plano(f, Side.LONG, alvos_r=(2.0, 4.0))
    risco = plano["entry"] - plano["stop_loss"]
    assert plano["take_profits"][0] == pytest.approx(plano["entry"] + 2 * risco)
    assert plano["take_profits"][1] == pytest.approx(plano["entry"] + 4 * risco)


# --------------------------------------------------- shrinkage da probabilidade
def test_sem_historico_usa_o_prior():
    assert prob_acerto_ajustada(None) == pytest.approx(PRIOR_WIN_RATE)
    assert prob_acerto_ajustada(BacktestStats(trades=0)) == pytest.approx(PRIOR_WIN_RATE)


def test_amostra_pequena_e_encolhida_para_o_prior():
    """8 trades com 87,5% de acerto NÃO podem virar 'probabilidade de 87%'."""
    p = prob_acerto_ajustada(BacktestStats(trades=8, win_rate=0.875))
    assert p < 0.6
    assert p > PRIOR_WIN_RATE


def test_amostra_grande_domina_o_prior():
    p = prob_acerto_ajustada(BacktestStats(trades=400, win_rate=0.62))
    assert p == pytest.approx(0.62, abs=0.02)


def test_prob_nunca_chega_a_um():
    """Nenhuma amostra deve produzir 'quase 100% de acerto'."""
    p = prob_acerto_ajustada(BacktestStats(trades=1000, win_rate=1.0))
    assert p < 1.0


# ------------------------------------------------------------ classificação
def test_score_baixo_e_rejeitado():
    cfg = SignalConfig()
    grade, _ = classificar(50.0, BacktestStats(trades=100, win_rate=0.7,
                                               profit_factor=3.0, expectancy_r=1.0), cfg)
    assert grade is SignalGrade.REJEITADO


def test_score_alto_sem_amostra_fica_em_observacao():
    """O portão estatístico é o que impede 'score bonito' de virar ordem."""
    cfg = SignalConfig()
    grade, notas = classificar(95.0, BacktestStats(trades=3, win_rate=1.0,
                                                  profit_factor=99.0,
                                                  expectancy_r=2.0), cfg)
    assert grade is SignalGrade.C
    assert any("amostra" in n for n in notas)


def test_score_alto_com_historico_ruim_fica_em_observacao():
    cfg = SignalConfig()
    grade, notas = classificar(90.0, BacktestStats(trades=80, win_rate=0.30,
                                                   profit_factor=0.7,
                                                   expectancy_r=-0.4), cfg)
    assert grade is SignalGrade.C
    assert len(notas) >= 3


def test_grade_a_exige_score_e_estatistica():
    cfg = SignalConfig()
    bom = BacktestStats(trades=120, win_rate=0.58, profit_factor=2.1, expectancy_r=0.45)
    assert classificar(cfg.score_min_grade_a + 1, bom, cfg)[0] is SignalGrade.A
    assert classificar(cfg.score_min_grade_b + 1, bom, cfg)[0] is SignalGrade.B


# --------------------------------------------------------------- avaliação
def _feats(velas, tfs=("15m", "1H", "4H")):
    return {tf: SerieFeatures("BTCUSDT", tf, velas).at(len(velas) - 1) for tf in tfs}


def test_avaliar_produz_sinal_coerente(velas):
    cfg = SignalConfig()
    feats = _feats(velas)
    s = avaliar("BTCUSDT", feats, Side.LONG, cfg)
    assert 0.0 <= s.score <= 100.0
    assert s.stop_loss < s.entry
    assert len(s.fatores) == len(PESOS)
    assert s.invalidacao
    assert 0.0 < s.prob_acerto_estimada < 1.0


def test_volatilidade_extrema_reprova(velas):
    cfg = SignalConfig(max_atr_pct=0.01)   # limite impossível de cumprir
    s = avaliar("BTCUSDT", _feats(velas), Side.LONG, cfg)
    assert s.grade is SignalGrade.REJEITADO
    assert any("volatilidade" in n for n in s.notas)


def test_liquidez_baixa_reprova(velas):
    cfg = SignalConfig()
    snap = MarketSnapshot(symbol="BTCUSDT", last_price=60000.0,
                          volume_24h_usd=1000.0)
    s = avaliar("BTCUSDT", _feats(velas), Side.LONG, cfg, snapshot=snap)
    assert s.grade is SignalGrade.REJEITADO
    assert any("liquidez" in n for n in s.notas)


def test_funding_alto_penaliza_o_lado_da_multidao(velas):
    """Funding muito positivo deve desfavorecer LONG, não favorecer."""
    cfg = SignalConfig()
    feats = _feats(velas)
    neutro = MarketSnapshot(symbol="BTCUSDT", last_price=60000.0,
                            funding_rate=0.0, volume_24h_usd=1e9)
    esticado = MarketSnapshot(symbol="BTCUSDT", last_price=60000.0,
                              funding_rate=0.003, volume_24h_usd=1e9)
    fator_neutro = next(f for f in avaliar("BTCUSDT", feats, Side.LONG, cfg,
                                           snapshot=neutro).fatores
                        if f.nome == "funding")
    fator_esticado = next(f for f in avaliar("BTCUSDT", feats, Side.LONG, cfg,
                                             snapshot=esticado).fatores
                          if f.nome == "funding")
    assert fator_esticado.valor < fator_neutro.valor


def test_tf_principal_ausente_e_erro(velas):
    cfg = SignalConfig()
    with pytest.raises(KeyError):
        avaliar("BTCUSDT", _feats(velas, tfs=("15m",)), Side.LONG, cfg)


def test_melhor_direcao_escolhe_o_maior_score(velas):
    cfg = SignalConfig()
    feats = _feats(velas)
    escolhido = melhor_direcao("BTCUSDT", feats, cfg)
    long_s = avaliar("BTCUSDT", feats, Side.LONG, cfg)
    short_s = avaliar("BTCUSDT", feats, Side.SHORT, cfg)
    assert escolhido.score == max(long_s.score, short_s.score)
