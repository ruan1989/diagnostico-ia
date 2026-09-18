"""Journal, análise pós-trade, alertas e relatório diário."""
import pytest

from investai.models import Side, Trade
from investai.reporting import (
    Alerta, AlertaInvalido, BlocoDesempenho, CategoriaAlerta,
    CentralDeAlertas, ChecklistProcesso, Journal, NivelAlerta,
    OportunidadeRejeitada, Quadrante, QualidadeProcesso, alerta_drawdown,
    alerta_oportunidade, alerta_risco, montar_relatorio,
)

CHECKLIST_OK = ChecklistProcesso(
    tese_registrada=True, stop_definido_antes_da_entrada=True,
    stop_respeitado=True, tamanho_conforme_risco=True,
    risco_dentro_do_limite=True, dados_confiaveis=True,
    estatistica_suportava=True, regime_compativel=True, risco_aprovou=True,
    sem_aumento_apos_perda=True)


def trade(r):
    return Trade("BTCUSDT", Side.LONG, 64000.0, 64500.0, 0.004, 0, 1,
                 r * 5, r, "alvo_1" if r > 0 else "stop_loss")


@pytest.fixture
def journal():
    return Journal()


def registra(j, **kw):
    base = dict(symbol="BTCUSDT", side=Side.LONG, tese="rompimento com volume",
                entrada=64000.0, stop=62720.0, alvos=[66300.0],
                risco_usd=5.0, risco_pct_capital=0.5, score=74.0,
                decisao="validada_pelo_modelo", estrategia="confluencia",
                versao_estrategia="v1", ev_estimado_r=0.35)
    base.update(kw)
    return j.registrar(**base)


# ========================================= quatro quadrantes do journal
def test_processo_correto_com_lucro(journal):
    e = registra(journal)
    _, a = journal.fechar(e.id, trade(1.8), checklist=CHECKLIST_OK)
    assert a.quadrante is Quadrante.ACERTO_MERECIDO
    assert a.qualidade_processo is QualidadeProcesso.CORRETO
    assert "não aumentar o tamanho" in a.licao


def test_prejuizo_com_processo_correto_e_azar_nao_falha(journal):
    """Perda dentro do plano não invalida a estratégia."""
    e = registra(journal)
    _, a = journal.fechar(e.id, trade(-1.0), checklist=CHECKLIST_OK)
    assert a.quadrante is Quadrante.AZAR
    assert a.qualidade_processo is QualidadeProcesso.CORRETO
    assert "NÃO mudar a estratégia" in a.licao


def test_lucro_com_processo_falho_e_o_caso_mais_perigoso(journal):
    """O resultado positivo reforça o comportamento errado."""
    chk = ChecklistProcesso(**{**CHECKLIST_OK.itens(),
                               "stop_respeitado": False})
    e = registra(journal)
    _, a = journal.fechar(e.id, trade(2.4), checklist=chk)
    assert a.quadrante is Quadrante.SORTE
    assert a.qualidade_processo is QualidadeProcesso.FALHO
    assert "mais perigoso" in a.licao
    assert "o stop não foi respeitado" in a.violacoes


def test_prejuizo_com_processo_falho_aponta_a_execucao(journal):
    chk = ChecklistProcesso(**{**CHECKLIST_OK.itens(),
                               "tamanho_conforme_risco": False,
                               "risco_dentro_do_limite": False,
                               "sem_aumento_apos_perda": False})
    e = registra(journal)
    _, a = journal.fechar(e.id, trade(-3.2), checklist=chk)
    assert a.quadrante is Quadrante.ERRO_COBRADO
    assert "na execução, não no mercado" in a.licao
    assert len(a.violacoes) == 3


def test_checklist_incompleto_e_indeterminado(journal):
    e = registra(journal)
    _, a = journal.fechar(e.id, trade(1.1),
                          checklist=ChecklistProcesso(tese_registrada=True))
    assert a.quadrante is Quadrante.INDETERMINADO
    assert a.qualidade_processo is QualidadeProcesso.INDETERMINADO
    assert a.itens_nao_verificados


def test_violacao_leve_nao_reprova_o_processo(journal):
    """Só itens graves reprovam; os demais viram ressalva."""
    chk = ChecklistProcesso(**{**CHECKLIST_OK.itens(),
                               "regime_compativel": False})
    e = registra(journal)
    _, a = journal.fechar(e.id, trade(1.5), checklist=chk)
    assert a.qualidade_processo is QualidadeProcesso.CORRETO
    assert a.violacoes


def test_erro_de_previsao_compara_com_a_expectativa(journal):
    e = registra(journal, ev_estimado_r=0.35)
    _, a = journal.fechar(e.id, trade(2.0), checklist=CHECKLIST_OK)
    assert "+0.35R" in a.erro_de_previsao
    assert "só a média de muitas operações" in a.erro_de_previsao


def test_sem_expectativa_registrada_nao_ha_erro_de_previsao(journal):
    e = registra(journal, ev_estimado_r=None)
    _, a = journal.fechar(e.id, trade(2.0), checklist=CHECKLIST_OK)
    assert "sem expectativa registrada" in a.erro_de_previsao


def test_resumo_alerta_sobre_sorte(journal):
    chk = ChecklistProcesso(**{**CHECKLIST_OK.itens(),
                               "stop_respeitado": False})
    e = registra(journal)
    journal.fechar(e.id, trade(2.0), checklist=chk)
    r = journal.resumo_por_quadrante()
    assert r["por_quadrante"][Quadrante.SORTE.value] == 1
    assert any("mais perigoso" in a for a in r["alertas"])


def test_resumo_alerta_quando_execucao_e_o_problema(journal):
    chk = ChecklistProcesso(**{**CHECKLIST_OK.itens(),
                               "risco_dentro_do_limite": False})
    for i in range(6):
        e = registra(journal)
        journal.fechar(e.id, trade(-1.0), checklist=chk)
    for i in range(6):
        e = registra(journal)
        journal.fechar(e.id, trade(1.0), checklist=CHECKLIST_OK)
    r = journal.resumo_por_quadrante()
    assert any("na execução, não na estratégia" in a for a in r["alertas"])


def test_resumo_alerta_quando_checklist_nao_e_preenchido(journal):
    for i in range(5):
        e = registra(journal)
        journal.fechar(e.id, trade(1.0), checklist=ChecklistProcesso())
    e = registra(journal)
    journal.fechar(e.id, trade(1.0), checklist=CHECKLIST_OK)
    r = journal.resumo_por_quadrante()
    assert any("não está sendo preenchido" in a for a in r["alertas"])


def test_journal_registra_procedencia_dos_dados(journal):
    e = registra(journal, dados_utilizados=["bitget:ohlcv:1H",
                                            "bitget:funding"])
    assert e.to_dict()["dados_utilizados"] == ["bitget:ohlcv:1H",
                                               "bitget:funding"]


def test_listagem_filtra_abertas_e_por_symbol(journal):
    registra(journal, symbol="BTCUSDT")
    e2 = registra(journal, symbol="ETHUSDT")
    journal.fechar(e2.id, trade(1.0), checklist=CHECKLIST_OK)
    assert len(journal.listar(apenas_abertas=True)) == 1
    assert len(journal.listar(symbol="ETHUSDT")) == 1


def test_export_jsonl_uma_linha_por_entrada(journal):
    for _ in range(3):
        registra(journal)
    assert len(journal.exportar_jsonl().split("\n")) == 3


def test_resumo_explica_que_eixos_sao_independentes(journal):
    e = registra(journal)
    _, a = journal.fechar(e.id, trade(1.0), checklist=CHECKLIST_OK)
    assert "lucro não valida processo falho" in a.to_dict()["observacao"]


# ============================================================ ALERTAS
@pytest.mark.parametrize("titulo,msg", [
    ("COMPRE AGORA BTC", "oportunidade imperdível"),
    ("Sinal com 100% de acerto", "entre já"),
    ("Lucro garantido em ETH", "risco zero"),
    ("Dinheiro fácil", "não perca"),
])
def test_linguagem_promocional_e_recusada(titulo, msg):
    with pytest.raises(AlertaInvalido, match="promocional"):
        Alerta(CategoriaAlerta.OPORTUNIDADE, NivelAlerta.INFO, titulo, msg)


def test_alerta_tecnico_e_aceito():
    a = alerta_oportunidade("BTCUSDT", "compra", 74.0,
                            "validada_pelo_modelo", 0.35, 140)
    assert a.categoria is CategoriaAlerta.OPORTUNIDADE
    assert "não representa probabilidade de lucro" in a.mensagem


def test_alerta_sempre_diz_o_que_o_sistema_fez():
    for a in (alerta_oportunidade("B", "compra", 70.0, "x", 0.2, 50),
              alerta_risco("B", "motivo"),
              alerta_drawdown(11.0, 10.0)):
        assert a.acao_do_sistema


def test_drawdown_no_limite_e_urgente():
    assert alerta_drawdown(11.0, 10.0).nivel is NivelAlerta.URGENTE
    assert alerta_drawdown(5.0, 10.0).nivel is NivelAlerta.ATENCAO


def test_deduplicacao_na_janela():
    c = CentralDeAlertas(janela_dedup_ms=900_000)
    a1 = alerta_drawdown(9.0, 10.0)
    a1.ts = 1_000_000
    a2 = alerta_drawdown(9.0, 10.0)
    a2.ts = 1_000_000 + 60_000
    assert c.emitir(a1)
    assert not c.emitir(a2)


def test_alerta_repetido_fora_da_janela_passa():
    c = CentralDeAlertas(janela_dedup_ms=60_000)
    a1 = alerta_drawdown(9.0, 10.0)
    a1.ts = 1_000_000
    a2 = alerta_drawdown(9.0, 10.0)
    a2.ts = 1_000_000 + 120_000
    assert c.emitir(a1)
    assert c.emitir(a2)


def test_central_limita_o_historico():
    c = CentralDeAlertas(janela_dedup_ms=0, limite=5)
    for i in range(20):
        a = alerta_risco(f"SYM{i}", "motivo")
        a.ts = 1_000_000 + i * 1000
        c.emitir(a)
    assert len(c.listar(limite=100)) == 5


def test_filtros_da_central():
    c = CentralDeAlertas(janela_dedup_ms=0)
    c.emitir(alerta_oportunidade("B", "compra", 70.0, "x", 0.2, 50))
    c.emitir(alerta_drawdown(12.0, 10.0))
    assert len(c.listar(categoria=CategoriaAlerta.DRAWDOWN)) == 1
    assert len(c.listar(nivel=NivelAlerta.URGENTE)) == 1


def test_resumo_da_central():
    c = CentralDeAlertas(janela_dedup_ms=0)
    c.emitir(alerta_drawdown(12.0, 10.0))
    r = c.resumo()
    assert r["total"] == 1
    assert r["urgentes_abertos"]


# =================================================== RELATÓRIO DIÁRIO
def test_nenhuma_oportunidade_diz_capital_preservado():
    r = montar_relatorio(pares_analisados=20)
    assert "CAPITAL" in r.resumo_executivo and "PRESERVADO" in r.resumo_executivo


def test_relatorio_inclui_rejeitadas_com_motivos():
    """A informação completa é quantos foram rejeitados e por quê."""
    r = montar_relatorio(
        pares_analisados=20,
        operaveis=[{"symbol": "ADAUSDT"}],
        rejeitadas=[
            OportunidadeRejeitada("A", "compra", 60.0, "nao_operar", "m",
                                  "score_insuficiente"),
            OportunidadeRejeitada("B", "compra", 58.0, "nao_operar", "m",
                                  "score_insuficiente"),
            OportunidadeRejeitada("C", "venda", 78.0, "rejeitada_pelo_risco",
                                  "m", "veto_de_risco"),
        ])
    motivos = r.motivos_de_rejeicao()
    assert motivos["score_insuficiente"] == 2
    assert motivos["veto_de_risco"] == 1
    assert "rejeitado" in r.resumo_executivo


def test_modos_nunca_somados():
    r = montar_relatorio(
        pares_analisados=5,
        desempenho=[BlocoDesempenho("backtest", 37, 0.51, 185.0),
                    BlocoDesempenho("paper", 3, 0.0, -0.49),
                    BlocoDesempenho("live", 0)])
    d = r.to_dict()
    modos = [x["modo"] for x in d["desempenho_por_modo"]]
    assert modos == ["backtest", "paper", "live"]
    assert any("somar resultado simulado" in a for a in d["avisos"])


def test_relatorio_declara_lacunas():
    r = montar_relatorio(
        pares_analisados=5,
        lacunas=["acao/fundamentos: FONTE NÃO CONFIGURADA"])
    assert r.to_dict()["lacunas_declaradas"]
    assert "FONTE NÃO CONFIGURADA" in r.para_texto()


def test_texto_do_relatorio_e_legivel():
    r = montar_relatorio(
        pares_analisados=20, modo_de_dados="sintetico",
        operaveis=[{"symbol": "ADAUSDT", "direcao": "venda", "score": 71.7,
                    "decisao": "validada_pelo_modelo"}],
        risco={"capital_atual": 995.1, "drawdown_pct": 0.49,
               "kill_switch": False},
        portfolio={"n_posicoes": 5, "apostas_efetivas": 1.88,
                   "avisos": ["FALSA DIVERSIFICAÇÃO"]},
        saude={"estado_geral": "HEALTHY", "pode_abrir_posicao": True},
        agora_ms=1789761600000)
    texto = r.para_texto()
    assert "RELATÓRIO DIÁRIO — 2026-09-18" in texto
    assert "ADAUSDT" in texto
    assert "FALSA DIVERSIFICAÇÃO" in texto
    assert "não representa probabilidade de lucro" in texto


def test_relatorio_avisa_que_score_nao_e_probabilidade():
    d = montar_relatorio(pares_analisados=1).to_dict()
    assert any("não representa probabilidade" in a for a in d["avisos"])
