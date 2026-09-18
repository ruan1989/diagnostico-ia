"""Executor e motor autônomo, incluindo as travas do modo real."""
import pytest

from investai.config import ExecutionConfig
from investai.models import Position, Regime, Side, Signal, SignalGrade
from investai.risk.manager import DecisaoRisco
from investai.trading import (
    CONFIRMACAO_LIVE, Executor, arredondar_preco, arredondar_size,
    gerar_client_oid,
)


def sinal(entry=64000.0, stop=62720.0, side=Side.LONG):
    risco = abs(entry - stop)
    alvos = ([entry + risco * r for r in (1.8, 3.0, 4.5)] if side is Side.LONG
             else [entry - risco * r for r in (1.8, 3.0, 4.5)])
    return Signal(symbol="BTCUSDT", timeframe="1H", side=side,
                  grade=SignalGrade.A, score=80.0, entry=entry, stop_loss=stop,
                  take_profits=alvos, risk_reward=1.8, atr=entry * 0.01,
                  regime=Regime.TENDENCIA_ALTA)


def decisao(size=0.0039):
    return DecisaoRisco(True, "ok", size=size, notional_usd=size * 64000,
                        risco_usd=5.0, alavancagem=1.0)


@pytest.fixture
def ex():
    return Executor(ExecutionConfig(), modo="paper")


# -------------------------------------------------------------- idempotência
def test_client_oid_igual_na_mesma_janela():
    """Reenvio após falha de rede não pode criar posição dobrada."""
    a = gerar_client_oid("BTCUSDT", Side.LONG, 64000.0, 1_700_000_000_000)
    b = gerar_client_oid("BTCUSDT", Side.LONG, 64000.0, 1_700_000_000_900)
    assert a == b


def test_client_oid_muda_em_janela_diferente():
    a = gerar_client_oid("BTCUSDT", Side.LONG, 64000.0, 1_700_000_000_000)
    b = gerar_client_oid("BTCUSDT", Side.LONG, 64000.0, 1_700_000_300_000)
    assert a != b


def test_client_oid_distingue_lado_e_par():
    base = gerar_client_oid("BTCUSDT", Side.LONG, 64000.0, 1_700_000_000_000)
    assert base != gerar_client_oid("BTCUSDT", Side.SHORT, 64000.0, 1_700_000_000_000)
    assert base != gerar_client_oid("ETHUSDT", Side.LONG, 64000.0, 1_700_000_000_000)


# ------------------------------------------------------------ arredondamento
def test_size_arredonda_para_baixo():
    """Arredondar para cima aumentaria o risco acima do aprovado."""
    assert arredondar_size(0.0039876, 3, 0.001) == pytest.approx(0.003)


def test_size_abaixo_do_minimo_vira_zero():
    assert arredondar_size(0.0004, 3, 0.001) == 0.0


def test_preco_arredonda_para_o_passo():
    assert arredondar_preco(64123.456789, 2) == pytest.approx(64123.46)


# --------------------------------------------------------------- abrir/fechar
def test_abrir_em_papel_registra_posicao(ex):
    res = ex.abrir(sinal(), decisao())
    assert res.ok
    assert len(ex.posicoes()) == 1
    assert ex.posicoes()[0].modo == "paper"


def test_risco_reprovado_nao_abre(ex):
    res = ex.abrir(sinal(), DecisaoRisco(False, "drawdown estourado"))
    assert not res.ok
    assert "drawdown" in res.mensagem
    assert not ex.posicoes()


def test_live_sem_backend_e_recusado():
    with pytest.raises(ValueError, match="backend"):
        Executor(ExecutionConfig(), modo="live")


def test_modo_invalido_recusado():
    with pytest.raises(ValueError):
        Executor(ExecutionConfig(), modo="turbo")


# ----------------------------------------------------------------- gestão
def test_stop_fecha_a_posicao_com_prejuizo(ex):
    ex.abrir(sinal(), decisao())
    eventos = ex.gerenciar({"BTCUSDT": 62700.0})
    assert len(eventos) == 1
    _, trade = eventos[0]
    assert trade.motivo_saida == "stop_loss"
    assert trade.pnl_usd < 0
    assert not ex.posicoes()


def test_alvo1_faz_parcial_e_move_stop_para_breakeven(ex):
    """Depois do primeiro alvo a operação não pode mais virar prejuízo."""
    ex.abrir(sinal(), decisao())
    s = sinal()
    eventos = ex.gerenciar({"BTCUSDT": s.take_profits[0]})
    _, trade = eventos[0]
    assert trade.motivo_saida == "alvo_1"
    assert trade.pnl_usd > 0
    pos = ex.posicoes()[0]
    assert pos.trailing_ativo
    assert pos.stop_loss == pytest.approx(s.entry)
    assert pos.size < decisao().size          # parte foi realizada


def test_apos_breakeven_retorno_a_entrada_nao_da_prejuizo_grande(ex):
    ex.abrir(sinal(), decisao())
    s = sinal()
    ex.gerenciar({"BTCUSDT": s.take_profits[0]})
    eventos = ex.gerenciar({"BTCUSDT": s.entry})
    _, trade = eventos[0]
    assert trade.motivo_saida == "breakeven"
    # Só o custo de taxa/slippage, não 1R de perda.
    assert abs(trade.pnl_usd) < 5.0


def test_ultimo_alvo_fecha_tudo(ex):
    ex.abrir(sinal(), decisao())
    s = sinal()
    for alvo in s.take_profits:
        ex.gerenciar({"BTCUSDT": alvo})
    assert not ex.posicoes()


def test_short_inverte_a_direcao_do_lucro(ex):
    s = sinal(side=Side.SHORT)
    ex.abrir(s, decisao())
    eventos = ex.gerenciar({"BTCUSDT": s.take_profits[0]})
    _, trade = eventos[0]
    assert trade.side is Side.SHORT
    assert trade.pnl_usd > 0


def test_preco_indisponivel_nao_mexe_na_posicao(ex):
    ex.abrir(sinal(), decisao())
    assert ex.gerenciar({}) == []
    assert ex.gerenciar({"BTCUSDT": 0.0}) == []
    assert len(ex.posicoes()) == 1


def test_fechar_par_inexistente(ex):
    res, trade = ex.fechar("DOGEUSDT", 0.1, "manual")
    assert not res.ok and trade is None


def test_fechamento_parcial_reduz_tamanho(ex):
    ex.abrir(sinal(), decisao())
    res, trade = ex.fechar("BTCUSDT", 65000.0, "manual", fracao=0.5)
    assert res.ok
    assert ex.posicoes()[0].size == pytest.approx(decisao().size * 0.5)


def test_slippage_sempre_contra_o_operador(ex):
    cfg = ExecutionConfig(slippage_pct=1.0, taxa_taker_pct=0.0)
    e = Executor(cfg, modo="paper")
    e.abrir(sinal(), decisao())
    _, trade = e.fechar("BTCUSDT", 64000.0, "manual")
    assert trade.exit < 64000.0      # long sai mais barato que o preço de tela


def test_carregar_posicoes_do_banco(ex):
    ex.carregar_posicoes_papel([
        Position(symbol="ETHUSDT", side=Side.LONG, size=1.0, entry=3000.0,
                 stop_loss=2900.0, take_profits=[3200.0], opened_at=0)])
    assert [p.symbol for p in ex.posicoes()] == ["ETHUSDT"]


# ------------------------------------------------------------------- motor
def test_motor_comeca_em_simulacao(motor):
    assert motor.estado.modo == "paper"
    assert not motor.estado.armado_live


def test_armar_live_exige_frase_exata(motor):
    ok, msg = motor.armar_live("sim, quero")
    assert not ok and "confirmação incorreta" in msg
    assert not motor.estado.armado_live


def test_armar_live_exige_chave_conectada(motor):
    ok, msg = motor.armar_live(CONFIRMACAO_LIVE)
    assert not ok and "chave de API" in msg
    assert motor.executor.modo == "paper"


def test_ciclo_nao_explode_sem_oportunidade(motor):
    r = motor.ciclo()
    assert "gestao" in r and "entradas" in r
    assert motor.estado.ciclos == 1


def test_segundo_ciclo_nao_repete_varredura(motor):
    motor.ciclo()
    assert motor.ciclo()["scan"] is False


def test_status_expoe_limites_e_desempenho(motor):
    s = motor.status()
    assert {"motor", "risco", "limites", "posicoes", "desempenho_realizado"} <= set(s)
    assert s["confirmacao_necessaria_live"] == CONFIRMACAO_LIVE


def test_fechar_tudo_encerra_posicoes(motor):
    motor.executor.abrir(sinal(), decisao())
    assert len(motor.executor.posicoes()) == 1
    motor.fechar_tudo("teste")
    assert not motor.executor.posicoes()


def test_desarmar_volta_para_simulacao(motor):
    motor.executor.modo = "live"
    motor.estado.armado_live = True
    motor.desarmar_live()
    assert motor.executor.modo == "paper"
    assert not motor.estado.armado_live
