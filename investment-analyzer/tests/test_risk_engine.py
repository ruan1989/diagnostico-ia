"""Risk Engine: veto absoluto, liquidação e proibições estruturais."""
import pytest

from investai.config import RiskConfig
from investai.models import (
    Position, Regime, Side, Signal, SignalGrade, Trade,
)
from investai.risk import (
    ContextoMercado, LimitesExtra, RiskEngine, Severidade, Veredicto,
)
from investai.risk import liquidation as liq


def sinal(symbol="BTCUSDT", entry=64000.0, stop=62720.0, rr=2.0,
          side=Side.LONG):
    alvo = (entry + (entry - stop) * rr if side is Side.LONG
            else entry - (stop - entry) * rr)
    return Signal(symbol=symbol, timeframe="1H", side=side,
                  grade=SignalGrade.A, score=80.0, entry=entry,
                  stop_loss=stop, take_profits=[alvo], risk_reward=rr,
                  atr=entry * 0.015, regime=Regime.TENDENCIA_ALTA)


def pos(symbol, notional=100.0, side=Side.LONG, entry=100.0, stop=98.0):
    return Position(symbol=symbol, side=side, size=1.0, entry=entry,
                    stop_loss=stop, take_profits=[], opened_at=0,
                    notional_usd=notional)


def stop_trade(symbol="BTCUSDT", side=Side.LONG, closed=1_000_000):
    return Trade(symbol=symbol, side=side, entry=64000.0, exit=62720.0,
                 size=0.01, opened_at=0, closed_at=closed, pnl_usd=-5.0,
                 pnl_r=-1.0, motivo_saida="stop_loss")


@pytest.fixture
def ctx():
    return ContextoMercado(atr_pct=1.5, spread_pct=0.03,
                           minutos_ate_evento_critico=999,
                           volume_24h_usd=1e9)


@pytest.fixture
def engine():
    return RiskEngine(RiskConfig(), 1000.0)


# ======================================================== liquidação
def test_preco_de_liquidacao_long_e_short():
    assert liq.preco_liquidacao(100.0, 10, Side.LONG, 0.005) == pytest.approx(90.5)
    assert liq.preco_liquidacao(100.0, 10, Side.SHORT, 0.005) == pytest.approx(109.5)


def test_alavancagem_maior_aproxima_a_liquidacao():
    distancias = [
        abs(100 - liq.preco_liquidacao(100.0, lev, Side.LONG))
        for lev in (2, 5, 10, 25)
    ]
    assert distancias == sorted(distancias, reverse=True)


def test_stop_depois_da_liquidacao_e_rejeitado():
    """O erro terminal: com alavancagem alta o stop nunca é executado."""
    a = liq.analisar(64000.0, 64000 * 0.98, Side.LONG, 50, notional_usd=1000)
    assert not a.aprovado
    assert "ANTES do stop" in a.motivo


def test_folga_insuficiente_e_rejeitada():
    """Stop a 7% com liquidação a 9,5%: sobra 26% de folga, abaixo dos 35%."""
    a = liq.analisar(100.0, 93.0, Side.LONG, 10, notional_usd=1000)
    assert not a.aprovado
    assert "folga" in a.motivo
    assert a.folga < liq.FOLGA_MINIMA


def test_folga_na_fronteira_e_aprovada():
    a = liq.analisar(100.0, 94.0, Side.LONG, 10, notional_usd=1000)
    assert a.aprovado
    assert a.folga >= liq.FOLGA_MINIMA


def test_liquidacao_proxima_em_atrs_e_rejeitada():
    """A folga relativa sozinha aprova alavancagem absurda; o critério em
    ATRs é o que impede."""
    a = liq.analisar(64000.0, 64000 * 0.995, Side.LONG, 25,
                     notional_usd=1000, atr_pct=1.5)
    assert not a.aprovado
    assert "ATRs" in a.motivo


def test_mesma_alavancagem_aceita_em_ativo_menos_volatil():
    calmo = liq.analisar(64000.0, 64000 * 0.995, Side.LONG, 15,
                         notional_usd=1000, atr_pct=0.5)
    volatil = liq.analisar(64000.0, 64000 * 0.995, Side.LONG, 15,
                           notional_usd=1000, atr_pct=4.0)
    assert calmo.aprovado
    assert not volatil.aprovado


def test_sem_atr_usa_piso_absoluto():
    assert liq.analisar(100.0, 99.0, Side.LONG, 10, notional_usd=1000).aprovado
    assert not liq.analisar(100.0, 99.5, Side.LONG, 25,
                            notional_usd=1000).aprovado


def test_stop_do_lado_errado_recusado():
    a = liq.analisar(100.0, 105.0, Side.LONG, 5)
    assert not a.aprovado
    assert "lado errado" in a.motivo


def test_entrada_invalida_recusada():
    with pytest.raises(ValueError):
        liq.analisar(0.0, 1.0, Side.LONG, 5)
    with pytest.raises(ValueError):
        liq.preco_liquidacao(100.0, 0.5, Side.LONG)


def test_alavancagem_sugerida_cai_com_stop_mais_largo():
    sugestoes = [liq.sugerir_alavancagem(p) for p in (0.5, 1.0, 2.0, 8.0)]
    assert sugestoes == sorted(sugestoes, reverse=True)


def test_alavancagem_sugerida_cai_com_volatilidade():
    sugestoes = [liq.sugerir_alavancagem_por_atr(a)
                 for a in (0.8, 1.5, 3.0, 8.0)]
    assert sugestoes == sorted(sugestoes, reverse=True)


def test_stop_maximo_seguro_respeita_a_folga():
    s = liq.stop_maximo_seguro(100.0, Side.LONG, 10)
    a = liq.analisar(100.0, s, Side.LONG, 10, notional_usd=1000)
    assert a.folga == pytest.approx(liq.FOLGA_MINIMA, abs=1e-6)


# ==================================================== engine: aprovação
def test_caso_normal_aprovado_com_risco_exato(engine, ctx):
    d = engine.avaliar(sinal(), [], contexto=ctx)
    assert d.veredicto is Veredicto.APROVADO
    assert d.risco_usd == pytest.approx(5.0)     # 0,5% de 1000
    assert d.analise_liquidacao is not None


def test_decisao_expoe_todos_os_achados(engine, ctx):
    d = engine.avaliar(sinal(), [], contexto=ctx)
    assert isinstance(d.to_dict()["achados"], list)
    assert d.to_dict()["veredicto"] == "aprovado"


# ====================================================== engine: vetos
def test_dados_inadequados_vetam(engine):
    d = engine.avaliar(sinal(), [], contexto=ContextoMercado(
        atr_pct=1.5, dados_confiaveis=False, motivo_dados="feed congelado"))
    assert d.veredicto is Veredicto.REJEITADO
    assert "NÃO É POSSÍVEL VALIDAR" in d.motivo_principal


def test_sistema_offline_veta(engine):
    d = engine.avaliar(sinal(), [], contexto=ContextoMercado(
        atr_pct=1.5, saude_sistema="OFFLINE"))
    assert d.veredicto is Veredicto.REJEITADO
    assert "OFFLINE" in d.motivo_principal


def test_evento_economico_proximo_veta(engine):
    d = engine.avaliar(sinal(), [], contexto=ContextoMercado(
        atr_pct=1.5, minutos_ate_evento_critico=20,
        descricao_evento="decisão do FOMC"))
    assert d.veredicto is Veredicto.REJEITADO
    assert "FOMC" in d.motivo_principal


def test_evento_distante_nao_veta(engine, ctx):
    assert engine.avaliar(sinal(), [], contexto=ctx).aprovado


def test_calendario_indisponivel_gera_aviso_nao_veto(engine):
    d = engine.avaliar(sinal(), [], contexto=ContextoMercado(
        atr_pct=1.5, spread_pct=0.03, minutos_ate_evento_critico=None))
    assert d.aprovado
    assert any(a.regra == "calendario_indisponivel"
               and a.severidade is Severidade.AVISO for a in d.achados)


def test_spread_alto_veta(engine):
    d = engine.avaliar(sinal(), [], contexto=ContextoMercado(
        atr_pct=1.5, spread_pct=0.5, minutos_ate_evento_critico=999))
    assert d.veredicto is Veredicto.REJEITADO
    assert "spread" in d.motivo_principal


def test_teto_de_exposicao_ja_limita_a_alavancagem_efetiva(engine, ctx):
    """Com stop de 0,1% o dimensionamento pediria alavancagem enorme, mas o
    teto de exposição corta o notional antes — e a liquidação fica longe.

    Registra a interação: duas travas independentes, e a primeira a morder
    torna a segunda desnecessária."""
    eng = RiskEngine(RiskConfig(risco_por_trade_pct=5.0,
                                alavancagem_max=30.0), 1000.0)
    d = eng.avaliar(sinal(entry=64000.0, stop=64000 * 0.999), [], contexto=ctx)
    assert d.aprovado
    assert d.notional_usd <= 1000.0 * 3.0 + 1e-6   # teto de 300%
    assert d.analise_liquidacao["liquidacao_em_atrs"] > 4.0


def test_liquidacao_insegura_veta_quando_a_exposicao_permite(ctx):
    """Sem o teto de exposição no caminho, o veto de liquidação é quem barra."""
    eng = RiskEngine(RiskConfig(risco_por_trade_pct=5.0, alavancagem_max=40.0,
                                max_exposicao_notional_pct=4000.0), 1000.0)
    d = eng.avaliar(sinal(entry=64000.0, stop=64000 * 0.999), [], contexto=ctx)
    assert d.veredicto is Veredicto.REJEITADO
    assert any(a.regra == "protecao_de_liquidacao" for a in d.achados)


# ============================================ proibições estruturais
def test_averaging_down_e_proibido(engine, ctx):
    d = engine.avaliar(sinal(), [pos("BTCUSDT")], contexto=ctx)
    assert d.veredicto is Veredicto.REJEITADO
    assert "averaging down" in d.motivo_principal


def test_lado_oposto_no_mesmo_ativo_e_proibido(engine, ctx):
    d = engine.avaliar(sinal(), [pos("BTCUSDT", side=Side.SHORT)],
                       contexto=ctx)
    assert d.veredicto is Veredicto.REJEITADO
    assert "hedge" in d.motivo_principal.lower() or "oposto" in d.motivo_principal


def test_revenge_trading_bloqueado_na_janela(engine, ctx):
    """A 90 min o cooldown global já expirou; quem barra é o revenge."""
    engine.registrar_trade(stop_trade(), agora_ms=1_000_000)
    d = engine.avaliar(sinal(), [], contexto=ctx,
                       agora_ms=1_000_000 + 90 * 60_000)
    assert d.veredicto is Veredicto.REJEITADO
    assert "revenge" in d.motivo_principal.lower()


def test_revenge_liberado_apos_a_janela(engine, ctx):
    engine.registrar_trade(stop_trade(), agora_ms=1_000_000)
    d = engine.avaliar(sinal(), [], contexto=ctx,
                       agora_ms=1_000_000 + 180 * 60_000)
    assert d.aprovado


def test_revenge_nao_bloqueia_outro_ativo(engine, ctx):
    """A 90 min o cooldown global (60 min) já passou, mas a janela de revenge
    (120 min) ainda vale — e ela é por ativo e direção."""
    engine.registrar_trade(stop_trade(symbol="ETHUSDT"), agora_ms=1_000_000)
    d = engine.avaliar(sinal("BTCUSDT"), [], contexto=ctx,
                       agora_ms=1_000_000 + 90 * 60_000)
    assert d.aprovado


def test_cooldown_global_morde_antes_da_janela_de_revenge(engine, ctx):
    """Duas travas com propósitos diferentes: o cooldown pausa qualquer
    entrada após um stop; o revenge impede voltar ao MESMO trade."""
    engine.registrar_trade(stop_trade(symbol="ETHUSDT"), agora_ms=1_000_000)
    d = engine.avaliar(sinal("BTCUSDT"), [], contexto=ctx,
                       agora_ms=1_000_000 + 10 * 60_000)
    assert d.veredicto is Veredicto.REJEITADO
    assert "cooldown" in d.motivo_principal


def test_revenge_nao_bloqueia_direcao_oposta(engine, ctx):
    engine.registrar_trade(stop_trade(side=Side.LONG), agora_ms=1_000_000)
    d = engine.avaliar(sinal(entry=64000, stop=65280, side=Side.SHORT), [],
                       contexto=ctx, agora_ms=1_000_000 + 90 * 60_000)
    assert d.aprovado


def test_afastar_stop_e_proibido(engine):
    p = pos("BTCUSDT", entry=64000.0, stop=62720.0)
    ok, msg = engine.validar_ajuste_de_stop(p, 62000.0)
    assert not ok
    assert "AFASTA" in msg


def test_apertar_stop_e_permitido(engine):
    p = pos("BTCUSDT", entry=64000.0, stop=62720.0)
    ok, _ = engine.validar_ajuste_de_stop(p, 63500.0)
    assert ok


def test_afastar_stop_de_short_e_proibido(engine):
    p = pos("BTCUSDT", side=Side.SHORT, entry=64000.0, stop=65280.0)
    assert not engine.validar_ajuste_de_stop(p, 66000.0)[0]
    assert engine.validar_ajuste_de_stop(p, 64500.0)[0]


def test_aviso_de_martingale_apos_perda(engine, ctx):
    engine.registrar_trade(Trade("ETHUSDT", Side.LONG, 3000, 2900, 1, 0, 1,
                                 -5.0, -1.0, "stop_loss"), agora_ms=1_000_000)
    d = engine.avaliar(sinal(), [], contexto=ctx,
                       agora_ms=1_000_000 + 200 * 60_000)
    assert any("martingale" in a.mensagem.lower() for a in d.achados)


def test_proibicoes_aparecem_no_status(engine):
    proibicoes = engine.status()["proibicoes_estruturais"]
    texto = " ".join(proibicoes).lower()
    for termo in ("martingale", "averaging down", "stop", "revenge"):
        assert termo in texto


# ======================================================= reduções
def test_sistema_degradado_reduz_em_vez_de_vetar(engine):
    d = engine.avaliar(sinal(), [], contexto=ContextoMercado(
        atr_pct=1.5, spread_pct=0.03, minutos_ate_evento_critico=999,
        saude_sistema="DEGRADED"))
    assert d.veredicto is Veredicto.APROVADO_COM_REDUCAO
    assert d.fator_reducao < 1.0


def test_risco_de_gap_reduz_tamanho(engine):
    normal = engine.avaliar(sinal(), [], contexto=ContextoMercado(
        atr_pct=1.5, spread_pct=0.03, minutos_ate_evento_critico=999))
    com_gap = engine.avaliar(sinal(), [], contexto=ContextoMercado(
        atr_pct=1.5, spread_pct=0.03, minutos_ate_evento_critico=999,
        gap_tipico_pct=5.0))
    assert com_gap.notional_usd < normal.notional_usd


def test_reducoes_se_acumulam(engine):
    d = engine.avaliar(sinal(), [], contexto=ContextoMercado(
        atr_pct=1.5, spread_pct=0.03, minutos_ate_evento_critico=999,
        saude_sistema="DEGRADED", gap_tipico_pct=5.0))
    assert d.fator_reducao < 0.5


# ============================================== concentração por fator
def test_concentracao_por_fator_de_risco_veta():
    """Três tickens de L1 são uma aposta, não três."""
    eng = RiskEngine(RiskConfig(max_posicoes_simultaneas=9,
                                max_exposicao_notional_pct=100.0), 1000.0)
    ctx = ContextoMercado(atr_pct=1.5, spread_pct=0.03,
                          minutos_ate_evento_critico=999)
    posicoes = [pos("SOLUSDT", notional=300.0),
                pos("AVAXUSDT", notional=300.0)]
    d = eng.avaliar(sinal("NEARUSDT", entry=5.0, stop=4.9), posicoes,
                    contexto=ctx)
    assert d.veredicto is Veredicto.REJEITADO
    assert any(a.regra == "concentracao_por_fator" for a in d.achados)


def test_concentracao_leve_e_apenas_aviso(engine, ctx):
    d = engine.avaliar(sinal("ETHUSDT", entry=3000.0, stop=2940.0),
                       [pos("BTCUSDT", notional=50.0)], contexto=ctx)
    assert d.aprovado
    assert any(a.regra == "concentracao_por_fator"
               and a.severidade is Severidade.AVISO for a in d.achados)


# =============================================== halt e retomada
def test_halt_bloqueia_tudo(engine, ctx):
    engine.halt("teste")
    d = engine.avaliar(sinal(), [], contexto=ctx)
    assert d.veredicto is Veredicto.TRADING_HALTED
    assert not d.aprovado


def test_retomada_exige_frase_exata(engine):
    engine.halt("teste")
    assert not engine.retomar("ok")[0]
    assert engine.halted
    assert engine.retomar("RETOMAR OPERACAO")[0]
    assert not engine.halted


def test_kill_switch_do_manager_tambem_halta(engine, ctx):
    for _ in range(30):
        engine.registrar_trade(Trade("BTCUSDT", Side.LONG, 100, 95, 1, 0, 1,
                                     -50.0, -1.0, "stop_loss"))
    assert engine.halted
    assert engine.avaliar(sinal(), [],
                          contexto=ctx).veredicto is Veredicto.TRADING_HALTED


def test_contagem_de_trades_por_dia(engine):
    for i in range(3):
        engine.registrar_trade(stop_trade(closed=1_000_000 + i),
                               agora_ms=1_000_000 + i)
    assert engine.trades_hoje(agora_ms=1_000_000) == 3
    assert engine.trades_hoje(agora_ms=1_000_000 + 86_400_000 * 2) == 0


def test_limites_extra_customizados(ctx):
    eng = RiskEngine(RiskConfig(), 1000.0,
                     extra=LimitesExtra(max_spread_pct=0.01))
    d = eng.avaliar(sinal(), [], contexto=ctx)
    assert d.veredicto is Veredicto.REJEITADO
    assert "spread" in d.motivo_principal
