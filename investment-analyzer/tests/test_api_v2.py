"""API das camadas novas: ciclo, validação, portfólio, multiativos, operação.

O foco destes testes não é "o endpoint responde 200". É o contrato que o
painel depende para não mentir:

- toda combinação sem fonte declara FONTE NÃO CONFIGURADA;
- nenhum endpoint devolve score onde não há dado;
- endpoint que muda estado exige token;
- decisão de risco sempre traz o motivo, inclusive quando é NÃO OPERAR.
"""
import pytest
from fastapi.testclient import TestClient

from investai.api import AppState, criar_app
from investai.config import Settings
from investai.data.quality import FONTE_NAO_CONFIGURADA
from investai.exchanges import SyntheticProvider

TOKEN = "token-de-teste-v2"


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    origem = Path(__file__).resolve().parent.parent / "data" / "fiis_snapshot.json"
    shutil.copy(origem, tmp_path / "fiis_snapshot.json")

    monkeypatch.setenv("INVESTAI_API_TOKEN", TOKEN)
    monkeypatch.setenv("INVESTAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INVESTAI_SYNTHETIC", "1")
    s = Settings.from_env()
    s.universo = ("BTCUSDT", "ETHUSDT")
    estado = AppState(settings=s, usar_sintetico=True)
    estado.provider = SyntheticProvider(seed=11, agora_ms=1_726_000_000_000)
    estado.hub.provider = estado.provider
    with TestClient(criar_app(estado)) as c:
        yield c


def auth():
    return {"X-API-Token": TOKEN}


# ============================================================ autenticação
@pytest.mark.parametrize("caminho,corpo", [
    ("/api/estatistica", {"symbol": "BTCUSDT", "side": "long",
                          "retornos_r": [1.0]}),
    ("/api/validacao/estrategia", {}),
    ("/api/risco/retomar", {"confirmacao": "RETOMAR OPERACAO"}),
    ("/api/risco/halt", {}),
])
def test_mutacoes_novas_exigem_token(cliente, caminho, corpo):
    assert cliente.post(caminho, json=corpo).status_code == 401


def test_leituras_novas_sao_publicas(cliente):
    """Ler diagnóstico não exige token; mudar estado exige."""
    for caminho in ("/api/dados/cobertura", "/api/saude", "/api/estrategias",
                    "/api/estrategias/criterios", "/api/journal",
                    "/api/alertas", "/api/risco", "/api/portfolio"):
        assert cliente.get(caminho).status_code == 200, caminho


# ================================================================== ciclo
def test_ciclo_traz_decisao_com_motivo_para_todos(cliente):
    d = cliente.get("/api/ciclo?symbols=BTCUSDT,ETHUSDT").json()
    assert d["total_analisado"] == 2
    assert (d["total_operavel"] + d["total_em_observacao"]
            + d["total_rejeitado"]) == 2
    for a in d["analises"]:
        # Sem motivo, o painel não consegue explicar a decisão a ninguém.
        assert a["motivo"], a["symbol"]
        assert a["decisao"]
        assert a["etapa_final"]
        assert a["categoria_motivo"]


def test_ciclo_sem_oportunidade_diz_capital_preservado(cliente):
    d = cliente.get("/api/ciclo?symbols=BTCUSDT,ETHUSDT").json()
    if d["total_operavel"] == 0:
        assert "CAPITAL PRESERVADO" in d["resumo"]
    else:
        assert "aprovado" in d["resumo"]


def test_ciclo_declara_lacunas_de_dados(cliente):
    d = cliente.get("/api/ciclo?symbols=BTCUSDT").json()
    assert isinstance(d["lacunas"], list)
    assert d["saude"]["estado_geral"]


def test_ciclo_traz_aviso_de_risco(cliente):
    assert "acerto garantido" in cliente.get("/api/ciclo?symbols=BTCUSDT"
                                             ).json()["aviso"]


def test_analise_completa_expõe_as_etapas(cliente):
    a = cliente.get("/api/analise-completa/BTCUSDT").json()["analise"]
    assert a["symbol"] == "BTCUSDT"
    assert a["canonical"]
    assert a["qualidade"] is not None
    assert a["regime"] is not None


def test_analise_completa_de_simbolo_inexistente_e_404(cliente):
    assert cliente.get("/api/analise-completa/NAOEXISTE").status_code == 404


def test_consenso_distingue_sem_dados_de_neutro(cliente):
    """Agente sem dado precisa aparecer como SEM_DADOS, não como neutro."""
    a = cliente.get("/api/analise-completa/BTCUSDT").json()["analise"]
    if a["consenso"] is None:
        pytest.skip("análise parou antes do consenso")
    pareceres = a["consenso"]["pareceres"]
    assert pareceres
    posturas = {p["postura"] for p in pareceres}
    for p in pareceres:
        # Abstenção tem que ser distinguível de parecer neutro: quem não
        # entra no cálculo não pode ter valor numérico.
        if not p["entra_no_calculo"]:
            assert p["valor"] is None, p["agente"]
            assert p["postura"] in ("sem_dados", "nao_aplicavel"), p["agente"]
        else:
            assert p["valor"] is not None
            assert -1.0 <= p["valor"] <= 1.0
    # O peso dos ausentes é renormalizado, então cobertura < 100% precisa
    # aparecer declarada em vez de virar "tudo analisado".
    assert 0.0 <= a["consenso"]["cobertura"] <= 1.0
    if posturas & {"sem_dados", "nao_aplicavel"}:
        assert a["consenso"]["cobertura"] < 1.0


# ============================================================== estatística
def test_estatistica_alimenta_ev(cliente):
    r = cliente.post("/api/estatistica", headers=auth(), json={
        "symbol": "BTCUSDT", "side": "long",
        "retornos_r": [2.0, -1.0, 1.5, -1.0, 3.0, -1.0, 1.0, -1.0,
                       2.5, -1.0, 1.2, -1.0]})
    assert r.status_code == 200
    ev = r.json()["ev"]
    assert ev["n"] == 12
    assert "ev_liquido_r" in ev
    # Amostra de 12 não sustenta decisão, e o próprio payload tem que dizê-lo.
    assert ev["amostra_suficiente"] is False
    assert ev["ic_win_rate"]["informativo"] is False
    assert ev["motivos"]


def test_estatistica_lista_vazia_recusada(cliente):
    r = cliente.post("/api/estatistica", headers=auth(), json={
        "symbol": "BTCUSDT", "side": "long", "retornos_r": []})
    assert r.status_code == 422


# ============================================================== cobertura
def test_cobertura_declara_o_que_falta(cliente):
    d = cliente.get("/api/dados/cobertura").json()
    assert d["resumo"]["combinacoes_sem_fonte"] > 0
    assert d["resumo"]["exemplos_sem_fonte"]
    assert FONTE_NAO_CONFIGURADA in d["observacao"] or \
        "FONTE NÃO CONFIGURADA" in d["observacao"]


def test_matriz_cobertura_nunca_omite_celula(cliente):
    matriz = cliente.get("/api/dados/cobertura").json()["matriz"]
    for classe, tipos in matriz.items():
        for tipo, cov in tipos.items():
            assert "disponivel" in cov, f"{classe}/{tipo}"
            if not cov["disponivel"]:
                assert FONTE_NAO_CONFIGURADA in cov["mensagem"]


def test_provedor_nao_configurado_traz_instrucao(cliente):
    provedores = cliente.get("/api/dados/cobertura").json()["provedores"]
    for p in provedores:
        if p["estado"] == "nao_configurado":
            assert p["instrucao_configuracao"], p["nome"]


# ================================================================= saúde
def test_saude_separa_abrir_de_gerenciar(cliente):
    d = cliente.get("/api/saude").json()
    assert d["estado_geral"] in ("HEALTHY", "DEGRADED", "OFFLINE")
    assert isinstance(d["pode_abrir_posicao"], bool)
    assert isinstance(d["pode_gerenciar_posicao"], bool)
    assert d["componentes"]


def test_saude_marca_noticias_offline(cliente):
    """Notícias não estão conectadas: tem que aparecer como falha, não ok."""
    comps = {c["nome"]: c
             for c in cliente.get("/api/saude").json()["componentes"]}
    assert comps["feed_de_noticias"]["estado"] != "HEALTHY"
    # Fonte não conectada não pode derrubar o sistema: degrada, não bloqueia.
    assert comps["feed_de_noticias"]["critico"] is False


# =============================================== regime e anomalias
def test_regime_classifica_com_evidencia(cliente):
    d = cliente.get("/api/regime/BTCUSDT").json()
    assert d["regime"]
    assert d["confianca"] >= 0
    assert d["evidencias"]


def test_regime_recusa_historico_curto(cliente):
    r = cliente.get("/api/regime/NAOEXISTE")
    assert r.status_code in (404, 422)


def test_anomalias_responde_sempre_com_lista(cliente):
    d = cliente.get("/api/anomalias/BTCUSDT").json()
    assert d["symbol"] == "BTCUSDT"
    assert isinstance(d["anomalias"], list)
    assert "bloqueia_entrada" in d
    assert "não afirma direção" in d["observacao"]


# ============================================================== validação
def test_monte_carlo_mostra_risco_de_ruina(cliente):
    r = cliente.post("/api/validacao/monte-carlo", json={
        "retornos_r": [2.0, -1.0, 1.5, -1.0, 3.0, -1.0] * 8,
        "n_simulacoes": 500, "risco_por_trade_frac": 0.01})
    assert r.status_code == 200
    d = r.json()
    assert d["n_simulacoes"] == 500
    assert 0.0 <= d["prob_ruina"] <= 1.0
    assert 0.0 <= d["prob_prejuizo"] <= 1.0
    assert d["modo"] == "blocos"
    assert d["drawdown_p95"] >= 0.0


def test_monte_carlo_alta_alavancagem_mostra_ruina_alta(cliente):
    """O mesmo conjunto de trades a 10% por trade tem que assustar."""
    trades = [2.0, -1.0, 1.5, -1.0, 3.0, -1.0] * 8
    baixo = cliente.post("/api/validacao/monte-carlo", json={
        "retornos_r": trades, "n_simulacoes": 800,
        "risco_por_trade_frac": 0.005}).json()
    alto = cliente.post("/api/validacao/monte-carlo", json={
        "retornos_r": trades, "n_simulacoes": 800,
        "risco_por_trade_frac": 0.10}).json()
    assert alto["drawdown_p95"] > baixo["drawdown_p95"]
    assert alto["prob_ruina"] >= baixo["prob_ruina"]


def test_comparar_modos_expõe_o_custo_de_assumir_independencia(cliente):
    r = cliente.post("/api/validacao/comparar-modos", json={
        "retornos_r": [1.5, -1.0, -1.0, -1.0, 2.0, 2.5] * 8,
        "n_simulacoes": 600})
    assert r.status_code == 200
    d = r.json()
    assert "iid" in d and "blocos" in d
    # Blocos preservam o agrupamento das perdas; IID o destrói. O relatório
    # precisa mostrar a diferença, não escolher a estimativa mais bonita.
    assert d["agrupamento"]
    assert d["blocos"]["drawdown_p95"] >= d["iid"]["drawdown_p95"] * 0.5


def test_monte_carlo_modo_invalido_recusado(cliente):
    r = cliente.post("/api/validacao/monte-carlo", json={
        "retornos_r": [1.0], "modo": "magico"})
    assert r.status_code == 422


def test_validacao_estrategia_roda_pipeline_completo(cliente):
    r = cliente.post("/api/validacao/estrategia", headers=auth(), json={
        "symbol": "BTCUSDT", "timeframe": "1H", "barras": 6000,
        "n_ciclos": 3})
    assert r.status_code == 200
    d = r.json()
    assert d["chave_estrategia"]
    assert d["fase_final"]
    assert isinstance(d["promovida"], bool)
    # Promoção sem paper trading real é proibida pelo desenho.
    assert d["fase_final"] not in ("shadow", "assistido", "real_limitado")


def test_validacao_registra_evento_auditavel(cliente):
    cliente.post("/api/validacao/estrategia", headers=auth(),
                 json={"barras": 4000, "n_ciclos": 2})
    eventos = cliente.get("/api/eventos?limite=50").json()["eventos"]
    assert any(e["origem"] == "validacao" for e in eventos)


def test_validacao_barras_abaixo_do_minimo_recusada(cliente):
    r = cliente.post("/api/validacao/estrategia", headers=auth(),
                     json={"barras": 200})
    assert r.status_code == 422


# ============================================================ estratégias
def test_estrategias_listadas_com_fase(cliente):
    d = cliente.get("/api/estrategias").json()
    assert "por_fase" in d
    assert d["ordem_das_fases"][0] == "rascunho"
    assert d["ordem_das_fases"][-1] == "real_limitado"


def test_nenhuma_estrategia_nasce_operavel_em_real(cliente):
    d = cliente.get("/api/estrategias").json()
    assert d["operaveis_em_real"] == []


def test_criterios_dizem_que_backtest_nao_vence_paper(cliente):
    d = cliente.get("/api/estrategias/criterios").json()
    assert "NÃO podem ser vencidas por backtest" in d["observacao"]
    crit = d["criterios"]
    assert crit["backtest"]["min_trades"] >= 30
    assert crit["out_of_sample"]["exige_overfit_aprovado"] is True
    assert crit["out_of_sample"]["exige_ic_positivo"] is True
    # Paper trading exige DIAS de calendário: não se atalha rodando histórico.
    assert crit["paper_trading"]["min_dias"] >= 14
    # 7 fases do pipeline de promoção + as fases terminais.
    assert len(d["fases"]) >= 7
    assert set(d["criterios"]) >= {"backtest", "out_of_sample",
                                   "paper_trading", "shadow"}


# ============================================================== portfólio
def test_portfolio_sem_posicao_nao_inventa_correlacao(cliente):
    d = cliente.get("/api/portfolio").json()
    assert d["n_posicoes"] == 0
    assert "correlacao" not in d
    assert d["cenarios_disponiveis"]


def test_stress_sem_posicao_e_recusado(cliente):
    r = cliente.post("/api/portfolio/stress", json={"capital": 10000})
    assert r.status_code == 422


def test_portfolio_com_posicao_calcula_diversificacao(cliente):
    cliente.post("/api/motor/ciclo", headers=auth())
    d = cliente.get("/api/portfolio").json()
    if d["n_posicoes"] == 0:
        pytest.skip("o ciclo não abriu posição — comportamento válido")
    assert d["diversificacao"]["n_posicoes"] >= 1
    assert "correlacao" in d


# ============================================================ multiativos
def test_acao_sem_fundamentos_nao_recebe_score(cliente):
    r = cliente.post("/api/ativos/acao", json={"ticker": "PETR4"})
    assert r.status_code == 200
    a = r.json()["analise"]
    assert a["disponivel"] is False
    assert a["score"] == 0
    assert FONTE_NAO_CONFIGURADA in a["mensagem"]
    assert a["dados_faltando"]


def test_acao_declara_cobertura_ausente(cliente):
    cov = cliente.post("/api/ativos/acao",
                       json={"ticker": "PETR4"}).json()["cobertura"]
    assert cov["disponivel"] is False
    assert cov["instrucao"]


def test_acao_com_fundamentos_recebe_analise(cliente):
    r = cliente.post("/api/ativos/acao", json={"ticker": "WEGE3", "fundamentos": {
        "roe": 28.0, "roic": 22.0, "margem_liquida": 14.0,
        "margem_operacional": 18.0, "crescimento_receita_3a": 15.0,
        "crescimento_lucro_3a": 18.0, "divida_liquida_ebitda": 0.3,
        "liquidez_corrente": 2.1, "fcf_yield": 4.0,
        "payout": 45.0, "pl": 28.0, "pvp": 8.0}})
    a = r.json()["analise"]
    assert a["disponivel"] is True
    assert 0 < a["score"] <= 100
    assert a["cobertura"] > 0.5
    assert a["fatores"]


def test_etf_sem_dados_nao_inventa(cliente):
    a = cliente.post("/api/ativos/etf",
                     json={"ticker": "IVVB11"}).json()["analise"]
    assert a["disponivel"] is False
    assert FONTE_NAO_CONFIGURADA in a["mensagem"]


def test_renda_fixa_ordena_por_retorno_real_liquido(cliente):
    r = cliente.post("/api/ativos/renda-fixa", json={
        "cdi_aa": 10.5, "selic_aa": 10.75, "ipca_aa": 4.5,
        "titulos": [
            {"nome": "CDB banco grande 105% CDI", "tipo": "cdb",
             "indexador": "pos_cdi", "taxa": 105.0, "prazo_dias": 720,
             "emissor": "Banco Grande", "risco_credito": 1,
             "liquidez_diaria": True},
            {"nome": "CDB banco frágil 135% CDI", "tipo": "cdb",
             "indexador": "pos_cdi", "taxa": 135.0, "prazo_dias": 1080,
             "emissor": "Banco Fragil", "risco_credito": 5},
            {"nome": "LCI isenta 95% CDI", "tipo": "lci",
             "indexador": "pos_cdi", "taxa": 95.0, "prazo_dias": 540,
             "emissor": "Banco Medio", "risco_credito": 2},
        ]})
    assert r.status_code == 200
    d = r.json()
    assert d["disponivel"] is True
    scores = [t["score"] for t in d["titulos"]]
    assert scores == sorted(scores, reverse=True)
    assert "não a taxa nominal" in d["observacao"]


def test_emissor_fragil_sempre_recebe_alerta(cliente):
    d = cliente.post("/api/ativos/renda-fixa", json={
        "cdi_aa": 10.5, "ipca_aa": 4.5,
        "titulos": [{"nome": "CDB 140% CDI", "tipo": "cdb",
                     "indexador": "pos_cdi", "taxa": 140.0, "prazo_dias": 1440,
                     "emissor": "Banco X", "risco_credito": 5}]}).json()
    t = d["titulos"][0]
    assert t["alertas"], "risco de crédito 5 sem alerta é perigoso"
    assert any("crédito" in a.lower() or "risco" in a.lower()
               for a in t["alertas"])


def test_renda_fixa_sem_macro_nao_estima(cliente):
    d = cliente.post("/api/ativos/renda-fixa", json={
        "titulos": [{"nome": "CDB 110% CDI", "tipo": "cdb",
                     "indexador": "pos_cdi", "taxa": 110.0,
                     "prazo_dias": 720}]}).json()
    assert d["disponivel"] is False
    assert FONTE_NAO_CONFIGURADA in d["mensagem"]


def test_indexador_invalido_recusado(cliente):
    r = cliente.post("/api/ativos/renda-fixa", json={
        "cdi_aa": 10.5, "titulos": [
            {"nome": "x", "tipo": "cdb", "indexador": "cdi",
             "taxa": 1.0, "prazo_dias": 30}]})
    assert r.status_code == 422


def test_comparar_classes_respeita_perfil(cliente):
    r = cliente.post("/api/ativos/comparar", json={
        "inflacao_aa": 4.5, "horizonte_meses": 12,
        "tolerancia_drawdown_pct": 5.0,
        "necessidade_liquidez_dias": 5, "objetivo": "renda",
        "candidatos": [
            {"identificador": "Tesouro Selic", "classe": "renda_fixa",
             "retorno_nominal_aa": 10.5, "volatilidade_aa": 0.5,
             "drawdown_plausivel_pct": 1.0, "aliquota_ir": 0.15,
             "liquidez_dias": 1, "risco_credito": 1,
             "garantia": "soberano"},
            {"identificador": "Cripto alavancado", "classe": "cripto",
             "retorno_nominal_aa": 80.0, "incerteza_retorno": 60.0,
             "volatilidade_aa": 90.0, "drawdown_plausivel_pct": 70.0,
             "aliquota_ir": 0.15, "liquidez_dias": 1,
             "horizonte_minimo_meses": 48},
        ]})
    assert r.status_code == 200
    d = r.json()
    # A ordenação é por retorno real líquido ajustado a risco, e o candidato
    # de 70% de drawdown com horizonte de 48 meses é INCOMPATÍVEL com um
    # perfil de 12 meses e tolerância de 5% — precisa ser barrado, não
    # apenas ranqueado abaixo.
    ident = [x["candidato"]["identificador"] for x in d["linhas"]]
    assert ident[0] == "Tesouro Selic", ident
    incompat = " ".join(str(i) for i in d["incompativeis"])
    assert "Cripto alavancado" in incompat, d["incompativeis"]
    assert d["conclusao"]
    assert "nunca por" in d["observacao"]


def test_aliquota_em_percentual_e_recusada(cliente):
    """15.0 no lugar de 0.15 daria retorno negativo em silêncio."""
    r = cliente.post("/api/ativos/comparar", json={
        "candidatos": [{"identificador": "x", "classe": "renda_fixa",
                        "retorno_nominal_aa": 10.5, "aliquota_ir": 15.0}]})
    assert r.status_code == 422


def test_classe_invalida_recusada(cliente):
    r = cliente.post("/api/ativos/comparar", json={
        "candidatos": [{"identificador": "x", "classe": "nft"}]})
    assert r.status_code == 422


# ========================================================= journal/alertas
def test_journal_resume_por_quadrante(cliente):
    d = cliente.get("/api/journal").json()
    assert "entradas" in d
    assert "quadrantes" in d["resumo"] or "contagem" in d["resumo"] \
        or d["resumo"]


def test_alertas_filtra_por_nivel(cliente):
    cliente.get("/api/ciclo?symbols=BTCUSDT")
    d = cliente.get("/api/alertas?nivel=urgente").json()
    assert all(a["nivel"] == "urgente" for a in d["alertas"])
    assert "por_nivel" in d["resumo"]


def test_alerta_nivel_invalido_da_erro_tratado(cliente):
    r = cliente.get("/api/alertas?nivel=apocalipse")
    assert r.status_code in (400, 422)


# =============================================================== risco
def test_risco_lista_proibicoes_estruturais(cliente):
    d = cliente.get("/api/risco").json()
    assert d["halted"] is False
    assert len(d["proibicoes_estruturais"]) >= 6
    assert any("martingale" in p for p in d["proibicoes_estruturais"])
    assert d["confirmacao_para_retomar"] == "RETOMAR OPERACAO"


def test_halt_e_retomada_exigem_frase_exata(cliente):
    assert cliente.post("/api/risco/halt", headers=auth()).status_code == 200
    assert cliente.get("/api/risco").json()["halted"] is True

    r = cliente.post("/api/risco/retomar", headers=auth(),
                     json={"confirmacao": "pode voltar"})
    assert r.status_code == 400
    assert cliente.get("/api/risco").json()["halted"] is True

    r = cliente.post("/api/risco/retomar", headers=auth(),
                     json={"confirmacao": "RETOMAR OPERACAO"})
    assert r.status_code == 200
    assert cliente.get("/api/risco").json()["halted"] is False


def test_halt_bloqueia_novas_entradas_no_ciclo(cliente):
    cliente.post("/api/risco/halt", headers=auth())
    d = cliente.get("/api/ciclo?symbols=BTCUSDT,ETHUSDT").json()
    assert d["total_operavel"] == 0
    assert all(not a["operavel"] for a in d["analises"])


# ========================================================== liquidação
def test_liquidacao_aprova_alavancagem_conservadora(cliente):
    d = cliente.get("/api/liquidacao?entry=100&stop=97&leverage=3"
                    "&lado=long&atr_pct=1.0").json()
    assert d["preco_liquidacao"] < 97
    assert d["aprovado"] is True
    assert d["folga"] > 0.35
    assert d["liquidacao_em_atrs"] >= 4.0


def test_liquidacao_reprova_quando_liquidacao_vem_antes_do_stop(cliente):
    d = cliente.get("/api/liquidacao?entry=100&stop=90&leverage=20"
                    "&lado=long&atr_pct=2.0").json()
    assert d["aprovado"] is False
    assert d["motivo"]
    # A liquidação tem que estar mais perto da entrada do que o stop.
    assert d["dist_liquidacao_pct"] < d["dist_stop_pct"]
    assert "ANTES do stop" in d["motivo"]
    assert "perda total da margem" in d["motivo"]


def test_liquidacao_short_usa_o_lado_certo(cliente):
    d = cliente.get("/api/liquidacao?entry=100&stop=103&leverage=3"
                    "&lado=short").json()
    assert d["preco_liquidacao"] > 103


def test_liquidacao_alavancagem_invalida_recusada(cliente):
    assert cliente.get("/api/liquidacao?entry=100&stop=97&leverage=0"
                       ).status_code == 422
    assert cliente.get("/api/liquidacao?entry=0&stop=97&leverage=3"
                       ).status_code == 422


# ====================================================== relatório diário
def test_relatorio_diario_tem_texto_legivel(cliente):
    d = cliente.get("/api/relatorio-diario").json()
    assert d["relatorio"]["data"]
    texto = d["texto"]
    assert "RELATÓRIO" in texto.upper()
    assert "sintetico" in texto or "SINTÉTICO" in texto.upper()


def test_relatorio_nunca_promete_lucro(cliente):
    texto = cliente.get("/api/relatorio-diario").json()["texto"].lower()
    for proibida in ("lucro garantido", "risco zero", "infalível",
                     "certeza de valorização", "100% de acerto"):
        assert proibida not in texto
