"""Gestão de risco: o módulo que decide se a conta sobrevive."""
import pytest

from investai.config import RiskConfig
from investai.models import (
    Position, Regime, Side, Signal, SignalGrade, Trade,
)
from investai.risk import MS_POR_DIA, RiskManager, grupo_de, indice_dia


def sinal(symbol="BTCUSDT", entry=64000.0, stop=62720.0, rr=2.0,
          side=Side.LONG) -> Signal:
    return Signal(symbol=symbol, timeframe="1H", side=side,
                  grade=SignalGrade.A, score=80.0, entry=entry, stop_loss=stop,
                  take_profits=[entry + (entry - stop) * rr], risk_reward=rr,
                  atr=entry * 0.01, regime=Regime.TENDENCIA_ALTA)


def posicao(symbol, notional=100.0, side=Side.LONG) -> Position:
    return Position(symbol=symbol, side=side, size=1.0, entry=100.0,
                    stop_loss=98.0, take_profits=[], opened_at=0,
                    notional_usd=notional)


def perda(usd=-10.0) -> Trade:
    return Trade(symbol="BTCUSDT", side=Side.LONG, entry=100.0, exit=98.0,
                 size=1.0, opened_at=0, closed_at=1, pnl_usd=usd,
                 pnl_r=-1.0, motivo_saida="stop_loss")


def ganho(usd=20.0) -> Trade:
    return Trade(symbol="BTCUSDT", side=Side.LONG, entry=100.0, exit=102.0,
                 size=1.0, opened_at=0, closed_at=1, pnl_usd=usd,
                 pnl_r=2.0, motivo_saida="alvo_1")


# ----------------------------------------------------------- dimensionamento
def test_risco_por_operacao_e_exatamente_o_configurado():
    rm = RiskManager(RiskConfig(risco_por_trade_pct=0.5), 1000.0)
    d = rm.avaliar_entrada(sinal(), [])
    assert d.aprovado
    assert d.risco_usd == pytest.approx(5.0)      # 0,5% de 1000


def test_stop_mais_largo_reduz_o_tamanho():
    """O risco em dólar é constante; quem varia é a quantidade."""
    rm = RiskManager(RiskConfig(), 1000.0)
    estreito = rm.avaliar_entrada(sinal(stop=63360.0), [])   # 1% de distância
    largo = rm.avaliar_entrada(sinal(stop=60800.0), [])      # 5% de distância
    assert largo.size < estreito.size
    assert estreito.risco_usd == pytest.approx(largo.risco_usd, rel=1e-6)


def test_alavancagem_e_limitada():
    rm = RiskManager(RiskConfig(alavancagem_max=3.0, risco_por_trade_pct=5.0), 1000.0)
    d = rm.avaliar_entrada(sinal(stop=63936.0), [])   # stop muito apertado
    assert d.alavancagem <= 3.0 + 1e-9
    assert d.notional_usd <= 3000.0 + 1e-6


def test_stop_zero_rejeitado():
    rm = RiskManager(RiskConfig(), 1000.0)
    d = rm.avaliar_entrada(sinal(entry=100.0, stop=100.0), [])
    assert not d.aprovado


def test_capital_minusculo_rejeita_por_notional():
    rm = RiskManager(RiskConfig(), 10.0)
    assert not rm.avaliar_entrada(sinal(), []).aprovado


def test_capital_inicial_invalido():
    with pytest.raises(ValueError):
        RiskManager(RiskConfig(), 0.0)


# ------------------------------------------------------------------ bloqueios
def test_limite_de_posicoes_simultaneas():
    rm = RiskManager(RiskConfig(max_posicoes_simultaneas=2), 1000.0)
    d = rm.avaliar_entrada(sinal(), [posicao("ETHUSDT"), posicao("SOLUSDT")])
    assert not d.aprovado
    assert "posições abertas" in d.motivo


def test_posicao_duplicada_no_mesmo_par():
    rm = RiskManager(RiskConfig(), 1000.0)
    d = rm.avaliar_entrada(sinal("BTCUSDT"), [posicao("BTCUSDT")])
    assert not d.aprovado
    assert "já existe posição" in d.motivo


def test_risco_retorno_abaixo_do_minimo():
    rm = RiskManager(RiskConfig(min_risk_reward=2.0), 1000.0)
    assert not rm.avaliar_entrada(sinal(rr=1.2), []).aprovado


def test_concentracao_em_grupo_correlacionado():
    """Três posições em L1 alternativas não são três apostas — são uma."""
    rm = RiskManager(RiskConfig(max_posicoes_simultaneas=5), 1000.0)
    d = rm.avaliar_entrada(sinal("AVAXUSDT"),
                           [posicao("SOLUSDT"), posicao("NEARUSDT")])
    assert not d.aprovado
    assert "grupo" in d.motivo


def test_grupos_de_correlacao_conhecidos():
    assert grupo_de("BTCUSDT") == grupo_de("ETHUSDT") == "majors"
    assert grupo_de("ARBUSDT") == grupo_de("OPUSDT") == "l2"
    assert grupo_de("PAR_INEXISTENTE") == "outros"


def test_limite_de_exposicao_total_reduz_tamanho():
    rm = RiskManager(RiskConfig(max_exposicao_notional_pct=150.0,
                                max_posicoes_simultaneas=5), 1000.0)
    d = rm.avaliar_entrada(sinal(), [posicao("ETHUSDT", notional=1400.0)])
    assert d.aprovado
    assert d.notional_usd <= 100.0 + 1e-6      # só sobraram US$ 100 de espaço


def test_exposicao_esgotada_bloqueia():
    rm = RiskManager(RiskConfig(max_exposicao_notional_pct=100.0,
                                max_posicoes_simultaneas=5), 1000.0)
    d = rm.avaliar_entrada(sinal(), [posicao("ETHUSDT", notional=1000.0)])
    assert not d.aprovado
    assert "exposição" in d.motivo


# ------------------------------------------------------- circuit breakers
def test_perda_diaria_bloqueia_novas_entradas():
    rm = RiskManager(RiskConfig(perda_diaria_max_pct=2.0,
                                cooldown_minutos_pos_stop=0,
                                perdas_consecutivas_max=99), 1000.0)
    rm.registrar_trade(perda(-25.0))
    assert not rm.avaliar_entrada(sinal(), []).aprovado


def test_perdas_consecutivas_pausam_o_robo():
    rm = RiskManager(RiskConfig(perdas_consecutivas_max=3,
                                cooldown_minutos_pos_stop=0), 1000.0)
    for _ in range(3):
        rm.registrar_trade(perda(-1.0))
    d = rm.avaliar_entrada(sinal(), [])
    assert not d.aprovado
    assert "consecutivas" in " ".join(d.bloqueios)


def test_ganho_zera_contador_de_perdas():
    rm = RiskManager(RiskConfig(cooldown_minutos_pos_stop=0), 1000.0)
    rm.registrar_trade(perda(-1.0))
    rm.registrar_trade(perda(-1.0))
    assert rm.estado.perdas_consecutivas == 2
    rm.registrar_trade(ganho(5.0))
    assert rm.estado.perdas_consecutivas == 0


def test_cooldown_apos_stop():
    rm = RiskManager(RiskConfig(cooldown_minutos_pos_stop=60,
                                perdas_consecutivas_max=99), 1000.0)
    rm.registrar_trade(perda(-1.0), agora_ms=1_000_000)
    bloqueado = rm.avaliar_entrada(sinal(), [], agora_ms=1_000_000 + 60_000)
    liberado = rm.avaliar_entrada(sinal(), [], agora_ms=1_000_000 + 3_700_000)
    assert not bloqueado.aprovado and "cooldown" in bloqueado.motivo
    assert liberado.aprovado


def test_drawdown_aciona_kill_switch():
    rm = RiskManager(RiskConfig(drawdown_max_pct=10.0,
                                perda_diaria_max_pct=100.0,
                                perdas_consecutivas_max=99,
                                cooldown_minutos_pos_stop=0), 1000.0)
    rm.registrar_trade(perda(-150.0))
    assert rm.estado.kill_switch
    assert not rm.avaliar_entrada(sinal(), []).aprovado


def test_kill_switch_nao_se_rearma_sozinho():
    """Um robô que se rearma automaticamente volta a perder pelo mesmo motivo."""
    rm = RiskManager(RiskConfig(drawdown_max_pct=5.0,
                                perda_diaria_max_pct=100.0,
                                perdas_consecutivas_max=99,
                                cooldown_minutos_pos_stop=0), 1000.0)
    rm.registrar_trade(perda(-100.0))
    assert rm.estado.kill_switch
    rm.registrar_trade(ganho(500.0))
    assert rm.estado.kill_switch          # continua travado após recuperar
    rm.liberar_kill_switch()
    assert not rm.estado.kill_switch


def test_pico_de_capital_acompanha_a_alta():
    rm = RiskManager(RiskConfig(), 1000.0)
    rm.registrar_trade(ganho(200.0))
    assert rm.estado.pico_capital == pytest.approx(1200.0)
    assert rm.estado.drawdown_pct == pytest.approx(0.0)


def test_janela_diaria_rola_na_troca_de_dia():
    """A perda do dia zera na fronteira do dia de calendário, não 24h depois
    do boot — assim reiniciar o robô não reabre o limite já estourado."""
    rm = RiskManager(RiskConfig(cooldown_minutos_pos_stop=0,
                                perdas_consecutivas_max=99), 1000.0)
    dia0 = 20_000 * MS_POR_DIA + 3_600_000
    rm.registrar_trade(perda(-15.0), agora_ms=dia0)
    assert rm.estado.pnl_dia == pytest.approx(-15.0)
    # Mesmo dia, mais tarde: soma no mesmo contador.
    rm.registrar_trade(perda(-5.0), agora_ms=dia0 + 7_200_000)
    assert rm.estado.pnl_dia == pytest.approx(-20.0)
    # Dia seguinte: contador reiniciado.
    rm.registrar_trade(ganho(1.0), agora_ms=dia0 + MS_POR_DIA)
    assert rm.estado.pnl_dia == pytest.approx(1.0)


def test_janela_diaria_sobrevive_a_reinicio_no_mesmo_dia():
    """Religar o processo não pode apagar a perda já registrada no dia."""
    dia = indice_dia(int(__import__("time").time() * 1000))
    rm = RiskManager(RiskConfig(cooldown_minutos_pos_stop=0,
                                perdas_consecutivas_max=99), 1000.0)
    assert rm.estado.dia_indice == dia
    # Um estado restaurado do banco com o mesmo índice de dia é preservado.
    rm.estado.pnl_dia = -18.0
    rm.registrar_trade(perda(-2.0))
    assert rm.estado.pnl_dia == pytest.approx(-20.0)


def test_semana_rola_independente_do_dia():
    rm = RiskManager(RiskConfig(cooldown_minutos_pos_stop=0,
                                perdas_consecutivas_max=99,
                                perda_diaria_max_pct=100.0), 1000.0)
    base = 20_000 * MS_POR_DIA
    rm.registrar_trade(perda(-10.0), agora_ms=base)
    assert rm.estado.pnl_semana == pytest.approx(-10.0)
    rm.registrar_trade(perda(-10.0), agora_ms=base + MS_POR_DIA * 7)
    assert rm.estado.pnl_semana == pytest.approx(-10.0)   # semana reiniciada


def test_sincronizar_capital_com_a_exchange():
    rm = RiskManager(RiskConfig(), 1000.0)
    rm.sincronizar_capital(2500.0)
    assert rm.estado.capital_atual == pytest.approx(2500.0)
    rm.sincronizar_capital(-5.0)                     # valor inválido é ignorado
    assert rm.estado.capital_atual == pytest.approx(2500.0)
