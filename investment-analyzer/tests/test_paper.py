"""Simulador de execução: as fricções que backtests otimistas ignoram."""
import pytest

from investai.models import Candle, Side
from investai.trading.paper import (
    ConfigSimulador, LivroSintetico, SimuladorExecucao, StatusOrdem, TipoOrdem,
)


def c(o, h, l, cl, v=1_000_000.0, ts=0):
    return Candle(ts=ts, open=o, high=h, low=l, close=cl, volume=v)


@pytest.fixture
def sim():
    # Latência mínima para não interferir nos testes de fill.
    return SimuladorExecucao(ConfigSimulador(latencia_ms_min=1,
                                             latencia_ms_max=1))


# =========================================== a premissa central recusada
def test_tocar_o_nivel_sem_volume_nao_executa(sim):
    """"O preço tocou meu limite" não significa que a ordem executou: se a
    fila à frente não foi consumida, ela continua pendente."""
    o = sim.enviar("BTCUSDT", Side.LONG, TipoOrdem.LIMIT, 1.0,
                   preco_limite=100.0, agora_ms=0)
    sim.processar_candle(c(101, 102, 100.0, 101.5, v=0.5, ts=60_000))
    assert o.size_executada == 0.0
    assert o.status is StatusOrdem.PENDENTE


def test_atravessar_o_nivel_executa_integralmente(sim):
    o = sim.enviar("BTCUSDT", Side.LONG, TipoOrdem.LIMIT, 1.0,
                   preco_limite=100.0, agora_ms=0)
    sim.processar_candle(c(101, 102, 99.0, 100.5, ts=60_000))
    assert o.status is StatusOrdem.EXECUTADA
    assert o.preco_medio == pytest.approx(100.0)


def test_tocar_com_volume_alto_executa(sim):
    o = sim.enviar("BTCUSDT", Side.LONG, TipoOrdem.LIMIT, 1.0,
                   preco_limite=100.0, agora_ms=0)
    sim.processar_candle(c(101, 102, 100.0, 101.5, v=1e7, ts=60_000))
    assert o.status is StatusOrdem.EXECUTADA


def test_volume_intermediario_da_fill_parcial(sim):
    o = sim.enviar("BTCUSDT", Side.LONG, TipoOrdem.LIMIT, 1.0,
                   preco_limite=100.0, agora_ms=0)
    sim.processar_candle(c(101, 102, 100.0, 101.5, v=150.0, ts=60_000))
    assert o.status is StatusOrdem.PARCIAL
    assert 0 < o.size_executada < 1.0


def test_fill_parcial_pode_ser_desabilitado():
    s = SimuladorExecucao(ConfigSimulador(latencia_ms_min=1,
                                          latencia_ms_max=1,
                                          permitir_fill_parcial=False))
    o = s.enviar("BTCUSDT", Side.LONG, TipoOrdem.LIMIT, 1.0,
                 preco_limite=100.0, agora_ms=0)
    s.processar_candle(c(101, 102, 100.0, 101.5, v=150.0, ts=60_000))
    assert o.size_executada == 0.0


def test_preco_longe_do_limite_nao_executa(sim):
    o = sim.enviar("BTCUSDT", Side.LONG, TipoOrdem.LIMIT, 1.0,
                   preco_limite=90.0, agora_ms=0)
    sim.processar_candle(c(101, 102, 100.0, 101.5, ts=60_000))
    assert o.status is StatusOrdem.PENDENTE


# ============================================================= latência
def test_ordem_nao_executa_na_barra_em_que_foi_criada():
    s = SimuladorExecucao(ConfigSimulador(latencia_ms_min=90_000,
                                          latencia_ms_max=90_000))
    o = s.enviar("BTCUSDT", Side.LONG, TipoOrdem.MARKET, 1.0, agora_ms=0)
    s.processar_candle(c(100, 101, 99, 100.5, ts=0))
    assert o.status is StatusOrdem.PENDENTE
    s.processar_candle(c(100, 101, 99, 100.5, ts=120_000))
    assert o.status is StatusOrdem.EXECUTADA


# ================================================== spread e slippage
def test_compra_a_mercado_sai_acima_da_referencia(sim):
    o = sim.enviar("BTCUSDT", Side.LONG, TipoOrdem.MARKET, 0.1, agora_ms=0)
    sim.processar_candle(c(100, 101, 99, 100.5, ts=60_000))
    assert o.preco_medio > 100.0


def test_venda_a_mercado_sai_abaixo_da_referencia(sim):
    o = sim.enviar("BTCUSDT", Side.SHORT, TipoOrdem.MARKET, 0.1, agora_ms=0)
    sim.processar_candle(c(100, 101, 99, 100.5, ts=60_000))
    assert o.preco_medio < 100.0


def test_execucao_nunca_sai_da_faixa_do_candle(sim):
    """O mercado não negociou fora do range da barra."""
    o = sim.enviar("BTCUSDT", Side.LONG, TipoOrdem.MARKET, 1000.0, agora_ms=0)
    candle = c(100, 100.5, 99.5, 100.2, ts=60_000)
    sim.processar_candle(candle)
    assert candle.low <= o.preco_medio <= candle.high


def test_ordem_maior_executa_pior():
    livro = LivroSintetico(profundidade_topo_usd=10_000, niveis=8)
    precos = []
    for notional in (5_000, 40_000, 70_000):
        s = SimuladorExecucao(ConfigSimulador(latencia_ms_min=1,
                                              latencia_ms_max=1), livro=livro)
        o = s.enviar("BTCUSDT", Side.LONG, TipoOrdem.MARKET,
                     notional / 100.0, agora_ms=0)
        s.processar_candle(c(100, 110, 90, 100, ts=60_000))
        precos.append(o.preco_medio)
    assert precos == sorted(precos)


def test_ordem_maior_que_o_livro_nao_preenche_tudo():
    livro = LivroSintetico(profundidade_topo_usd=1_000, niveis=3)
    s = SimuladorExecucao(ConfigSimulador(latencia_ms_min=1,
                                          latencia_ms_max=1), livro=livro)
    o = s.enviar("BTCUSDT", Side.LONG, TipoOrdem.MARKET, 500.0, agora_ms=0)
    s.processar_candle(c(100, 110, 90, 100, ts=60_000))
    assert o.status is StatusOrdem.PARCIAL
    assert o.size_executada < 500.0


# ================================================================ taxas
def test_limitada_paga_maker_e_mercado_paga_taker():
    cfg = ConfigSimulador(latencia_ms_min=1, latencia_ms_max=1,
                          taxa_maker_pct=0.02, taxa_taker_pct=0.06)
    s1 = SimuladorExecucao(cfg)
    lim = s1.enviar("B", Side.LONG, TipoOrdem.LIMIT, 1.0, preco_limite=100.0,
                    agora_ms=0)
    s1.processar_candle(c(101, 102, 99, 100.5, ts=60_000))

    s2 = SimuladorExecucao(cfg)
    mkt = s2.enviar("B", Side.LONG, TipoOrdem.MARKET, 1.0, agora_ms=0)
    s2.processar_candle(c(100, 101, 99, 100.5, ts=60_000))

    assert lim.fills[0]["tipo_taxa"] == "maker"
    assert mkt.fills[0]["tipo_taxa"] == "taker"
    assert mkt.taxas_usd > lim.taxas_usd


# ============================================================ gatilhos
def test_stop_dispara_e_vira_mercado(sim):
    o = sim.enviar("BTCUSDT", Side.SHORT, TipoOrdem.STOP, 1.0,
                   preco_gatilho=98.0, reduce_only=True, agora_ms=0)
    sim.processar_candle(c(100, 101, 99.5, 100, ts=60_000))
    assert not o.gatilho_disparado
    sim.processar_candle(c(100, 100.5, 97.0, 97.5, ts=120_000))
    assert o.gatilho_disparado
    assert o.status is StatusOrdem.EXECUTADA


def test_stop_limit_dispara_mas_pode_nao_executar(sim):
    o = sim.enviar("BTCUSDT", Side.SHORT, TipoOrdem.STOP_LIMIT, 1.0,
                   preco_gatilho=98.0, preco_limite=97.9, agora_ms=0)
    # Dispara o gatilho e cai muito além do limite: a limitada não pega.
    sim.processar_candle(c(100, 100.2, 90.0, 90.5, ts=60_000))
    assert o.gatilho_disparado


def test_take_profit_de_long_dispara_na_alta(sim):
    o = sim.enviar("BTCUSDT", Side.SHORT, TipoOrdem.TAKE_PROFIT, 1.0,
                   preco_gatilho=110.0, reduce_only=True, agora_ms=0)
    sim.processar_candle(c(100, 105, 99, 104, ts=60_000))
    assert not o.gatilho_disparado
    sim.processar_candle(c(104, 112, 103, 111, ts=120_000))
    assert o.gatilho_disparado


# ========================================================== validação
@pytest.mark.parametrize("tipo,kw", [
    (TipoOrdem.LIMIT, {}),
    (TipoOrdem.STOP, {}),
    (TipoOrdem.STOP_LIMIT, {"preco_gatilho": 100.0}),
])
def test_ordens_sem_precos_obrigatorios_sao_rejeitadas(sim, tipo, kw):
    o = sim.enviar("B", Side.LONG, tipo, 1.0, agora_ms=0, **kw)
    assert o.status is StatusOrdem.REJEITADA
    assert o.motivo


def test_tamanho_invalido_rejeitado(sim):
    assert sim.enviar("B", Side.LONG, TipoOrdem.MARKET,
                      0.0).status is StatusOrdem.REJEITADA


def test_cancelamento(sim):
    o = sim.enviar("B", Side.LONG, TipoOrdem.LIMIT, 1.0, preco_limite=50.0,
                   agora_ms=0)
    assert sim.cancelar(o.id)
    assert o.status is StatusOrdem.CANCELADA
    assert not sim.cancelar(o.id)      # já finalizada


def test_pendentes_filtra_por_symbol(sim):
    sim.enviar("A", Side.LONG, TipoOrdem.LIMIT, 1.0, preco_limite=1.0)
    sim.enviar("B", Side.LONG, TipoOrdem.LIMIT, 1.0, preco_limite=1.0)
    assert len(sim.pendentes("A")) == 1
    assert len(sim.pendentes()) == 2


# ============================================================= funding
def test_funding_cobrado_por_periodo_de_oito_horas(sim):
    oito_h = 8 * 3600 * 1000
    assert sim.custo_funding(1000.0, Side.LONG, 0, oito_h - 1, 0.0001) == 0.0
    um_periodo = sim.custo_funding(1000.0, Side.LONG, 0, oito_h, 0.0001)
    assert um_periodo == pytest.approx(0.1)
    assert sim.custo_funding(1000.0, Side.LONG, 0, oito_h * 3,
                             0.0001) == pytest.approx(0.3)


def test_short_recebe_funding_positivo(sim):
    oito_h = 8 * 3600 * 1000
    assert sim.custo_funding(1000.0, Side.SHORT, 0, oito_h, 0.0001) < 0


# ======================================================= interpretação
def test_volume_em_usd_vs_em_unidades(sim):
    """Adivinhar a unidade pela magnitude erraria em silêncio nos ativos de
    preço muito alto ou muito baixo."""
    o1 = sim.enviar("B", Side.LONG, TipoOrdem.LIMIT, 1.0, preco_limite=100.0,
                    agora_ms=0)
    sim.processar_candle(c(101, 102, 100.0, 101.5, v=2.0, ts=60_000),
                         volume_em_usd=True)
    executado_usd = o1.size_executada

    s2 = SimuladorExecucao(ConfigSimulador(latencia_ms_min=1,
                                           latencia_ms_max=1))
    o2 = s2.enviar("B", Side.LONG, TipoOrdem.LIMIT, 1.0, preco_limite=100.0,
                   agora_ms=0)
    s2.processar_candle(c(101, 102, 100.0, 101.5, v=2.0, ts=60_000),
                        volume_em_usd=False)
    assert o2.size_executada > executado_usd


def test_resumo_agrega_status_e_taxas(sim):
    sim.enviar("B", Side.LONG, TipoOrdem.MARKET, 1.0, agora_ms=0)
    sim.processar_candle(c(100, 101, 99, 100, ts=60_000))
    r = sim.resumo()
    assert r["total_ordens"] == 1
    assert r["taxas_totais_usd"] > 0
    assert r["modelo_de_livro"]["tipo"] == "livro_sintetico"
    assert "não é profundidade real" in r["modelo_de_livro"]["aviso"]
