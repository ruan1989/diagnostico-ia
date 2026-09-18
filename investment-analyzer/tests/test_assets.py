"""Multiativos: ações, renda fixa, ETFs e comparação entre classes."""
import pytest

from investai.assets import (
    CandidatoComparacao, CenarioMacro, Indexador, LIMITE_FGC,
    PerfilInvestidor, PESOS_ACAO, PESOS_ETF, TipoRendaFixa, Titulo,
    aliquota_ir, analisar_acao, analisar_etf, analisar_titulo, comparar,
    comparar_etfs, comparar_titulos,
)
from investai.data.quality import FONTE_NAO_CONFIGURADA
from investai.data.symbols import AssetClass


# =============================================================== AÇÕES
FUND_BOA = {
    "nome": "Empresa Sólida", "setor": "utilities", "preco": 38.0,
    "roe": 18.0, "roic": 15.0, "margem_liquida": 14.0, "margem_ebitda": 32.0,
    "crescimento_receita": 9.0, "crescimento_lucro": 11.0,
    "divida_liquida_ebitda": 1.2, "divida_liquida_patrimonio": 0.35,
    "fluxo_caixa_livre": 4.2e9, "lucro_liquido": 5.0e9, "p_l": 9.5,
    "p_vp": 1.6, "ev_ebitda": 5.8, "dividend_yield": 9.0, "payout": 65,
}


def test_pesos_de_acao_somam_um():
    assert sum(PESOS_ACAO.values()) == pytest.approx(1.0)


def test_acao_sem_fundamentos_e_indisponivel():
    """Não existe análise de ação por gráfico neste sistema."""
    a = analisar_acao("PETR4", None)
    assert not a.disponivel
    assert FONTE_NAO_CONFIGURADA in a.mensagem
    assert a.dados_faltando


def test_acao_com_bons_fundamentos_pontua_alto():
    a = analisar_acao("XYZQ3", FUND_BOA)
    assert a.disponivel
    assert a.score > 70
    assert a.cobertura == pytest.approx(1.0)
    assert not a.alertas


def test_acao_endividada_que_queima_caixa_e_reprovada():
    ruim = {**FUND_BOA, "roe": 4.0, "roic": 3.0, "margem_liquida": -2.0,
            "divida_liquida_ebitda": 5.2, "divida_liquida_patrimonio": 1.8,
            "fluxo_caixa_livre": -1.5e9, "crescimento_receita": -14.0,
            "crescimento_lucro": -40.0, "payout": 140}
    a = analisar_acao("ABCD4", ruim)
    assert a.score < 40
    assert a.classificacao == "evitar"
    textos = " ".join(a.alertas).lower()
    for termo in ("dívida", "caixa", "payout", "prejuízo", "receita"):
        assert termo in textos


def test_cobertura_insuficiente_marca_indisponivel():
    a = analisar_acao("EFGH3", {"p_l": 10.0, "p_vp": 1.2})
    assert not a.disponivel
    assert a.cobertura < 0.5
    assert "cobertura" in a.mensagem


def test_ausencia_de_dado_nao_vira_nota_baixa():
    """Deve reduzir a cobertura, não penalizar o score."""
    completa = analisar_acao("A", FUND_BOA)
    sem_valuation = analisar_acao("A", {k: v for k, v in FUND_BOA.items()
                                        if k not in ("p_l", "p_vp",
                                                     "ev_ebitda")})
    assert sem_valuation.cobertura < completa.cobertura
    assert abs(sem_valuation.score - completa.score) < 15


def test_lucro_crescendo_muito_acima_da_receita_e_penalizado():
    """Costuma ser corte de custo ou evento não recorrente."""
    normal = analisar_acao("A", {**FUND_BOA, "crescimento_receita": 9.0,
                                 "crescimento_lucro": 11.0})
    suspeito = analisar_acao("A", {**FUND_BOA, "crescimento_receita": 2.0,
                                   "crescimento_lucro": 45.0})
    f_normal = next(f for f in normal.fatores if f.nome == "crescimento")
    f_susp = next(f for f in suspeito.fatores if f.nome == "crescimento")
    assert "recorrente" in f_susp.detalhe
    assert f_susp.valor < f_normal.valor + 0.5


# ========================================================= RENDA FIXA
MACRO = CenarioMacro(cdi_aa=10.5, selic_aa=10.5, ipca_aa=4.2)


@pytest.mark.parametrize("dias,esperado", [
    (100, 0.225), (300, 0.20), (600, 0.175), (900, 0.15),
])
def test_tabela_regressiva_de_ir(dias, esperado):
    assert aliquota_ir(dias, TipoRendaFixa.CDB) == esperado


@pytest.mark.parametrize("tipo", [TipoRendaFixa.LCI, TipoRendaFixa.LCA,
                                  TipoRendaFixa.CRI, TipoRendaFixa.CRA,
                                  TipoRendaFixa.DEBENTURE_INCENTIVADA])
def test_instrumentos_isentos_de_ir(tipo):
    assert aliquota_ir(100, tipo) == 0.0


def test_sem_macro_nao_ha_comparacao():
    """'115% do CDI' e 'IPCA+6%' não são comparáveis sem CDI e IPCA."""
    t = Titulo("x", TipoRendaFixa.CDB, Indexador.CDI, 115, 720)
    a = analisar_titulo(t, CenarioMacro())
    assert not a.disponivel
    assert FONTE_NAO_CONFIGURADA in a.mensagem


def test_ipca_mais_usa_composicao_nao_soma():
    """(1+ipca)(1+spread)-1, não ipca+spread."""
    t = Titulo("ipca", TipoRendaFixa.TESOURO, Indexador.IPCA, 6.0, 1800)
    a = analisar_titulo(t, MACRO)
    esperado = ((1.042 * 1.06) - 1) * 100
    assert a.bruto_aa == pytest.approx(esperado, abs=0.01)
    assert a.bruto_aa > 4.2 + 6.0 - 0.01   # composto > soma


def test_pos_cdi_converte_percentual():
    t = Titulo("cdb", TipoRendaFixa.CDB, Indexador.CDI, 120, 720)
    assert analisar_titulo(t, MACRO).bruto_aa == pytest.approx(12.6)


def test_retorno_real_desconta_inflacao():
    t = Titulo("pre", TipoRendaFixa.TESOURO, Indexador.PRE, 11.8, 1460)
    a = analisar_titulo(t, MACRO)
    assert a.real_liquido_aa < a.liquido_aa < a.bruto_aa


def test_isencao_faz_liquido_igual_ao_bruto():
    t = Titulo("lci", TipoRendaFixa.LCI, Indexador.CDI, 96, 730)
    a = analisar_titulo(t, MACRO)
    assert a.aliquota_ir == 0.0
    assert a.liquido_aa == pytest.approx(a.bruto_aa)


def test_retorno_real_negativo_gera_alerta():
    t = Titulo("ruim", TipoRendaFixa.CDB, Indexador.CDI, 40, 100)
    a = analisar_titulo(t, MACRO)
    assert a.real_liquido_aa < 0
    assert any("NEGATIVO" in x for x in a.alertas)


def test_fgc_mitiga_mas_nao_elimina_risco_de_credito():
    """Zerar a penalidade faria um emissor nota 5 parecer Tesouro."""
    frageis = Titulo("cdb frágil", TipoRendaFixa.CDB, Indexador.CDI, 128,
                     1080, "Banco pequeno", risco_credito=5)
    solido = Titulo("cdb sólido", TipoRendaFixa.CDB, Indexador.CDI, 102, 720,
                    "Banco AAA", risco_credito=2, liquidez_diaria=True)
    a_fragil = analisar_titulo(frageis, MACRO, valor_aplicado=50_000)
    a_solido = analisar_titulo(solido, MACRO, valor_aplicado=50_000)
    assert a_fragil.dentro_do_fgc
    assert a_fragil.score < a_solido.score
    assert any("FGC cobre" in x and "semanas a meses" in x
               for x in a_fragil.alertas)


def test_acima_do_limite_do_fgc_perde_cobertura():
    t = Titulo("cdb", TipoRendaFixa.CDB, Indexador.CDI, 120, 720,
               risco_credito=5)
    a = analisar_titulo(t, MACRO, valor_aplicado=LIMITE_FGC + 150_000)
    assert not a.dentro_do_fgc
    assert any("excede o limite do FGC" in x for x in a.alertas)
    assert any("SEM" in x and "FGC" in x for x in a.alertas)


def test_duration_longa_com_marcacao_gera_alerta():
    t = Titulo("ipca longo", TipoRendaFixa.TESOURO, Indexador.IPCA, 6.0,
               4015, marcacao_a_mercado=True)
    a = analisar_titulo(t, MACRO)
    assert a.duration_anos > 5
    assert any("marcação a mercado" in x for x in a.alertas)


def test_prefixado_longo_alerta_sobre_inflacao():
    t = Titulo("pre", TipoRendaFixa.TESOURO, Indexador.PRE, 11.8, 1460)
    assert any("inflação surpreender" in x
               for x in analisar_titulo(t, MACRO).alertas)


def test_ranking_nao_segue_a_taxa_nominal():
    titulos = [
        Titulo("Tesouro Selic", TipoRendaFixa.TESOURO, Indexador.SELIC, 0.05,
               1800, "Tesouro", 1, True),
        Titulo("CDB frágil 128%", TipoRendaFixa.CDB, Indexador.CDI, 128, 1080,
               "Banco pequeno", 5, False),
        Titulo("CDB AAA 102%", TipoRendaFixa.CDB, Indexador.CDI, 102, 720,
               "Banco AAA", 2, True),
    ]
    r = comparar_titulos(titulos, MACRO, valor_aplicado=50_000)
    nomes = [t["titulo"]["nome"] for t in r["titulos"]]
    assert r["disponivel"]
    # O de maior taxa nominal não é o primeiro.
    assert nomes[0] != "CDB frágil 128%"
    assert "RETORNO REAL LÍQUIDO" in r["observacao"]


# =============================================================== ETFs
ETF_BOM = {"nome": "iShares Ibovespa", "benchmark": "Ibovespa",
           "taxa_administracao": 0.30, "tracking_error": 0.35,
           "liquidez_diaria": 6.5e8, "n_ativos": 86, "peso_top5": 38.0}


def test_pesos_de_etf_somam_um():
    assert sum(PESOS_ETF.values()) == pytest.approx(1.0)


def test_etf_sem_dados_e_indisponivel():
    a = analisar_etf("BOVA11", None)
    assert not a.disponivel
    assert FONTE_NAO_CONFIGURADA in a.mensagem


def test_etf_eficiente_pontua_alto():
    a = analisar_etf("BOVA11", ETF_BOM)
    assert a.score > 75
    assert a.classificacao == "eficiente"
    assert not a.alertas


def test_taxa_alta_em_produto_indexado_e_penalizada():
    caro = analisar_etf("CARO11", {**ETF_BOM, "taxa_administracao": 1.20,
                                   "liquidez_diaria": 3e6})
    assert caro.score < analisar_etf("BOVA11", ETF_BOM).score
    assert any("alta para um produto indexado" in x for x in caro.alertas)


def test_tracking_error_alto_significa_produto_que_nao_cumpre_a_funcao():
    a = analisar_etf("RUIM11", {**ETF_BOM, "tracking_error": 2.80})
    assert any("não cumpre sua função" in x for x in a.alertas)


def test_contagem_de_ativos_nao_mede_diversificacao():
    a = analisar_etf("CONC11", {**ETF_BOM, "n_ativos": 62, "peso_top5": 68.0})
    assert any("não significa diversificação" in x for x in a.alertas)
    assert a.hhi_estimado is not None


def test_sem_peso_top5_avisa_que_contagem_nao_basta():
    dados = {k: v for k, v in ETF_BOM.items() if k != "peso_top5"}
    a = analisar_etf("X11", dados)
    assert any("contagem de ativos não mede" in x for x in a.alertas)


def test_liquidez_baixa_pode_custar_mais_que_a_taxa():
    a = analisar_etf("ILIQ11", {**ETF_BOM, "liquidez_diaria": 180_000})
    assert any("spread" in x for x in a.alertas)


def test_comparacao_de_etfs_ordena_disponiveis_primeiro():
    r = comparar_etfs([("BOVA11", ETF_BOM), ("SEM11", None)])
    assert r["disponiveis"] == 1
    assert r["etfs"][0]["ticker"] == "BOVA11"
    assert not r["etfs"][-1]["disponivel"]


# ====================================================== COMPARADOR
def _cands():
    return [
        CandidatoComparacao(
            "CDB frágil", AssetClass.RENDA_FIXA, retorno_nominal_aa=13.44,
            incerteza_retorno=0.5, volatilidade_aa=0.5,
            drawdown_plausivel_pct=2.0, aliquota_ir=0.15, liquidez_dias=1080,
            horizonte_minimo_meses=36, risco_credito=5, garantia="FGC"),
        CandidatoComparacao(
            "CDB AAA", AssetClass.RENDA_FIXA, retorno_nominal_aa=10.71,
            incerteza_retorno=0.3, volatilidade_aa=0.4,
            drawdown_plausivel_pct=1.0, aliquota_ir=0.175, liquidez_dias=720,
            horizonte_minimo_meses=24, risco_credito=2, garantia="FGC"),
        CandidatoComparacao(
            "Tesouro Selic", AssetClass.RENDA_FIXA, retorno_nominal_aa=10.55,
            incerteza_retorno=0.3, volatilidade_aa=0.8,
            drawdown_plausivel_pct=1.0, aliquota_ir=0.15, liquidez_dias=1,
            horizonte_minimo_meses=1, risco_credito=1, garantia="soberano"),
        CandidatoComparacao(
            "FII", AssetClass.FII, retorno_nominal_aa=12.5,
            incerteza_retorno=8.0, volatilidade_aa=14.0,
            drawdown_plausivel_pct=25.0, isento_ir=True, liquidez_dias=2,
            horizonte_minimo_meses=36),
        CandidatoComparacao(
            "BTC", AssetClass.CRIPTO, retorno_nominal_aa=25.0,
            incerteza_retorno=60.0, volatilidade_aa=65.0,
            drawdown_plausivel_pct=75.0, aliquota_ir=0.15, liquidez_dias=1,
            horizonte_minimo_meses=60),
        CandidatoComparacao("Sem dados", AssetClass.ACAO),
    ]


def test_maior_retorno_nominal_nao_ganha():
    r = comparar(_cands(), inflacao_aa=4.2,
                 perfil=PerfilInvestidor(horizonte_meses=120,
                                         tolerancia_drawdown_pct=90.0,
                                         necessidade_liquidez_dias=3000))
    assert "não lidera" in r.conclusao
    assert "rentabilidade nominal engana" in r.conclusao


def test_risco_de_credito_entra_no_denominador():
    """Volatilidade sozinha colocaria emissor frágil no topo."""
    r = comparar(_cands(), inflacao_aa=4.2,
                 perfil=PerfilInvestidor(horizonte_meses=120,
                                         tolerancia_drawdown_pct=90.0,
                                         necessidade_liquidez_dias=3000))
    fragil = next(l for l in r.linhas
                  if l.candidato.identificador == "CDB frágil")
    aaa = next(l for l in r.linhas if l.candidato.identificador == "CDB AAA")
    assert fragil.risco_total_aa > fragil.candidato.volatilidade_aa * 5
    assert aaa.retorno_por_risco > fragil.retorno_por_risco
    assert any("não aparece na volatilidade" in o
               for o in fragil.candidato.observacoes)


def test_garantia_soberana_reduz_risco_de_credito():
    r = comparar(_cands(), inflacao_aa=4.2)
    selic = next(l for l in r.linhas
                 if l.candidato.identificador == "Tesouro Selic")
    assert selic.risco_total_aa < 1.5


def test_piso_pessimista_revela_a_cauda():
    r = comparar(_cands(), inflacao_aa=4.2)
    btc = next(l for l in r.linhas if l.candidato.identificador == "BTC")
    assert btc.retorno_real_liquido_aa > 0
    assert btc.retorno_pessimista_aa < -30


def test_candidato_sem_retorno_e_incomparavel():
    r = comparar(_cands(), inflacao_aa=4.2)
    sem = next(l for l in r.linhas
               if l.candidato.identificador == "Sem dados")
    assert not sem.comparavel
    assert FONTE_NAO_CONFIGURADA in sem.motivo_incomparavel


def test_sem_inflacao_avisa_que_a_comparacao_e_nominal():
    r = comparar(_cands(), inflacao_aa=None)
    assert any("retorno real" in a for a in r.avisos)
    assert r.linhas[0].retorno_real_liquido_aa is None


def test_perfil_conservador_exclui_ativos_por_drawdown_e_prazo():
    r = comparar(_cands(), inflacao_aa=4.2, perfil=PerfilInvestidor(
        horizonte_meses=36, tolerancia_drawdown_pct=15.0,
        necessidade_liquidez_dias=30, objetivo="renda"))
    excluidos = {x["identificador"] for x in r.incompativeis}
    assert "BTC" in excluidos
    assert "CDB frágil" in excluidos       # 1080 dias de liquidez
    assert "excluídos por incompatibilidade" in r.conclusao


def test_objetivo_renda_muda_a_orientacao():
    r = comparar(_cands(), inflacao_aa=4.2, perfil=PerfilInvestidor(
        objetivo="renda", tolerancia_drawdown_pct=90.0,
        necessidade_liquidez_dias=3000, horizonte_meses=120))
    assert "previsibilidade de fluxo" in r.conclusao


def test_objetivo_preservacao_muda_a_orientacao():
    r = comparar(_cands(), inflacao_aa=4.2, perfil=PerfilInvestidor(
        objetivo="preservacao", tolerancia_drawdown_pct=90.0,
        necessidade_liquidez_dias=3000, horizonte_meses=120))
    assert "preservação" in r.conclusao


def test_nenhum_candidato_compativel_diz_que_a_decisao_e_do_usuario():
    r = comparar(_cands(), inflacao_aa=4.2, perfil=PerfilInvestidor(
        horizonte_meses=1, tolerancia_drawdown_pct=0.5,
        necessidade_liquidez_dias=0))
    assert "decisões suas, não do sistema" in r.conclusao


def test_conclusao_sempre_lembra_que_nao_ha_garantia():
    r = comparar(_cands(), inflacao_aa=4.2)
    assert "não é garantia" in r.conclusao or "garantia" in r.conclusao
