"""Arquitetura multiagente: N/A, abstenção, consenso e NÃO OPERAR."""
import random

import pytest

from investai.agents import (
    AgenteDerivativos, AgenteFundamentos, AgenteLiquidez, AgenteMacro,
    AgenteNoticias, AgenteQuantitativo, AgenteTecnico, ChiefInvestmentEngine,
    ContextoAnalise, CriteriosConsenso, DecisaoFinal, ParecerAgente, Postura,
    agentes_padrao, faixa_do_score, postura_de_valor,
)
from investai.data.quality import QualityReport, QualityStatus
from investai.data.symbols import AssetClass
from investai.risk import ContextoMercado, RiskEngine, Veredicto
from investai.validation import calcular_ev


@pytest.fixture
def estatistica_boa():
    random.seed(4)
    return calcular_ev([2.0 if random.random() < 0.47 else -1.0
                        for _ in range(140)])


@pytest.fixture
def features(provider, settings):
    from investai.analysis import SerieFeatures
    out = {}
    for tf in settings.signal.timeframes:
        v = provider.candles("BTCUSDT", tf, limit=400)
        out[tf] = SerieFeatures("BTCUSDT", tf, v).at(len(v) - 1)
    return out


def ctx_cripto(features, provider, estatistica=None, **kw):
    base = dict(
        symbol="BTCUSDT", asset_class=AssetClass.CRIPTO, timeframe="1H",
        features=features, snapshot=provider.ticker("BTCUSDT"),
        derivativos={"open_interest_variacao_pct": 6.0, "basis_pct": 0.25,
                     "spread_pct": 0.03, "liquidacoes_24h_usd": 4e7},
        macro={"regime_risco": "risk_on", "direcao_juros": "queda",
               "surpresa_inflacao_pct": -0.1},
        noticias=[], estatistica=estatistica,
        qualidade_dados=QualityReport(QualityStatus.OK))
    base.update(kw)
    return ContextoAnalise(**base)


# ============================================================= pesos
def test_pesos_nominais_somam_um():
    assert sum(a.peso for a in agentes_padrao()) == pytest.approx(1.0)


def test_agente_de_risco_nao_esta_entre_os_que_pontuam():
    """Misturar veto com pontuação permitiria score alto compensar risco."""
    assert "risco" not in [a.nome for a in agentes_padrao()]
    assert "risco" in ChiefInvestmentEngine().config()["observacao"]


# ====================================================== N/A vs sem dados
def test_na_e_sem_dados_ficam_fora_do_calculo():
    for postura in (Postura.NAO_APLICAVEL, Postura.SEM_DADOS):
        p = ParecerAgente("x", postura, valor=0.9, confianca=1.0,
                          peso_base=0.5)
        assert not p.entra_no_calculo
        assert p.peso_efetivo == 0.0


def test_derivativos_marca_na_para_fii(features, provider):
    ctx = ContextoAnalise(symbol="HGLG11", asset_class=AssetClass.FII,
                          features=features,
                          snapshot=provider.ticker("BTCUSDT"))
    p = AgenteDerivativos().analisar(ctx, +1)
    assert p.postura is Postura.NAO_APLICAVEL
    assert "não tem mercado de futuros" in p.evidencias[0]


def test_fundamentos_marca_na_para_cripto(features, provider):
    """Derivar 'fundamento' de capitalização seria inventar métrica."""
    p = AgenteFundamentos().analisar(
        ctx_cripto(features, provider), +1)
    assert p.postura is Postura.NAO_APLICAVEL
    assert "demonstrativo" in p.evidencias[0]


def test_fundamentos_sem_fonte_e_sem_dados_nao_na(features, provider):
    ctx = ContextoAnalise(symbol="PETR4", asset_class=AssetClass.ACAO,
                          features=features, fundamentos=None)
    p = AgenteFundamentos().analisar(ctx, +1)
    assert p.postura is Postura.SEM_DADOS
    assert "FONTE NÃO CONFIGURADA" in p.evidencias[0]


def test_noticias_sem_fonte_se_abstem_em_vez_de_dizer_neutro(features,
                                                             provider):
    """'Sem notícia' e 'notícia neutra' são coisas diferentes."""
    ctx = ctx_cripto(features, provider, noticias=None)
    p = AgenteNoticias().analisar(ctx, +1)
    assert p.postura is Postura.SEM_DADOS
    assert not p.entra_no_calculo


def test_noticias_lista_vazia_e_neutro_nao_abstencao(features, provider):
    p = AgenteNoticias().analisar(ctx_cripto(features, provider, noticias=[]),
                                  +1)
    assert p.postura is Postura.NEUTRO
    assert p.entra_no_calculo


def test_macro_sem_fonte_se_abstem(features, provider):
    p = AgenteMacro().analisar(ctx_cripto(features, provider, macro=None), +1)
    assert p.postura is Postura.SEM_DADOS


def test_quantitativo_sem_estatistica_se_abstem(features, provider):
    p = AgenteQuantitativo().analisar(
        ctx_cripto(features, provider, estatistica=None), +1)
    assert p.postura is Postura.SEM_DADOS
    assert "INSUFICIENTE" in p.evidencias[0]


# ========================================================= notícias
def test_rumor_de_alto_impacto_tem_peso_minimo(features, provider):
    ctx = ctx_cripto(features, provider, noticias=[{
        "titulo": "Fundo soberano pode comprar BTC, dizem fontes",
        "classificacao": "rumor", "impacto": "critico", "direcao": 0.9,
        "fonte": "perfil anônimo"}])
    p = AgenteNoticias().analisar(ctx, +1)
    assert p.peso_efetivo < 0.02
    assert any("NÃO CONFIRMADA" in c for c in p.contraindicacoes)


def test_noticia_confirmada_pesa_muito_mais_que_rumor(features, provider):
    base = {"titulo": "t", "impacto": "alto", "direcao": 0.6, "fonte": "f"}
    conf = AgenteNoticias().analisar(ctx_cripto(
        features, provider,
        noticias=[{**base, "classificacao": "confirmada"}]), +1)
    rumor = AgenteNoticias().analisar(ctx_cripto(
        features, provider,
        noticias=[{**base, "classificacao": "rumor"}]), +1)
    assert conf.peso_efetivo > rumor.peso_efetivo * 3


def test_apenas_ruido_e_opiniao_nao_move_nada(features, provider):
    ctx = ctx_cripto(features, provider, noticias=[
        {"titulo": "a", "classificacao": "opiniao", "impacto": "ruido",
         "direcao": 1.0, "fonte": "x"}])
    p = AgenteNoticias().analisar(ctx, +1)
    assert p.valor == pytest.approx(0.0)


# ======================================================= quantitativo
def test_quantitativo_penaliza_piso_de_ic_negativo(features, provider):
    random.seed(9)
    fraco = calcular_ev([2.0 if random.random() < 0.40 else -1.0
                         for _ in range(34)])
    forte = calcular_ev([2.0 if random.random() < 0.47 else -1.0
                         for _ in range(200)])
    p_fraco = AgenteQuantitativo().analisar(
        ctx_cripto(features, provider, estatistica=fraco), +1)
    p_forte = AgenteQuantitativo().analisar(
        ctx_cripto(features, provider, estatistica=forte), +1)
    assert p_fraco.confianca < p_forte.confianca


def test_confianca_do_quantitativo_cresce_com_a_amostra(features, provider):
    confs = []
    for n in (20, 60, 200):
        random.seed(1)
        est = calcular_ev([2.0 if random.random() < 0.47 else -1.0
                           for _ in range(n)])
        confs.append(AgenteQuantitativo().analisar(
            ctx_cripto(features, provider, estatistica=est), +1).confianca)
    assert confs == sorted(confs)


def test_amplitude_do_ic_de_expectativa_nao_gera_alarme_falso(features,
                                                              provider,
                                                              estatistica_boa):
    """0,3R de amplitude é normal; aplicar o limiar de proporção aqui
    produziria contraindicação em toda amostra saudável."""
    p = AgenteQuantitativo().analisar(
        ctx_cripto(features, provider, estatistica=estatistica_boa), +1)
    assert not any("largo demais" in c for c in p.contraindicacoes)


# ========================================================== liquidez
def test_liquidez_sem_dados_se_abstem(features):
    ctx = ContextoAnalise(symbol="X", asset_class=AssetClass.CRIPTO,
                          features=features, snapshot=None, derivativos={})
    assert AgenteLiquidez().analisar(ctx, +1).postura is Postura.SEM_DADOS


def test_liquidez_nao_tem_direcao(features, provider):
    """Liquidez habilita ou impede, para os dois lados igualmente."""
    ctx = ctx_cripto(features, provider)
    compra = AgenteLiquidez().analisar(ctx, +1)
    venda = AgenteLiquidez().analisar(ctx, -1)
    assert compra.valor == pytest.approx(venda.valor)


# ===================================================== consolidação
def test_caso_completo_e_validado(features, provider, estatistica_boa):
    chief = ChiefInvestmentEngine()
    c = chief.melhor(ctx_cripto(features, provider, estatistica_boa,
                                noticias=[{"titulo": "entrada de ETF",
                                           "classificacao": "confirmada",
                                           "impacto": "alto", "direcao": 0.6,
                                           "fonte": "comunicado"}]))
    assert c.decisao is DecisaoFinal.VALIDADA
    assert c.operavel
    assert c.cobertura > 0.7


def test_peso_de_na_e_redistribuido(features, provider, estatistica_boa):
    """FII não pode ser penalizado por não ter funding."""
    chief = ChiefInvestmentEngine()
    ctx = ContextoAnalise(
        symbol="HGLG11", asset_class=AssetClass.FII, timeframe="1H",
        features=features, snapshot=provider.ticker("BTCUSDT"),
        fundamentos={"nome": "F", "segmento": "logistica", "preco": 158.0,
                     "dy_12m": 8.9, "p_vp": 0.94, "vacancia_pct": 3.5,
                     "liquidez_diaria": 9.5e6, "num_imoveis": 19,
                     "patrimonio_liquido": 4.1e9},
        macro={"regime_risco": "neutro", "direcao_juros": "queda",
               "surpresa_inflacao_pct": 0.0},
        estatistica=estatistica_boa,
        qualidade_dados=QualityReport(QualityStatus.OK))
    c = chief.consolidar(ctx, +1)
    assert "derivativos" in c.nao_aplicaveis
    assert c.cobertura > 0.6
    assert c.decisao in (DecisaoFinal.VALIDADA,
                         DecisaoFinal.AGUARDAR_CONFIRMACAO)


def test_sem_estatistica_nada_e_validado(features, provider):
    """Nada pode ser 'validado pelo modelo' sem amostra histórica."""
    chief = ChiefInvestmentEngine()
    c = chief.melhor(ctx_cripto(features, provider, estatistica=None))
    assert c.decisao is not DecisaoFinal.VALIDADA
    assert any("quantitativo se absteve" in m for m in c.motivos_decisao)


def test_dados_ruins_dao_dados_insuficientes(features, provider,
                                             estatistica_boa):
    chief = ChiefInvestmentEngine()
    ctx = ctx_cripto(features, provider, estatistica_boa,
                     qualidade_dados=QualityReport(
                         QualityStatus.INADEQUADO, ["feed congelado"]))
    c = chief.consolidar(ctx, +1)
    assert c.decisao is DecisaoFinal.DADOS_INSUFICIENTES
    assert "NÃO É POSSÍVEL VALIDAR" in c.motivos_decisao[0]


def test_cobertura_baixa_da_dados_insuficientes(features, provider,
                                                estatistica_boa):
    chief = ChiefInvestmentEngine(criterios=CriteriosConsenso(
        cobertura_minima=0.95))
    c = chief.consolidar(ctx_cripto(features, provider, estatistica_boa), +1)
    assert c.decisao is DecisaoFinal.DADOS_INSUFICIENTES
    assert any("cobertura" in m for m in c.motivos_decisao)


def test_poucos_agentes_participando_da_dados_insuficientes(features):
    chief = ChiefInvestmentEngine()
    ctx = ContextoAnalise(symbol="X", asset_class=AssetClass.CRIPTO,
                          features={}, snapshot=None)
    c = chief.consolidar(ctx, +1)
    assert c.decisao is DecisaoFinal.DADOS_INSUFICIENTES


def test_veto_do_risco_tem_a_palavra_final(features, provider,
                                           estatistica_boa):
    """Mesmo com todos os agentes concordando."""
    from investai.config import RiskConfig
    from investai.models import Regime, Side, Signal, SignalGrade
    chief = ChiefInvestmentEngine()
    eng = RiskEngine(RiskConfig(), 1000.0)
    eng.halt("teste")
    sinal = Signal(symbol="BTCUSDT", timeframe="1H", side=Side.LONG,
                   grade=SignalGrade.A, score=90.0, entry=64000.0,
                   stop_loss=62720.0, take_profits=[66000.0], risk_reward=2.0,
                   atr=900.0, regime=Regime.TENDENCIA_ALTA)
    risco = eng.avaliar(sinal, [], contexto=ContextoMercado(atr_pct=1.5))
    assert risco.veredicto is Veredicto.TRADING_HALTED

    c = chief.consolidar(ctx_cripto(features, provider, estatistica_boa), +1,
                         risco=risco)
    assert c.decisao is DecisaoFinal.REJEITADA_PELO_RISCO
    assert not c.operavel


def test_confirmacao_multipla_e_exigida(features, provider, estatistica_boa):
    """Nenhuma operação depende de um único ângulo de análise."""
    chief = ChiefInvestmentEngine(criterios=CriteriosConsenso(
        min_agentes_concordantes=7))
    c = chief.consolidar(ctx_cripto(features, provider, estatistica_boa), +1)
    assert c.decisao is not DecisaoFinal.VALIDADA
    assert any("concordam com a direção" in m for m in c.motivos_decisao)


def test_nao_operar_e_saida_normal(features, provider):
    """Score baixo sem evidência resulta em NÃO OPERAR, não em erro."""
    random.seed(2)
    ruim = calcular_ev([-1.0 if random.random() < 0.7 else 1.0
                        for _ in range(120)])
    chief = ChiefInvestmentEngine()
    c = chief.consolidar(ctx_cripto(features, provider, ruim), +1)
    assert c.decisao in (DecisaoFinal.NAO_OPERAR, DecisaoFinal.OBSERVAR)
    assert c.motivos_decisao


def test_agente_que_lanca_excecao_nao_derruba_os_outros(features, provider,
                                                        estatistica_boa):
    class Explosivo(AgenteTecnico):
        nome = "explosivo"
        def analisar(self, ctx, direcao):
            raise RuntimeError("falha interna")

    chief = ChiefInvestmentEngine(agentes=[Explosivo(), AgenteQuantitativo(),
                                           AgenteLiquidez(), AgenteMacro()])
    c = chief.consolidar(ctx_cripto(features, provider, estatistica_boa), +1)
    explosivo = next(p for p in c.pareceres if p.agente == "explosivo")
    assert explosivo.postura is Postura.SEM_DADOS
    assert len(c.pareceres) == 4


def test_avalia_os_dois_lados(features, provider, estatistica_boa):
    """Evita o viés de só procurar compra em mercado que está caindo."""
    chief = ChiefInvestmentEngine()
    compra, venda = chief.avaliar_ambas_direcoes(
        ctx_cripto(features, provider, estatistica_boa))
    assert compra.direcao == "compra" and venda.direcao == "venda"
    assert compra.score != venda.score


# ============================================================ faixas
@pytest.mark.parametrize("score,rotulo", [
    (10.0, "REJEITAR"), (50.0, "OBSERVAR"),
    (65.0, "OPORTUNIDADE POTENCIAL"),
    (80.0, "CONVICÇÃO QUANTITATIVA ELEVADA"),
])
def test_faixas_do_score(score, rotulo):
    assert faixa_do_score(score) == rotulo


def test_faixa_excepcional_exige_confirmacoes():
    assert "múltiplas confirmações" in faixa_do_score(95.0)


def test_score_nao_e_probabilidade(features, provider, estatistica_boa):
    chief = ChiefInvestmentEngine()
    d = chief.consolidar(ctx_cripto(features, provider,
                                    estatistica_boa), +1).to_dict()
    assert "NÃO representa probabilidade de lucro" in d["aviso_score"]


@pytest.mark.parametrize("valor,postura", [
    (0.8, Postura.FORTE_ALTA), (0.3, Postura.ALTA), (0.0, Postura.NEUTRO),
    (-0.3, Postura.BAIXA), (-0.8, Postura.FORTE_BAIXA),
])
def test_traducao_de_valor_para_postura(valor, postura):
    assert postura_de_valor(valor) is postura
