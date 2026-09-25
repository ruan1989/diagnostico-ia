"""Ranking do universo, com entrada e saída automáticas.

Um universo fixo envelhece: um par com liquidez boa há seis meses pode estar
com spread que come a vantagem inteira hoje. O que estes testes protegem, além
disso, é a histerese — sem ela um ativo no limite do critério entra e sai a
cada ciclo, e cada saída descarta a estatística acumulada daquele par.
"""
import pytest

from investai.assets.universo import (
    CICLOS_PARA_READMITIR, CICLOS_PARA_REMOVER, FUNDING_MAXIMO_ABS,
    SPREAD_MAXIMO_PCT, VOLUME_MINIMO_USD, GestorUniverso, LeituraAtivo,
    avaliar,
)


def bom(symbol="BTCUSDT", volume=5e9, spread=0.02, funding=0.0001, **kw):
    return LeituraAtivo(symbol, volume_24h_usd=volume, spread_pct=spread,
                        funding_rate=funding, **kw)


def ruim(symbol="ALTUSDT", **kw):
    return LeituraAtivo(symbol, volume_24h_usd=1e6, spread_pct=0.40,
                        funding_rate=0.0001, **kw)


# =====================================================================
# Pisos absolutos
# =====================================================================
def test_ativo_bom_e_operavel():
    a = avaliar(bom())
    assert a.operavel
    assert a.reprovacoes == []
    assert a.score > 80


def test_volume_baixo_reprova():
    """Com volume baixo o próprio sistema move o preço."""
    a = avaliar(bom(volume=VOLUME_MINIMO_USD * 0.5))
    assert not a.operavel
    assert "move o preço" in a.reprovacoes[0]


def test_volume_no_piso_exato_reprova():
    assert not avaliar(bom(volume=VOLUME_MINIMO_USD * 0.999)).operavel
    assert avaliar(bom(volume=VOLUME_MINIMO_USD * 1.001)).operavel


def test_spread_alto_reprova_com_a_conta_na_mensagem():
    """Vantagem de 0,3R desaparece se entrar e sair custa 0,25R."""
    a = avaliar(bom(spread=SPREAD_MAXIMO_PCT * 2))
    assert not a.operavel
    assert "consome a vantagem" in a.reprovacoes[0]
    assert f"{SPREAD_MAXIMO_PCT * 4:.3f}%" in a.reprovacoes[0]


def test_qualidade_reprovada_derruba():
    a = avaliar(LeituraAtivo("X", volume_24h_usd=5e9, spread_pct=0.02,
                             qualidade_ok=False,
                             motivos_qualidade=["furo de 6 horas na série"]))
    assert not a.operavel
    assert "furo de 6 horas" in a.reprovacoes[0]
    assert a.componentes["qualidade"] == 0.0


def test_funding_extremo_reprova():
    """O carrego consome a expectativa mesmo com a direção certa."""
    a = avaliar(bom(funding=FUNDING_MAXIMO_ABS * 2))
    assert not a.operavel
    assert "carrego" in a.reprovacoes[-1]


def test_funding_negativo_extremo_tambem_reprova():
    assert not avaliar(bom(funding=-FUNDING_MAXIMO_ABS * 2)).operavel


def test_varias_reprovacoes_sao_listadas():
    a = avaliar(LeituraAtivo("X", volume_24h_usd=1e5, spread_pct=0.9,
                             funding_rate=0.05, qualidade_ok=False))
    assert len(a.reprovacoes) == 4


# =====================================================================
# Score: falta de dado não pode ser premiada
# =====================================================================
def test_ativo_sem_dado_medido_nao_lidera_o_ranking():
    """O defeito que este teste trava: omitir componente não medido e
    renormalizar fazia o ativo SEM DADO sair com score máximo, porque
    sobravam só os componentes bons."""
    completo = avaliar(bom("CHEIO"))
    faltando = avaliar(LeituraAtivo("VAZIO", volume_24h_usd=5e9,
                                    spread_pct=None, funding_rate=None))
    assert faltando.score_medido > completo.score_medido
    assert faltando.score < completo.score
    assert faltando.completude == pytest.approx(0.55)


def test_completude_reflete_o_peso_medido():
    a = avaliar(LeituraAtivo("X", volume_24h_usd=5e9, spread_pct=0.02,
                             funding_rate=None))
    assert a.nao_medidos == ["funding"]
    assert a.completude == pytest.approx(0.85)
    assert a.score == pytest.approx(a.score_medido * 0.85)


def test_liquidez_e_logaritmica():
    """Acima de certo ponto, liquidez extra não muda nada para este sistema."""
    a = avaliar(bom(volume=VOLUME_MINIMO_USD * 10)).componentes["liquidez"]
    b = avaliar(bom(volume=VOLUME_MINIMO_USD * 100)).componentes["liquidez"]
    c = avaliar(bom(volume=VOLUME_MINIMO_USD * 1000)).componentes["liquidez"]
    assert a < b
    assert c == b == 1.0, "acima de cem vezes o piso a nota satura"


def test_spread_zero_tem_nota_cheia():
    assert avaliar(bom(spread=0.0)).componentes["spread"] == 1.0


def test_score_melhor_para_ativo_melhor():
    melhor = avaliar(bom("A", volume=1e10, spread=0.01))
    pior = avaliar(bom("B", volume=VOLUME_MINIMO_USD * 1.2, spread=0.12))
    assert melhor.score > pior.score
    assert melhor.operavel and pior.operavel


# =====================================================================
# Histerese
# =====================================================================
def test_construtor_recusa_histerese_invertida():
    """Readmitir mais fácil que remover faz o ativo oscilar a cada leitura."""
    with pytest.raises(ValueError, match="oscila"):
        GestorUniverso(["X"], ciclos_para_remover=5, ciclos_para_readmitir=3)


def test_uma_leitura_ruim_nao_remove():
    g = GestorUniverso(["BTCUSDT", "ALTUSDT"])
    g.atualizar([bom(), ruim()])
    assert "ALTUSDT" in g.universo


def test_remocao_exige_leituras_consecutivas():
    g = GestorUniverso(["ALTUSDT"])
    for _ in range(CICLOS_PARA_REMOVER - 1):
        g.atualizar([ruim()])
        assert "ALTUSDT" in g.universo
    r = g.atualizar([ruim()])
    assert "ALTUSDT" not in g.universo
    assert r.removidos_agora[0]["symbol"] == "ALTUSDT"


def test_leitura_boa_no_meio_zera_o_contador():
    """É o que impede uma oscilação de rede de derrubar um ativo bom."""
    g = GestorUniverso(["ALTUSDT"])
    g.atualizar([ruim()])
    g.atualizar([ruim()])
    g.atualizar([bom("ALTUSDT")])       # uma boa zera
    g.atualizar([ruim()])
    g.atualizar([ruim()])
    assert "ALTUSDT" in g.universo


def test_em_observacao_mostra_quanto_falta():
    g = GestorUniverso(["ALTUSDT"])
    r = g.atualizar([ruim()])
    assert r.em_observacao[0]["symbol"] == "ALTUSDT"
    assert r.em_observacao[0]["faltam"] == CICLOS_PARA_REMOVER - 1


def test_readmissao_exige_mais_ciclos_que_remocao():
    """Assimetria proposital: é mais barato ficar de fora de um ativo bom
    do que dentro de um ativo caro."""
    g = GestorUniverso(["ALTUSDT"])
    for _ in range(CICLOS_PARA_REMOVER):
        g.atualizar([ruim()])
    assert "ALTUSDT" not in g.universo

    for k in range(CICLOS_PARA_READMITIR - 1):
        r = g.atualizar([bom("ALTUSDT")])
        assert "ALTUSDT" not in g.universo
        assert r.fora[0]["faltam_para_voltar"] == CICLOS_PARA_READMITIR - k - 1
    r = g.atualizar([bom("ALTUSDT")])
    assert "ALTUSDT" in g.universo
    assert r.readmitidos_agora == ["ALTUSDT"]


def test_leitura_ruim_durante_a_recuperacao_reinicia():
    g = GestorUniverso(["ALTUSDT"])
    for _ in range(CICLOS_PARA_REMOVER):
        g.atualizar([ruim()])
    for _ in range(CICLOS_PARA_READMITIR - 1):
        g.atualizar([bom("ALTUSDT")])
    g.atualizar([ruim()])
    for _ in range(CICLOS_PARA_READMITIR - 1):
        g.atualizar([bom("ALTUSDT")])
    assert "ALTUSDT" not in g.universo


def test_readmissao_avisa_que_a_estatistica_nao_se_transfere():
    """A estatística anterior foi medida em outras condições de liquidez."""
    g = GestorUniverso(["ALTUSDT"])
    for _ in range(CICLOS_PARA_REMOVER):
        g.atualizar([ruim()])
    for _ in range(CICLOS_PARA_READMITIR - 1):
        g.atualizar([bom("ALTUSDT")])
    r = g.atualizar([bom("ALTUSDT")])
    assert any("não se transfere" in a for a in r.avisos)


# =====================================================================
# Relatório
# =====================================================================
def test_remocao_diz_quanta_estatistica_se_perde():
    """Descartar um par descarta o ativo mais valioso do sistema."""
    g = GestorUniverso(["ALTUSDT"])
    for _ in range(CICLOS_PARA_REMOVER):
        r = g.atualizar([ruim(n_trades_medidos=240)])
    assert r.removidos_agora[0]["estatistica_descartada"] == 240
    assert any("240 operações medidas" in a for a in r.avisos)


def test_ranking_ordena_do_melhor_para_o_pior():
    g = GestorUniverso(["A", "B", "C"])
    r = g.atualizar([bom("A", volume=1e10, spread=0.01),
                     bom("B", volume=1e8, spread=0.10),
                     bom("C", volume=5e9, spread=0.03)])
    nomes = [a.symbol for a in r.ranking]
    assert nomes[0] == "A"
    assert nomes[-1] == "B"


def test_universo_vazio_e_avisado_como_correto():
    """Operar um ativo cujo custo consome a vantagem é pior que não operar."""
    g = GestorUniverso(["A", "B"])
    for _ in range(CICLOS_PARA_REMOVER):
        r = g.atualizar([ruim("A"), ruim("B")])
    assert g.universo == []
    assert any("o universo ficou VAZIO" in a for a in r.avisos)
    assert any("pior que não operar" in a for a in r.avisos)


def test_todos_inoperaveis_avisa_que_o_ranking_nao_ajuda():
    g = GestorUniverso(["A", "B"])
    r = g.atualizar([ruim("A"), ruim("B")])
    assert any("ordena candidatos ruins" in a for a in r.avisos)


def test_ativo_novo_entra_no_estado():
    g = GestorUniverso(["A"])
    g.atualizar([bom("A"), bom("NOVO")])
    assert "NOVO" in g.universo
    assert g.estado_de("NOVO") is not None


def test_simbolo_e_normalizado_para_maiusculo():
    g = GestorUniverso(["btcusdt"])
    assert g.universo == ["BTCUSDT"]
    g.atualizar([bom("btcusdt")])
    assert g.universo == ["BTCUSDT"]


def test_relatorio_serializa_os_limites():
    d = GestorUniverso(["A"]).atualizar([bom("A")]).to_dict()
    assert d["limites"]["volume_minimo_usd"] == VOLUME_MINIMO_USD
    assert d["limites"]["ciclos_para_remover"] == CICLOS_PARA_REMOVER
    assert "assimetria é proposital" in d["observacao"]


def test_estado_do_gestor():
    g = GestorUniverso(["A", "B"])
    g.atualizar([bom("A"), ruim("B")])
    e = g.estado()
    assert e["universo"] == ["A", "B"]
    assert e["total_conhecidos"] == 2
    assert e["ativos"]["B"]["ciclos_ruins"] == 1
