"""Camada operacional: regime, anomalias, shadow mode e health monitor."""
import math

import pytest

from investai.models import Candle, Side
from investai.ops import (
    Componente, EstadoSaude, HealthMonitor, LimiaresAnomalia, LimiaresShadow,
    RegimeMercado, RelatorioAnomalias, ResultadoShadow, Severidade,
    ShadowRunner, TipoAnomalia, detectar_anomalias, detectar_regime,
    monitor_padrao,
)


def serie(fn, n=400, amp=0.004):
    return [Candle(ts=i * 3_600_000, open=fn(i), high=fn(i) * (1 + amp),
                   low=fn(i) * (1 - amp), close=fn(i), volume=1000.0)
            for i in range(n)]


def plana(n=80, close=100.0, volume=1000.0):
    return [Candle(ts=i * 3_600_000, open=close, high=close * 1.005,
                   low=close * 0.995, close=close, volume=volume)
            for i in range(n)]


# ============================================================== REGIME
@pytest.mark.parametrize("fn,amp,esperado", [
    (lambda i: 100 * (1.004 ** i), 0.004, RegimeMercado.TENDENCIA_ALTA),
    (lambda i: 300 * (0.996 ** i), 0.004, RegimeMercado.TENDENCIA_BAIXA),
    (lambda i: 100 + math.sin(i / 3) * 0.4, 0.001,
     RegimeMercado.BAIXA_VOLATILIDADE),
    (lambda i: 100 + math.sin(i / 2) * 14, 0.06,
     RegimeMercado.ALTA_VOLATILIDADE),
])
def test_classificacao_de_regime(fn, amp, esperado):
    assert detectar_regime(serie(fn, amp=amp)).regime is esperado


def test_volatilidade_relativa_nao_satura_em_tendencia():
    """Usar ATR absoluto faria toda alta sustentada parecer choque de
    volatilidade, porque o ATR cresce junto com o preço."""
    r = detectar_regime(serie(lambda i: 100 * (1.004 ** i)))
    assert r.regime is RegimeMercado.TENDENCIA_ALTA
    assert r.metricas["percentil_volatilidade"] < 0.85


def test_volatilidade_constante_neutraliza_o_percentil():
    """Com volatilidade praticamente constante, o percentil é ruído de ponto
    flutuante e não pode afirmar choque."""
    r = detectar_regime(serie(lambda i: 300 * (0.996 ** i)))
    assert r.metricas["percentil_volatilidade"] == pytest.approx(0.5)
    assert any("não é informativo" in e for e in r.evidencias)


def test_volatilidade_absoluta_alta_e_detectada_mesmo_sendo_o_normal():
    r = detectar_regime(serie(lambda i: 100 + math.sin(i / 2) * 14, amp=0.06))
    assert r.regime is RegimeMercado.ALTA_VOLATILIDADE
    assert any("termos absolutos" in e for e in r.evidencias)


def test_dados_insuficientes_desabilita_tudo():
    r = detectar_regime(plana(50))
    assert r.regime is RegimeMercado.INCERTO
    assert r.estrategias_habilitadas == []
    assert len(r.estrategias_desabilitadas) >= 4


def test_regime_habilita_familias_compativeis():
    alta = detectar_regime(serie(lambda i: 100 * (1.004 ** i)))
    assert "seguimento_de_tendencia" in alta.estrategias_habilitadas
    assert alta.habilita("seguimento_de_tendencia")
    baixa_vol = detectar_regime(serie(lambda i: 100 + math.sin(i / 3) * 0.4,
                                      amp=0.001))
    assert "reversao_a_media" in baixa_vol.estrategias_habilitadas


def test_alta_volatilidade_desabilita_estrategias_de_stop_apertado():
    r = detectar_regime(serie(lambda i: 100 + math.sin(i / 2) * 14, amp=0.06))
    assert "reversao_a_media" not in r.estrategias_habilitadas
    assert "carrego" not in r.estrategias_habilitadas


def test_risk_off_exige_dado_agregado(provider):
    """Risco agregado não se mede num único ativo."""
    velas = provider.candles("BTCUSDT", "1H", limit=400)
    sem = detectar_regime(velas)
    assert any("NÃO avaliado" in e for e in sem.evidencias)
    com = detectar_regime(velas, contexto_mercado={"correlacao_media": 0.88})
    assert RegimeMercado.RISK_OFF in com.regimes_secundarios


def test_apetite_risk_on_reportado(provider):
    r = detectar_regime(provider.candles("BTCUSDT", "1H", limit=400),
                        contexto_mercado={"apetite_risco": "risk_on"})
    assert RegimeMercado.RISK_ON in r.regimes_secundarios


def test_ema_discordante_reduz_confianca(provider):
    """Movimento forte contra a estrutura maior é tendência com menos
    confiança, não ausência de regime."""
    velas = provider.candles("BTCUSDT", "1H", limit=400)
    r = detectar_regime(velas)
    if any("NÃO confirma" in e for e in r.evidencias):
        assert r.confianca < 0.7


# =========================================================== ANOMALIAS
def test_serie_normal_nao_gera_anomalia():
    assert detectar_anomalias("BTCUSDT", plana()) == []


def test_volume_anormal_detectado():
    v = plana()
    v[-1] = Candle(ts=v[-1].ts, open=100, high=100.5, low=99.5, close=100,
                   volume=10_000.0)
    anomalias = detectar_anomalias("BTCUSDT", v)
    assert any(a.tipo is TipoAnomalia.VOLUME for a in anomalias)


def test_anomalia_de_volume_nao_indica_direcao():
    """Volume atípico abre investigação; não diz para onde o preço vai."""
    v = plana()
    v[-1] = Candle(ts=v[-1].ts, open=100, high=100.5, low=99.5, close=100,
                   volume=10_000.0)
    a = next(x for x in detectar_anomalias("BTCUSDT", v)
             if x.tipo is TipoAnomalia.VOLUME)
    assert "não indica direção" in a.acao_recomendada


def test_gap_bloqueia_entrada():
    v = plana()
    v[-1] = Candle(ts=v[-1].ts, open=104, high=105, low=103.5, close=104.5,
                   volume=1200.0)
    a = next(x for x in detectar_anomalias("BTCUSDT", v)
             if x.tipo is TipoAnomalia.GAP)
    assert a.bloqueia_entrada
    assert "stop não protege" in a.acao_recomendada


def test_zscore_robusto_nao_e_mascarado_pelo_proprio_outlier():
    """Com desvio-padrão, o pico entra no sigma e reduz o próprio z-score."""
    v = plana(70)
    v[-1] = Candle(ts=v[-1].ts, open=100, high=100.5, low=99.5, close=100,
                   volume=15_000.0)
    a = next(x for x in detectar_anomalias("BTCUSDT", v)
             if x.tipo is TipoAnomalia.VOLUME)
    assert a.desvios > 5.0
    assert a.severidade in (Severidade.ALTA, Severidade.CRITICA)


def test_funding_extremo_detectado():
    anomalias = detectar_anomalias(
        "BTCUSDT", plana(),
        derivativos={"funding_rate": 0.0035},
        historico_derivativos={"funding_rate": [0.0001] * 20})
    a = next(x for x in anomalias if x.tipo is TipoAnomalia.FUNDING)
    assert "fim da fila" in a.acao_recomendada


def test_onda_de_liquidacoes_bloqueia_entrada():
    anomalias = detectar_anomalias(
        "BTCUSDT", plana(),
        derivativos={"liquidacoes_24h_usd": 9.2e8},
        historico_derivativos={"liquidacoes_24h_usd": [4e7] * 20})
    a = next(x for x in anomalias if x.tipo is TipoAnomalia.LIQUIDACOES)
    assert a.bloqueia_entrada
    assert "fechamento forçado" in a.acao_recomendada


def test_spread_anormal_bloqueia_entrada():
    anomalias = detectar_anomalias(
        "BTCUSDT", plana(), derivativos={"spread_pct": 0.42},
        historico_derivativos={"spread_pct": [0.03] * 20})
    assert any(x.tipo is TipoAnomalia.SPREAD and x.bloqueia_entrada
               for x in anomalias)


def test_queda_de_liquidez_detectada():
    anomalias = detectar_anomalias(
        "BTCUSDT", plana(), derivativos={"volume_24h_usd": 3e7},
        historico_derivativos={"volume_24h_usd": [1.2e9] * 20})
    a = next(x for x in anomalias if x.tipo is TipoAnomalia.LIQUIDEZ)
    assert "saída pode não existir" in a.acao_recomendada


def test_salto_de_open_interest_detectado():
    anomalias = detectar_anomalias(
        "BTCUSDT", plana(),
        derivativos={"open_interest_variacao_pct": 34.0})
    assert any(x.tipo is TipoAnomalia.OPEN_INTEREST for x in anomalias)


def test_relatorio_agrega_e_decide_bloqueio():
    r = RelatorioAnomalias("BTCUSDT", detectar_anomalias(
        "BTCUSDT", plana(),
        derivativos={"liquidacoes_24h_usd": 9.2e8, "spread_pct": 0.42},
        historico_derivativos={"liquidacoes_24h_usd": [4e7] * 20,
                               "spread_pct": [0.03] * 20}))
    assert r.bloqueia_entrada
    assert r.pior_severidade in (Severidade.ALTA, Severidade.CRITICA)
    assert len(r.motivos_de_bloqueio()) >= 2
    assert "não operação" in r.to_dict()["observacao"]


def test_limiares_customizados_sao_respeitados():
    v = plana()
    v[-1] = Candle(ts=v[-1].ts, open=100, high=100.5, low=99.5, close=100,
                   volume=1500.0)
    frouxo = detectar_anomalias("BTCUSDT", v)
    rigido = detectar_anomalias("BTCUSDT", v,
                                limiares=LimiaresAnomalia(volume_desvios=0.5))
    assert len(rigido) > len(frouxo)


def test_serie_curta_nao_explode():
    assert detectar_anomalias("BTCUSDT", plana(5)) == []


# ============================================================= SHADOW
def _decisao(sr, i=0):
    return sr.registrar_decisao("BTCUSDT", Side.LONG, preco=64000.0,
                                stop=62720.0, alvo=66300.0, size=0.004,
                                score=72.0, agora_ms=i * 60_000)


def test_execucao_fiel():
    sr = ShadowRunner()
    d = _decisao(sr)
    sr.registrar_execucao(d.id, preco_execucao=64010.0, size_executada=0.004,
                          executado_em_ms=500)
    assert d.resultado is ResultadoShadow.FIEL
    assert d.slippage_pct == pytest.approx(0.0156, abs=0.001)


def test_slippage_material_marca_desvio():
    sr = ShadowRunner()
    d = _decisao(sr)
    sr.registrar_execucao(d.id, preco_execucao=64000 * 1.005,
                          size_executada=0.004, executado_em_ms=500)
    assert d.resultado is ResultadoShadow.DESVIO_MATERIAL
    assert any("longe do preço" in o for o in d.observacoes)


def test_fill_parcial_marca_desvio():
    sr = ShadowRunner()
    d = _decisao(sr)
    sr.registrar_execucao(d.id, preco_execucao=64000.0,
                          size_executada=0.002, executado_em_ms=500)
    assert d.resultado is ResultadoShadow.DESVIO_MATERIAL
    assert any("menor que a dimensionada" in o for o in d.observacoes)


def test_latencia_alta_marca_desvio():
    sr = ShadowRunner(LimiaresShadow(latencia_toleravel_ms=500))
    d = _decisao(sr)
    sr.registrar_execucao(d.id, preco_execucao=64000.0, size_executada=0.004,
                          executado_em_ms=5_000)
    assert d.resultado is ResultadoShadow.DESVIO_MATERIAL


def test_decisao_nao_executavel_e_registrada():
    """O backtest contaria essa operação; ao vivo ela não existiria."""
    sr = ShadowRunner()
    d = _decisao(sr)
    sr.registrar_execucao(d.id, preco_execucao=None, size_executada=None,
                          executado_em_ms=None,
                          motivo_nao_executado="livro sem contraparte")
    assert d.resultado is ResultadoShadow.NAO_EXECUTAVEL
    assert "livro sem contraparte" in d.observacoes


def test_venda_tem_slippage_invertido():
    sr = ShadowRunner()
    d = sr.registrar_decisao("BTCUSDT", Side.SHORT, preco=64000.0,
                             stop=65280.0, alvo=61700.0, size=0.004)
    # Vender mais barato que o decidido é pior para o operador.
    sr.registrar_execucao(d.id, preco_execucao=63900.0, size_executada=0.004,
                          executado_em_ms=300)
    assert d.slippage_pct > 0


def test_fidelidade_agrega_fieis_e_toleraveis():
    sr = ShadowRunner()
    for i in range(10):
        d = _decisao(sr, i)
        sr.registrar_execucao(d.id, preco_execucao=64000.0,
                              size_executada=0.004, executado_em_ms=300)
    for i in range(2):
        d = _decisao(sr, 20 + i)
        sr.registrar_execucao(d.id, preco_execucao=64000 * 1.006,
                              size_executada=0.004, executado_em_ms=300)
    r = sr.relatorio(dias_observados=20)
    assert r.avaliadas == 12
    assert r.fidelidade == pytest.approx(10 / 12, abs=0.01)


def test_fidelidade_baixa_gera_aviso():
    sr = ShadowRunner()
    for i in range(25):
        d = _decisao(sr, i)
        if i % 2:
            sr.registrar_execucao(d.id, preco_execucao=64000 * 1.01,
                                  size_executada=0.004, executado_em_ms=300)
        else:
            sr.registrar_execucao(d.id, preco_execucao=64000.0,
                                  size_executada=0.004, executado_em_ms=300)
    r = sr.relatorio(dias_observados=20)
    assert r.fidelidade < 0.85
    assert any("decide uma coisa e executa outra" in a for a in r.avisos)


def test_muitas_nao_executaveis_geram_aviso():
    sr = ShadowRunner()
    for i in range(20):
        d = _decisao(sr, i)
        if i < 5:
            sr.registrar_execucao(d.id, preco_execucao=None,
                                  size_executada=None, executado_em_ms=None)
        else:
            sr.registrar_execucao(d.id, preco_execucao=64000.0,
                                  size_executada=0.004, executado_em_ms=300)
    r = sr.relatorio()
    assert any("não eram executáveis" in a for a in r.avisos)


def test_decisao_pendente_nao_conta_na_fidelidade():
    sr = ShadowRunner()
    _decisao(sr)
    r = sr.relatorio()
    assert r.pendentes == 1
    assert r.avaliadas == 0
    assert any("nenhuma decisão avaliada" in a for a in r.avisos)


def test_movimento_com_stop_e_alvo_tocados_e_registrado():
    sr = ShadowRunner()
    d = _decisao(sr)
    sr.registrar_execucao(d.id, preco_execucao=64000.0, size_executada=0.004,
                          executado_em_ms=300)
    sr.registrar_movimento(d.id, preco_max=67000.0, preco_min=62000.0)
    assert any("qual veio primeiro" in o for o in d.observacoes)


def test_relatorio_explica_o_que_fidelidade_mede():
    assert "EXECUTA o que DECIDE" in ShadowRunner().relatorio().to_dict()[
        "observacao"]


# ============================================================= HEALTH
def test_tudo_saudavel():
    m = monitor_padrao(checar_dados=lambda: True, checar_exchange=lambda: True,
                       checar_banco=lambda: True, checar_risco=lambda: True,
                       checar_noticias=lambda: True)
    r = m.checar_tudo()
    assert r.estado_geral is EstadoSaude.HEALTHY
    assert r.pode_abrir_posicao


def test_componente_nao_critico_degrada_sem_bloquear():
    m = monitor_padrao(checar_dados=lambda: True, checar_exchange=lambda: True,
                       checar_banco=lambda: True, checar_risco=lambda: True,
                       checar_noticias=lambda: False)
    r = m.checar_tudo()
    assert r.estado_geral is EstadoSaude.DEGRADED
    assert r.pode_abrir_posicao


def test_falha_isolada_degrada_e_repetida_derruba():
    estados = {"dados": True}
    m = monitor_padrao(checar_dados=lambda: estados["dados"],
                       checar_exchange=lambda: True, checar_banco=lambda: True,
                       checar_risco=lambda: True, checar_noticias=lambda: True)
    m.checar_tudo()
    estados["dados"] = False
    assert m.checar_tudo().estado_geral is EstadoSaude.DEGRADED
    m.checar_tudo()
    r = m.checar_tudo()
    assert r.estado_geral is EstadoSaude.OFFLINE
    assert not r.pode_abrir_posicao


def test_offline_impede_abrir_mas_nao_gerenciar():
    """A exposição não desaparece junto com o feed."""
    m = monitor_padrao(checar_dados=lambda: False,
                       checar_exchange=lambda: True, checar_banco=lambda: True,
                       checar_risco=lambda: True, checar_noticias=lambda: True)
    for _ in range(3):
        r = m.checar_tudo()
    assert not r.pode_abrir_posicao
    assert r.pode_gerenciar_posicao
    assert any("NÃO ABRIR POSIÇÃO" in x for x in r.motivos)


def test_recuperacao_zera_o_contador():
    estados = {"dados": False}
    m = monitor_padrao(checar_dados=lambda: estados["dados"],
                       checar_exchange=lambda: True, checar_banco=lambda: True,
                       checar_risco=lambda: True, checar_noticias=lambda: True)
    for _ in range(3):
        m.checar_tudo()
    assert m.relatorio().estado_geral is EstadoSaude.OFFLINE
    estados["dados"] = True
    assert m.checar_tudo().estado_geral is EstadoSaude.HEALTHY


def test_excecao_no_check_e_tratada():
    def explode():
        raise RuntimeError("timeout no feed")
    m = monitor_padrao(checar_dados=explode, checar_exchange=lambda: True,
                       checar_banco=lambda: True, checar_risco=lambda: True,
                       checar_noticias=lambda: True)
    r = m.checar_tudo()
    assert r.estado_geral is EstadoSaude.DEGRADED
    assert "timeout no feed" in r.motivos[0]


def test_componente_nunca_verificado_impede_abrir():
    m = HealthMonitor()
    m.registrar(Componente(nome="critico", critico=True, checar=None))
    r = m.relatorio()
    assert r.estado_geral is EstadoSaude.DEGRADED
    assert any("nunca verificado" in x for x in r.motivos)


def test_monitor_vazio_nao_libera_operacao():
    r = HealthMonitor().relatorio()
    assert r.estado_geral is EstadoSaude.DESCONHECIDO
    assert not r.pode_abrir_posicao


def test_marcacao_manual_de_estado():
    m = monitor_padrao(checar_dados=lambda: True, checar_exchange=lambda: True,
                       checar_banco=lambda: True, checar_risco=lambda: True,
                       checar_noticias=lambda: True)
    m.checar_tudo()
    m.marcar("exchange", EstadoSaude.OFFLINE, "credencial revogada")
    c = m.componentes["exchange"]
    c.falhas_consecutivas = 3
    r = m.relatorio()
    assert r.estado_geral is EstadoSaude.OFFLINE
    assert not r.pode_abrir_posicao
