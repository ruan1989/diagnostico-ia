"""Portfólio: correlação, falsa diversificação e stress test."""
import random

import pytest

from investai.models import Candle, Position, Side
from investai.portfolio import (
    CENARIOS_PADRAO, Cenario, analisar_diversificacao, aplicar_cenario,
    apostas_efetivas, dependencia_de_cauda, matriz_correlacao, pearson,
    retornos, rodar_stress_test,
)


def serie(closes, ts0=0, passo=3_600_000):
    return [Candle(ts=ts0 + i * passo, open=c, high=c * 1.01, low=c * 0.99,
                   close=c, volume=1000.0) for i, c in enumerate(closes)]


def pos(symbol, notional=250.0, side=Side.LONG, entry=100.0, stop=98.0):
    return Position(symbol=symbol, side=side, size=notional / entry,
                    entry=entry, stop_loss=stop, take_profits=[],
                    opened_at=0, notional_usd=notional)


# ============================================================ Pearson
def test_pearson_de_series_identicas_e_um():
    a = [random.gauss(0, 1) for _ in range(50)]
    assert pearson(a, a) == pytest.approx(1.0)


def test_pearson_de_series_opostas_e_menos_um():
    a = [float(i) for i in range(50)]
    assert pearson(a, [-x for x in a]) == pytest.approx(-1.0)


def test_pearson_exige_amostra_minima():
    assert pearson([1.0, 2.0], [1.0, 2.0]) is None


def test_pearson_de_serie_constante_e_indefinido():
    assert pearson([1.0] * 30, [float(i) for i in range(30)]) is None


def test_retornos_ignora_divisao_por_zero():
    assert len(retornos([100.0, 0.0, 50.0])) == 1


# ================================================= dependência de cauda
def test_series_independentes_tem_dependencia_de_cauda_zero():
    random.seed(1)
    a = [random.gauss(0, 1) for _ in range(500)]
    b = [random.gauss(0, 1) for _ in range(500)]
    assert dependencia_de_cauda(a, b) == pytest.approx(0.0, abs=0.12)


def test_series_identicas_tem_dependencia_de_cauda_um():
    random.seed(2)
    a = [random.gauss(0, 1) for _ in range(300)]
    assert dependencia_de_cauda(a, a) == pytest.approx(1.0)


def test_dependencia_de_cauda_detecta_correlacao_so_na_queda():
    """O caso que Pearson condicionado reportaria ERRADO: condicionar na
    cauda de uma série trunca a variância dela e enviesa a correlação para
    baixo, sugerindo proteção onde não há."""
    random.seed(3)
    a, b = [], []
    for _ in range(600):
        if random.random() < 0.15:
            choque = random.gauss(-3, 0.5)
            a.append(choque)
            b.append(choque + random.gauss(0, 0.3))
        else:
            a.append(random.gauss(0, 1))
            b.append(random.gauss(0, 1))
    assert dependencia_de_cauda(a, b) > 0.5


def test_dependencia_de_cauda_exige_amostra():
    assert dependencia_de_cauda([1.0] * 10, [1.0] * 10) is None


# ==================================================== matriz e apostas
def test_matriz_e_simetrica_com_diagonal_um():
    random.seed(4)
    series = {s: serie([100 + random.gauss(0, 2) for _ in range(200)])
              for s in ("A", "B", "C")}
    m = matriz_correlacao(series)
    for a in m.simbolos:
        assert m.par(a, a) == 1.0
        for b in m.simbolos:
            va, vb = m.par(a, b), m.par(b, a)
            if va is not None and vb is not None:
                assert va == pytest.approx(vb)


def test_matriz_alinha_por_timestamp():
    """Comparar retornos de barras diferentes produz correlação sem sentido."""
    a = serie([100 + i for i in range(200)], ts0=0)
    b = serie([100 + i for i in range(200)], ts0=50 * 3_600_000)
    m = matriz_correlacao({"A": a, "B": b})
    assert m.janela == 200
    # Só os timestamps em comum entram.
    assert m.par("A", "B") is not None or m.avisos


def test_matriz_avisa_quando_ha_pouca_sobreposicao():
    a = serie([100.0] * 200, ts0=0)
    b = serie([100.0] * 200, ts0=500 * 3_600_000)
    m = matriz_correlacao({"A": a, "B": b})
    assert m.avisos


@pytest.mark.parametrize("rho,esperado", [
    (0.0, 5.0), (0.8, 1.19), (0.95, 1.04),
])
def test_apostas_efetivas(rho, esperado):
    assert apostas_efetivas(rho, 5) == pytest.approx(esperado, abs=0.02)


def test_apostas_efetivas_sem_correlacao_conhecida():
    assert apostas_efetivas(None, 4) == 4.0


def test_apostas_efetivas_com_zero_posicoes():
    assert apostas_efetivas(0.5, 0) == 0.0


# ========================================== falsa diversificação
def test_falsa_diversificacao_detectada_em_criptos_correlacionadas(provider):
    """Cinco criptos não são cinco apostas."""
    simbolos = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT"]
    m = matriz_correlacao({s: provider.candles(s, "1H", limit=500)
                           for s in simbolos}, janela=400)
    assert m.media() > 0.4          # o gerador tem fator de mercado comum
    a = analisar_diversificacao([pos(s) for s in simbolos], m)
    assert a.falsa_diversificacao
    assert a.apostas_efetivas < 3.0
    assert any("FALSA DIVERSIFICAÇÃO" in w for w in a.avisos)


def test_carteira_de_um_grupo_e_aposta_unica():
    a = analisar_diversificacao([pos("BTCUSDT"), pos("ETHUSDT")])
    assert any("aposta única" in w for w in a.avisos)


def test_concentracao_alta_e_sinalizada():
    a = analisar_diversificacao([pos("BTCUSDT", notional=5000.0),
                                 pos("XRPUSDT", notional=100.0)])
    assert a.concentracao_hhi > 0.5
    assert any("concentração alta" in w for w in a.avisos)


def test_hhi_de_carteira_equilibrada():
    a = analisar_diversificacao([pos(s) for s in
                                 ("BTCUSDT", "PETR4", "MXRF11", "BOVA11")])
    assert a.concentracao_hhi == pytest.approx(0.25, abs=0.01)


def test_carteira_vazia_nao_explode():
    a = analisar_diversificacao([])
    assert a.n_posicoes == 0
    assert a.apostas_efetivas == 0.0


def test_dependencia_de_cauda_alta_gera_aviso(provider):
    simbolos = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    m = matriz_correlacao({s: provider.candles(s, "1H", limit=500)
                           for s in simbolos}, janela=400)
    a = analisar_diversificacao([pos(s) for s in simbolos], m)
    if (m.media(em_cauda=True) or 0) > 0.45:
        assert any("dependência de cauda" in w for w in a.avisos)


# ============================================================= stress
def test_cenarios_padrao_cobrem_o_pedido():
    nomes = {c.nome for c in CENARIOS_PADRAO}
    for esperado in ("btc_-10", "btc_-20", "btc_-30", "ibov_-10", "ibov_-20",
                     "dolar_+15", "juros_+300bps", "liquidez_-70",
                     "spread_x5", "correlacao_1"):
        assert esperado in nomes


def test_carteira_conservadora_sobrevive_a_tudo():
    conservadora = [pos("BTCUSDT", 256.0, entry=64000.0, stop=62720.0),
                    pos("MXRF11", 250.0, entry=10.3, stop=10.0),
                    pos("PETR4", 246.0, entry=38.0, stop=37.0)]
    r = rodar_stress_test(conservadora, 1000.0)
    assert not r.cenarios_de_ruina
    assert r.pior_drawdown_pct < 25.0


def test_carteira_alavancada_e_arruinada():
    """A mesma disciplina de 0,5% por operação, a 25x, não sobrevive."""
    agressiva = [pos(s, 5000.0, entry=100.0, stop=99.0)
                 for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT",
                           "DOGEUSDT")]
    r = rodar_stress_test(agressiva, 1000.0)
    assert r.cenarios_de_ruina
    assert r.pior_drawdown_pct > 100.0


def test_gap_fura_o_stop():
    p = [pos("BTCUSDT", 1000.0, entry=100.0, stop=99.0)]   # stop de 1%
    cenario = Cenario("gap", "gap de 8%", choque_default_pct=-8.0,
                      fracao_em_gap=1.0)
    r = aplicar_cenario(p, 1000.0, cenario)
    assert r.stops_furados == 1
    assert r.perda_realista_usd > r.perda_planejada_usd
    assert any("furado" in a for a in r.avisos)


def test_stop_largo_nao_e_furado_por_gap_pequeno():
    p = [pos("BTCUSDT", 1000.0, entry=100.0, stop=85.0)]   # stop de 15%
    cenario = Cenario("gap", "gap de 8%", choque_default_pct=-8.0,
                      fracao_em_gap=1.0)
    assert aplicar_cenario(p, 1000.0, cenario).stops_furados == 0


def test_short_ganha_em_cenario_de_queda():
    p = [pos("BTCUSDT", 1000.0, side=Side.SHORT, entry=100.0, stop=102.0)]
    cenario = Cenario("queda", "queda de 20%", choque_default_pct=-20.0)
    r = aplicar_cenario(p, 1000.0, cenario)
    assert r.posicoes[0].perda_com_stop_usd == 0.0


def test_spread_ampliado_custa_mesmo_sem_choque_de_preco():
    p = [pos("BTCUSDT", 1000.0)]
    cenario = Cenario("spread", "spread x5", choque_default_pct=0.0,
                      multiplicador_spread=5.0)
    r = aplicar_cenario(p, 1000.0, cenario)
    assert r.perda_planejada_usd == 0.0
    assert r.perda_realista_usd > 0.0


def test_liquidez_reduzida_piora_a_saida():
    p = [pos("BTCUSDT", 1000.0)]
    base = aplicar_cenario(p, 1000.0, Cenario("a", "", choque_default_pct=-5.0,
                                              multiplicador_spread=2.0))
    seca = aplicar_cenario(p, 1000.0,
                           Cenario("b", "", choque_default_pct=-5.0,
                                   multiplicador_spread=2.0,
                                   reducao_liquidez_pct=70.0))
    assert seca.perda_realista_usd > base.perda_realista_usd


def test_correlacao_total_avisa_que_diversificacao_nao_protege():
    p = [pos("BTCUSDT"), pos("MXRF11")]
    r = aplicar_cenario(p, 1000.0, Cenario("c", "", choque_default_pct=-15.0,
                                           correlacao_total=True))
    assert any("correlação 1" in a for a in r.avisos)


def test_amplificacao_mede_o_que_o_stop_nao_captura():
    p = [pos("BTCUSDT", 1000.0, entry=100.0, stop=99.0)]
    r = aplicar_cenario(p, 1000.0, Cenario("x", "", choque_default_pct=-20.0,
                                           fracao_em_gap=0.5,
                                           multiplicador_spread=3.0))
    assert r.amplificacao > 1.5
    assert any("perda real é" in a for a in r.avisos)


def test_ruina_acima_de_cem_por_cento_e_explicada():
    p = [pos(s, 5000.0, entry=100.0, stop=99.5)
         for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]
    r = aplicar_cenario(p, 1000.0, Cenario("crash", "", choque_default_pct=-30.0,
                                           fracao_em_gap=1.0,
                                           multiplicador_spread=5.0))
    assert r.drawdown_pct > 100.0
    assert any("RUÍNA" in a for a in r.avisos)


def test_capital_invalido_recusado():
    with pytest.raises(ValueError):
        rodar_stress_test([pos("BTCUSDT")], 0.0)


def test_relatorio_identifica_o_pior_cenario():
    r = rodar_stress_test([pos("BTCUSDT", 2000.0, entry=100.0, stop=99.0)],
                          1000.0)
    assert r.pior_cenario
    assert r.to_dict()["alavancagem_efetiva"] == pytest.approx(2.0)
