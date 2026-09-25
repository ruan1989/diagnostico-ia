"""Persistência: o registro de auditoria é o que permite depurar prejuízo."""
import pytest

from investai.models import (
    BacktestStats, Position, Regime, Side, Signal, SignalGrade, Trade,
)
from investai.store import Store


def sinal(symbol="BTCUSDT", grade=SignalGrade.A, ts=1_700_000_000_000):
    return Signal(symbol=symbol, timeframe="1H", side=Side.LONG, grade=grade,
                  score=80.0, entry=64000.0, stop_loss=62720.0,
                  take_profits=[66304.0, 67840.0], risk_reward=1.8,
                  atr=640.0, regime=Regime.TENDENCIA_ALTA, gerado_em=ts,
                  prob_acerto_estimada=0.55, retorno_esperado_r=0.45,
                  hist=BacktestStats(trades=40, win_rate=0.55,
                                     profit_factor=1.9, expectancy_r=0.4))


def trade(pnl=10.0, r=1.0, symbol="BTCUSDT", closed=1, fees=0.5):
    return Trade(symbol=symbol, side=Side.LONG, entry=64000.0,
                 exit=64000.0 + pnl, size=0.01, opened_at=0, closed_at=closed,
                 pnl_usd=pnl, pnl_r=r,
                 motivo_saida="alvo_1" if pnl > 0 else "stop_loss",
                 fees_usd=fees, bars_held=5)


# ------------------------------------------------------------------ sinais
def test_salvar_e_recuperar_sinal(store):
    assert store.salvar_sinal(sinal()) > 0
    recuperados = store.sinais_recentes()
    assert len(recuperados) == 1
    assert recuperados[0]["symbol"] == "BTCUSDT"
    assert recuperados[0]["historico"]["trades"] == 40


def test_filtro_de_sinais_operaveis(store):
    store.salvar_sinal(sinal(grade=SignalGrade.A))
    store.salvar_sinal(sinal(symbol="ETHUSDT", grade=SignalGrade.B))
    store.salvar_sinal(sinal(symbol="SOLUSDT", grade=SignalGrade.C))
    store.salvar_sinal(sinal(symbol="XRPUSDT", grade=SignalGrade.REJEITADO))
    assert len(store.sinais_recentes()) == 4
    assert len(store.sinais_recentes(apenas_operaveis=True)) == 2


def test_filtro_por_par(store):
    store.salvar_sinal(sinal("BTCUSDT"))
    store.salvar_sinal(sinal("ETHUSDT"))
    assert len(store.sinais_recentes(symbol="ETHUSDT")) == 1


def test_sinais_vem_do_mais_recente(store):
    store.salvar_sinal(sinal("BTCUSDT", ts=1_000))
    store.salvar_sinal(sinal("ETHUSDT", ts=9_000))
    assert store.sinais_recentes()[0]["symbol"] == "ETHUSDT"


# ------------------------------------------------------------------ trades
def test_resumo_calcula_metricas_realizadas(store):
    for t in (trade(30, 3.0), trade(-10, -1.0), trade(20, 2.0), trade(-10, -1.0)):
        store.salvar_trade(t)
    r = store.resumo_trades()
    assert r["trades"] == 4
    assert r["win_rate"] == pytest.approx(0.5)
    assert r["pnl_usd"] == pytest.approx(30.0)
    assert r["profit_factor"] == pytest.approx(2.5)     # 50 / 20
    assert r["expectancy_r"] == pytest.approx(0.75)


def test_resumo_vazio_nao_inventa_numero(store):
    r = store.resumo_trades()
    assert r["trades"] == 0
    assert r["win_rate"] == 0.0
    assert r["profit_factor"] == 0.0


def test_resumo_separa_papel_de_real(store):
    store.salvar_trade(trade(10.0), modo="paper")
    store.salvar_trade(trade(-5.0), modo="live")
    assert store.resumo_trades("paper")["trades"] == 1
    assert store.resumo_trades("live")["pnl_usd"] == pytest.approx(-5.0)
    assert store.resumo_trades()["trades"] == 2


def test_curva_de_capital_acumula_na_ordem(store):
    store.salvar_trade(trade(50.0, closed=1))
    store.salvar_trade(trade(-20.0, closed=2))
    store.salvar_trade(trade(30.0, closed=3))
    curva = store.curva_capital(1000.0)
    assert [p["equity"] for p in curva] == [1000.0, 1050.0, 1030.0, 1060.0]


def test_curva_sem_trades(store):
    assert store.curva_capital(500.0) == [{"ts": 0, "equity": 500.0}]


# --------------------------------------------------------------- posições
def test_posicao_sobrevive_ao_reinicio(store, tmp_path):
    """Se a posição não persistir, reiniciar o robô perde o stop."""
    store.salvar_posicao(Position(
        symbol="ETHUSDT", side=Side.SHORT, size=0.5, entry=3100.0,
        stop_loss=3200.0, take_profits=[3000.0, 2900.0], opened_at=123,
        notional_usd=1550.0, risk_usd=50.0, tps_atingidos=1,
        trailing_ativo=True, client_oid="abc"))
    store.close()

    outro = Store(tmp_path / "trading.db")
    p = outro.posicoes()[0]
    assert p.symbol == "ETHUSDT"
    assert p.side is Side.SHORT
    assert p.stop_loss == pytest.approx(3200.0)
    assert p.tps_atingidos == 1
    assert p.trailing_ativo is True
    outro.close()


def test_salvar_posicao_e_idempotente(store):
    for stop in (2900.0, 3000.0):
        store.salvar_posicao(Position(
            symbol="ETHUSDT", side=Side.LONG, size=1.0, entry=3000.0,
            stop_loss=stop, take_profits=[], opened_at=0))
    posicoes = store.posicoes()
    assert len(posicoes) == 1
    assert posicoes[0].stop_loss == pytest.approx(3000.0)


def test_remover_posicao(store):
    store.salvar_posicao(Position(
        symbol="ETHUSDT", side=Side.LONG, size=1.0, entry=3000.0,
        stop_loss=2900.0, take_profits=[], opened_at=0))
    store.remover_posicao("ETHUSDT")
    assert store.posicoes() == []


# ---------------------------------------------------------------- eventos
def test_evento_guarda_dados_estruturados(store):
    store.registrar_evento("ALERTA", "risk", "kill switch",
                           {"drawdown_pct": 11.2, "motivo": "limite"})
    e = store.eventos()[0]
    assert e["nivel"] == "ALERTA"
    assert e["dados"]["drawdown_pct"] == 11.2


def test_filtro_de_eventos_por_nivel(store):
    store.registrar_evento("INFO", "a", "m1")
    store.registrar_evento("ERRO", "b", "m2")
    assert len(store.eventos(nivel="ERRO")) == 1


def test_evento_sem_dados(store):
    store.registrar_evento("INFO", "x", "sem payload")
    assert store.eventos()[0]["dados"] is None


# ------------------------------------------------------------------ estado
def test_estado_faz_roundtrip_de_tipos(store):
    store.set_estado("capital", 1234.56)
    store.set_estado("pares", ["BTCUSDT", "ETHUSDT"])
    store.set_estado("flags", {"live": False})
    assert store.get_estado("capital") == pytest.approx(1234.56)
    assert store.get_estado("pares") == ["BTCUSDT", "ETHUSDT"]
    assert store.get_estado("flags") == {"live": False}


def test_estado_sobrescreve(store):
    store.set_estado("modo", "paper")
    store.set_estado("modo", "live")
    assert store.get_estado("modo") == "live"


def test_estado_ausente_retorna_default(store):
    assert store.get_estado("nao_existe", "padrao") == "padrao"


def test_banco_e_criado_com_diretorio(tmp_path):
    caminho = tmp_path / "sub" / "dir" / "x.db"
    s = Store(caminho)
    assert caminho.exists()
    s.close()
