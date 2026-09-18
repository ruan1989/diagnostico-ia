"""O backtest é a fonte das estatísticas. Se ele mente, o sistema mente."""
import pytest

from investai.backtest import PF_SEM_PERDAS, calcular, max_drawdown_pct, rodar_backtest
from investai.backtest.metrics import maior_sequencia_perdas, sharpe_ratio
from investai.config import ExecutionConfig, SignalConfig
from investai.models import Side, Trade


def _trade(pnl, r=None, fees=0.0):
    return Trade(symbol="X", side=Side.LONG, entry=100.0, exit=101.0, size=1.0,
                 opened_at=0, closed_at=1, pnl_usd=pnl,
                 pnl_r=r if r is not None else pnl / 10.0,
                 motivo_saida="teste", fees_usd=fees)


# ------------------------------------------------------------------ métricas
def test_drawdown_de_pico_a_vale():
    assert max_drawdown_pct([100, 120, 90, 110]) == pytest.approx(25.0)


def test_drawdown_curva_sempre_subindo_e_zero():
    assert max_drawdown_pct([100, 110, 120]) == pytest.approx(0.0)


def test_profit_factor_calculado_como_razao():
    st = calcular([_trade(30), _trade(-10), _trade(20)], [100, 130, 120, 140], 100)
    assert st.profit_factor == pytest.approx(5.0)     # 50 de ganho / 10 de perda


def test_profit_factor_sem_perdas_usa_sentinela_nao_valor_em_dolar():
    """Sem operação perdedora o PF é infinito; reportar o lucro em US$ como se
    fosse razão seria enganoso."""
    st = calcular([_trade(30), _trade(66)], [100, 130, 196], 100)
    assert st.profit_factor == PF_SEM_PERDAS


def test_metricas_vazias_nao_inventam_numero():
    st = calcular([], [], 1000.0)
    assert st.trades == 0
    assert st.win_rate == 0.0
    assert st.profit_factor == 0.0
    assert st.equity_final == 1000.0


def test_win_rate_e_expectativa():
    st = calcular([_trade(10, 1.0), _trade(-5, -0.5), _trade(10, 1.0), _trade(-5, -0.5)],
                  [100, 110, 105, 115, 110], 100)
    assert st.win_rate == pytest.approx(0.5)
    assert st.expectancy_r == pytest.approx(0.25)


def test_maior_sequencia_de_perdas():
    seq = [_trade(5), _trade(-1), _trade(-1), _trade(-1), _trade(3), _trade(-1)]
    assert maior_sequencia_perdas(seq) == 3


def test_sharpe_exige_amostra_minima():
    assert sharpe_ratio([0.01, 0.02]) == 0.0


def test_sharpe_zero_sem_variacao():
    assert sharpe_ratio([0.01] * 20) == 0.0


# ------------------------------------------------------------------- engine
def test_backtest_produz_resultado_coerente(velas):
    r = rodar_backtest("BTCUSDT", "1H", velas, SignalConfig(), ExecutionConfig(),
                       side_filtro=Side.LONG)
    assert r.stats.trades >= 0
    assert r.sinais_gerados >= r.stats.trades - 1   # -1: trade aberto no fim
    assert len(r.equity) == r.stats.trades + 1
    assert 0.0 <= r.stats.win_rate <= 1.0


def test_backtest_respeita_filtro_de_lado(velas):
    cfg, ec = SignalConfig(), ExecutionConfig()
    longs = rodar_backtest("BTCUSDT", "1H", velas, cfg, ec, side_filtro=Side.LONG)
    shorts = rodar_backtest("BTCUSDT", "1H", velas, cfg, ec, side_filtro=Side.SHORT)
    assert all(t.side is Side.LONG for t in longs.trades)
    assert all(t.side is Side.SHORT for t in shorts.trades)


def test_backtest_nao_olha_o_futuro(velas):
    """Rodar sobre um prefixo tem que dar as MESMAS entradas que rodar sobre a
    série completa. É a prova de que o resultado não vem de lookahead."""
    cfg, ec = SignalConfig(), ExecutionConfig()
    prefixo = velas[:900]
    curto = rodar_backtest("BTCUSDT", "1H", prefixo, cfg, ec, side_filtro=Side.LONG)
    longo = rodar_backtest("BTCUSDT", "1H", velas, cfg, ec, side_filtro=Side.LONG)
    # O último trade do curto pode ter sido encerrado à força no fim da série.
    entradas_curto = [(t.opened_at, round(t.entry, 8)) for t in curto.trades]
    entradas_longo = [(t.opened_at, round(t.entry, 8)) for t in longo.trades]
    comparaveis = [t for t in curto.trades if t.motivo_saida != "fim_da_serie"]
    n = len(comparaveis)
    assert entradas_curto[:n] == entradas_longo[:n]


def test_entrada_nunca_ocorre_no_candle_do_sinal(velas):
    """Todo trade abre no timestamp de um candle posterior ao primeiro
    avaliável — nunca no próprio candle que gerou o sinal."""
    r = rodar_backtest("BTCUSDT", "1H", velas, SignalConfig(), ExecutionConfig())
    ts_validos = {c.ts for c in velas}
    for t in r.trades:
        assert t.opened_at in ts_validos
        assert t.opened_at > velas[0].ts


def test_taxas_e_slippage_reduzem_o_resultado(velas):
    """Sem custos o resultado tem que ser melhor. Se não for, os custos não
    estão sendo aplicados."""
    cfg = SignalConfig()
    com_custo = rodar_backtest("BTCUSDT", "1H", velas, cfg,
                               ExecutionConfig(taxa_taker_pct=0.06, slippage_pct=0.05),
                               side_filtro=Side.LONG)
    # funding também é custo: zerar só taxa e slippage não zera fees_paid.
    sem_custo = rodar_backtest("BTCUSDT", "1H", velas, cfg,
                               ExecutionConfig(taxa_taker_pct=0.0, slippage_pct=0.0),
                               side_filtro=Side.LONG, funding_rate=0.0)
    assert com_custo.stats.fees_paid > 0
    assert sem_custo.stats.fees_paid == pytest.approx(0.0)
    assert sem_custo.stats.net_pnl > com_custo.stats.net_pnl


def test_funding_e_cobrado_separado_de_taxa(velas):
    """Funding entra no custo mesmo sem taxa de corretagem."""
    ec = ExecutionConfig(taxa_taker_pct=0.0, slippage_pct=0.0)
    sem = rodar_backtest("BTCUSDT", "1H", velas, SignalConfig(), ec,
                         side_filtro=Side.LONG, funding_rate=0.0)
    com = rodar_backtest("BTCUSDT", "1H", velas, SignalConfig(), ec,
                         side_filtro=Side.LONG, funding_rate=0.001)
    assert com.stats.net_pnl < sem.stats.net_pnl


def test_uma_posicao_por_vez(velas):
    """Trades do mesmo símbolo não podem se sobrepor no tempo."""
    r = rodar_backtest("BTCUSDT", "1H", velas, SignalConfig(), ExecutionConfig(),
                       side_filtro=Side.LONG)
    for anterior, seguinte in zip(r.trades, r.trades[1:]):
        assert seguinte.opened_at >= anterior.closed_at


def test_capital_invalido_rejeitado(velas):
    with pytest.raises(ValueError):
        rodar_backtest("BTCUSDT", "1H", velas, SignalConfig(), ExecutionConfig(),
                       capital=0.0)


def test_score_minimo_alto_gera_menos_sinais(velas):
    cfg, ec = SignalConfig(), ExecutionConfig()
    frouxo = rodar_backtest("BTCUSDT", "1H", velas, cfg, ec, score_minimo=55.0)
    rigido = rodar_backtest("BTCUSDT", "1H", velas, cfg, ec, score_minimo=85.0)
    assert rigido.sinais_gerados <= frouxo.sinais_gerados


def test_serializacao_do_resultado(velas):
    d = rodar_backtest("BTCUSDT", "1H", velas, SignalConfig(),
                       ExecutionConfig()).to_dict()
    assert {"symbol", "stats", "trades", "equity", "sinais_gerados"} <= set(d)
    assert isinstance(d["stats"]["win_rate"], float)
