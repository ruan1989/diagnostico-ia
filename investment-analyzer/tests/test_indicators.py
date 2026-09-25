"""Os indicadores são a base de tudo. Erro aqui contamina o sistema inteiro."""
import math

import pytest

from investai.indicators import (
    adx, atr, bollinger, donchian, ema, rma, rsi, sma, stoch_rsi, true_range,
    vwap_rolling, zscore,
)


def test_sma_valor_conhecido():
    assert sma([1, 2, 3, 4, 5], 3)[-1] == pytest.approx(4.0)


def test_indicadores_preservam_tamanho_e_alinhamento():
    """Alinhamento é o que impede lookahead: posição i vale para o candle i."""
    dados = [float(i) for i in range(1, 61)]
    for serie in (sma(dados, 10), ema(dados, 10), rma(dados, 10), rsi(dados, 14)):
        assert len(serie) == len(dados)
    # As primeiras posições não têm dados suficientes e devem ser None.
    assert sma(dados, 10)[:9] == [None] * 9
    assert sma(dados, 10)[9] is not None


def test_ema_semeada_com_sma():
    dados = [float(i) for i in range(1, 21)]
    e = ema(dados, 5)
    assert e[4] == pytest.approx(3.0)      # SMA de 1..5
    assert e[3] is None


def test_rsi_alta_monotonica_saturada():
    assert rsi([float(i) for i in range(1, 40)], 14)[-1] == pytest.approx(100.0)


def test_rsi_baixa_monotonica_saturada():
    assert rsi([float(i) for i in range(40, 1, -1)], 14)[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_serie_plana_fica_neutro():
    """Série sem variação não tem força de alta nem de baixa."""
    assert rsi([100.0] * 40, 14)[-1] == pytest.approx(50.0)


def test_true_range_usa_fechamento_anterior():
    highs, lows, closes = [10, 12], [8, 11], [9, 11.5]
    tr = true_range(highs, lows, closes)
    # gap de alta: |high[1] - close[0]| = |12 - 9| = 3 domina o range de 1
    assert tr[1] == pytest.approx(3.0)


def test_atr_positivo_e_alinhado():
    n = 60
    highs = [100 + i + 1 for i in range(n)]
    lows = [100 + i - 1 for i in range(n)]
    closes = [100 + i for i in range(n)]
    a = atr(highs, lows, closes, 14)
    assert a[13] is not None and a[12] is None
    assert a[-1] > 0


def test_donchian_exclui_candle_atual():
    """Um rompimento tem que ser medido contra o passado, não contra si mesmo."""
    highs = [10.0] * 20 + [50.0]
    lows = [5.0] * 20 + [1.0]
    hi, lo = donchian(highs, lows, 20)
    assert hi[20] == pytest.approx(10.0)   # ignora a máxima de 50 do próprio candle
    assert lo[20] == pytest.approx(5.0)
    assert hi[19] is None                  # sem 20 candles anteriores


def test_bollinger_largura_cresce_com_volatilidade():
    calmo = [100.0 + (i % 2) * 0.1 for i in range(40)]
    agitado = [100.0 + (i % 2) * 10.0 for i in range(40)]
    u1, _, l1 = bollinger(calmo, 20)
    u2, _, l2 = bollinger(agitado, 20)
    assert (u2[-1] - l2[-1]) > (u1[-1] - l1[-1])


def test_bollinger_serie_plana_tem_largura_zero():
    u, m, l = bollinger([50.0] * 30, 20)
    assert u[-1] == pytest.approx(l[-1]) == pytest.approx(m[-1])


def test_adx_alto_em_tendencia_e_baixo_em_serra():
    n = 120
    tend_c = [100 + i * 1.5 for i in range(n)]
    a_tend, dip, dim = adx([c + 1 for c in tend_c], [c - 1 for c in tend_c], tend_c, 14)
    serra_c = [100 + (i % 2) * 2.0 for i in range(n)]
    a_serra, _, _ = adx([c + 1 for c in serra_c], [c - 1 for c in serra_c], serra_c, 14)
    assert a_tend[-1] > a_serra[-1]
    assert dip[-1] > dim[-1]               # tendência de alta: +DI domina


def test_adx_inverte_di_em_queda():
    n = 120
    closes = [300 - i * 1.5 for i in range(n)]
    _, dip, dim = adx([c + 1 for c in closes], [c - 1 for c in closes], closes, 14)
    assert dim[-1] > dip[-1]


def test_stoch_rsi_dentro_de_0_100():
    dados = [100 + math.sin(i / 3) * 10 for i in range(120)]
    valores = [v for v in stoch_rsi(dados) if v is not None]
    assert valores and all(0.0 <= v <= 100.0 for v in valores)


def test_vwap_pondera_por_volume():
    highs = lows = closes = [10.0] * 19 + [20.0]
    volumes = [1.0] * 19 + [1000.0]
    v = vwap_rolling(highs, lows, closes, volumes, 20)
    assert v[-1] > 19.0      # o candle de volume enorme domina a média


def test_zscore_serie_plana_e_zero():
    assert zscore([5.0] * 60, 50) == pytest.approx(0.0)


def test_periodo_invalido_rejeitado():
    for fn in (sma, ema, rma):
        with pytest.raises(ValueError):
            fn([1.0, 2.0, 3.0], 0)


def test_series_curtas_nao_estouram():
    """Dados insuficientes devem devolver None, não exceção."""
    assert ema([1.0], 10) == [None]
    assert rsi([1.0, 2.0], 14) == [None, None]
    assert atr([1.0], [1.0], [1.0], 14) == [None]
