"""Calibração de probabilidade.

Aqui a pergunta não é "o modelo acerta?", é "quando o modelo diz 70%,
acontece 70%?". A distinção importa porque a probabilidade não é o produto
final deste sistema: ela entra no cálculo de expectativa e no tamanho da
posição. Probabilidade inflada não gera previsão ruim, gera posição grande
demais.
"""
import random

import pytest

from investai.validation.calibracao import (
    ECE_ACEITAVEL, MIN_AMOSTRA, MIN_POR_FAIXA, SKILL_MINIMO, avaliar_calibracao,
    calcular_brier, recalibrar_isotonico,
)


def modelo_honesto(n=2000, seed=7):
    """Previsões cuja frequência observada é exatamente a prevista."""
    rng = random.Random(seed)
    p = [rng.random() for _ in range(n)]
    y = [rng.random() < pi for pi in p]
    return p, y


def modelo_que_superestima(n=2000, fator=0.6, seed=11):
    """Diz p, acontece p*fator. É o erro que custa dinheiro."""
    rng = random.Random(seed)
    p = [rng.random() for _ in range(n)]
    y = [rng.random() < pi * fator for pi in p]
    return p, y


# ------------------------------------------------------------- validação
def test_tamanhos_diferentes_sao_recusados():
    with pytest.raises(ValueError, match="tamanhos diferentes"):
        avaliar_calibracao([0.5, 0.6], [True])


def test_probabilidade_em_percentual_e_recusada():
    """70 em vez de 0,70 produziria calibração silenciosamente absurda."""
    with pytest.raises(ValueError, match=r"fora de \[0,1\]"):
        avaliar_calibracao([70.0], [True])


def test_probabilidade_negativa_e_recusada():
    with pytest.raises(ValueError):
        avaliar_calibracao([-0.1], [True])


def test_amostra_vazia_nao_explode():
    r = avaliar_calibracao([], [])
    assert r.n == 0
    assert r.veredicto == "SEM_AMOSTRA"
    assert "nada a calibrar" in r.avisos[0]


def test_n_faixas_minimo():
    with pytest.raises(ValueError, match="n_faixas"):
        avaliar_calibracao([0.5], [True], n_faixas=1)


# ---------------------------------------------------- modelo bem calibrado
def test_modelo_honesto_e_aprovado():
    p, y = modelo_honesto()
    r = avaliar_calibracao(p, y)
    assert r.veredicto == "CALIBRADO"
    assert r.calibrado
    assert r.ece < ECE_ACEITAVEL
    assert abs(r.vies) < 0.03


def test_modelo_honesto_tem_skill():
    p, y = modelo_honesto()
    b = calcular_brier(p, y)
    assert b.melhor_que_taxa_base
    assert b.skill > 0.2
    assert b.resolucao > 0.05


# -------------------------------------------------------- superestimação
def test_superestimacao_e_reprovada():
    p, y = modelo_que_superestima()
    r = avaliar_calibracao(p, y)
    assert r.veredicto == "DESCALIBRADO"
    assert not r.calibrado


def test_superestimacao_e_apontada_como_o_erro_perigoso():
    """A mensagem tem de dizer por que superestimar é pior que subestimar."""
    p, y = modelo_que_superestima()
    r = avaliar_calibracao(p, y)
    aviso = " ".join(r.avisos)
    assert "SUPERESTIMA" in aviso
    assert "posição grande demais" in aviso
    assert r.to_dict()["direcao_vies"] == "superestima"


def test_subestimacao_e_tratada_como_menos_grave():
    """Subestimar deixa oportunidade na mesa; não perde dinheiro."""
    rng = random.Random(3)
    p = [rng.uniform(0.0, 0.5) for _ in range(2000)]
    y = [rng.random() < min(1.0, pi * 1.8) for pi in p]
    r = avaliar_calibracao(p, y)
    assert r.to_dict()["direcao_vies"] == "subestima"
    assert any("na mesa" in a for a in r.avisos)


# ----------------------------------------- o modelo mais inútil possível
def test_modelo_que_so_diz_a_taxa_base_e_reprovado():
    """Perfeitamente confiável e completamente inútil.

    Este é o caso que o Brier sozinho aprova: o erro quadrático dele é
    pequeno. Só a decomposição revela que ele não distingue nada.
    """
    p, y = modelo_honesto()
    base = sum(1 for v in y if v) / len(y)
    r = avaliar_calibracao([base] * len(y), y)
    assert r.veredicto == "SEM_PODER_DISCRIMINANTE"
    assert not r.calibrado
    assert r.ece < 0.01, "ele É bem calibrado — é o ponto"
    assert r.brier.resolucao < 1e-9, "e não resolve nada"
    assert any("taxa-base" in a for a in r.avisos)


def test_skill_nao_confunde_residuo_numerico_com_poder_preditivo():
    """O modelo da taxa-base tem skill zero, mas sai 5e-15 em ponto flutuante.

    Sem um limiar, `skill > 0` aprovaria exatamente o modelo mais inútil
    que existe.
    """
    p, y = modelo_honesto()
    base = sum(1 for v in y if v) / len(y)
    b = calcular_brier([base] * len(y), y)
    assert 0 <= b.skill < SKILL_MINIMO
    assert not b.melhor_que_taxa_base


# ------------------------------------------------------------- Brier
def test_previsao_perfeita_tem_brier_zero():
    y = [True, False, True, False] * 50
    p = [1.0 if v else 0.0 for v in y]
    b = calcular_brier(p, y)
    assert b.score == pytest.approx(0.0)
    assert b.skill == pytest.approx(1.0)


def test_previsao_invertida_tem_skill_negativo():
    """Pior que não modelar nada."""
    y = [True, False] * 100
    p = [0.0 if v else 1.0 for v in y]
    b = calcular_brier(p, y)
    assert b.score == pytest.approx(1.0)
    assert b.skill < 0
    assert not b.melhor_que_taxa_base


def test_decomposicao_de_murphy_fecha_com_previsao_discreta():
    """Com previsão que cai exatamente nas faixas, a identidade é exata."""
    rng = random.Random(5)
    p, y = [], []
    for valor in (0.15, 0.35, 0.55, 0.75, 0.95):
        for _ in range(200):
            p.append(valor)
            y.append(rng.random() < valor)
    b = calcular_brier(p, y, n_faixas=10)
    assert b.residuo_binagem == pytest.approx(0.0, abs=1e-12)
    assert b.score == pytest.approx(
        b.confiabilidade - b.resolucao + b.incerteza, abs=1e-12)


def test_residuo_de_binagem_encolhe_com_mais_faixas():
    """Ele existe, é exposto, e some quando as faixas ficam estreitas.

    Esconder esse resíduo faria a decomposição parecer exata quando não é.
    """
    p, y = modelo_honesto(n=4000)
    grosso = abs(calcular_brier(p, y, n_faixas=5).residuo_binagem)
    fino = abs(calcular_brier(p, y, n_faixas=50).residuo_binagem)
    assert grosso > fino
    assert fino < 1e-3


def test_incerteza_e_propriedade_dos_dados():
    """A incerteza não depende do modelo, só da taxa-base."""
    y = [True] * 300 + [False] * 700
    a = calcular_brier([0.3] * 1000, y)
    b = calcular_brier([0.9] * 1000, y)
    assert a.incerteza == pytest.approx(b.incerteza)
    assert a.incerteza == pytest.approx(0.3 * 0.7)


# -------------------------------------------------------------- faixas
def test_faixa_pequena_e_marcada_nao_conclusiva():
    """Faixa com poucos casos aparece no relatório, mas sem valer conclusão.

    Esconder faixa pequena daria a impressão de que o modelo só opera onde
    tem dados.
    """
    p = [0.95] * 3 + [0.05] * 300
    y = [True] * 3 + [False] * 300
    r = avaliar_calibracao(p, y)
    alta = [f for f in r.faixas if f.inferior >= 0.9][0]
    assert alta.n == 3
    assert not alta.conclusiva
    assert any("não pôde ser medido" in a or "faixa" in a for a in r.avisos)


def test_faixa_usa_intervalo_de_wilson_para_julgar_desvio():
    """Com amostra pequena, 15 pontos de erro podem ser ruído.

    O intervalo de Wilson responde se são. Sem ele, o relatório acusaria
    descalibração em toda faixa pouco povoada.
    """
    p = [0.5] * 10
    y = [True] * 6 + [False] * 4      # observado 0,60 contra previsto 0,50
    r = avaliar_calibracao(p, y)
    faixa = [f for f in r.faixas if f.n == 10][0]
    assert faixa.observada == pytest.approx(0.6)
    assert faixa.coerente, "0,50 cabe no intervalo de 6 em 10"


def test_faixa_com_desvio_real_e_apontada():
    p = [0.9] * 200
    y = [True] * 60 + [False] * 140   # disse 90%, aconteceu 30%
    r = avaliar_calibracao(p, y)
    faixa = [f for f in r.faixas if f.n == 200][0]
    assert not faixa.coerente
    assert any("aconteceu" in a for a in r.avisos)


def test_probabilidade_um_cai_na_ultima_faixa():
    """1,0 não pode ser descartado por causa do limite do intervalo."""
    r = avaliar_calibracao([1.0] * 50, [True] * 50)
    assert r.n == 50
    assert sum(f.n for f in r.faixas) == 50


def test_todas_as_observacoes_entram_em_alguma_faixa():
    p, y = modelo_honesto(n=500)
    r = avaliar_calibracao(p, y)
    assert sum(f.n for f in r.faixas) == 500


# -------------------------------------------------------------- amostra
def test_amostra_pequena_nao_aprova():
    """Ausência de medição nunca é aprovação."""
    p = [0.5] * 20
    y = [True] * 10 + [False] * 10
    r = avaliar_calibracao(p, y)
    assert r.veredicto == "SEM_AMOSTRA"
    assert not r.calibrado
    assert f"mínimo de {MIN_AMOSTRA}" in " ".join(r.avisos)


def test_no_limite_da_amostra_o_veredicto_passa_a_valer():
    rng = random.Random(9)
    p = [rng.random() for _ in range(MIN_AMOSTRA)]
    y = [rng.random() < pi for pi in p]
    r = avaliar_calibracao(p, y)
    assert r.amostra_suficiente
    assert r.veredicto != "SEM_AMOSTRA"


# ---------------------------------------------------------- recalibração
def test_recalibracao_corrige_superestimacao():
    p, y = modelo_que_superestima(n=6000)
    m = recalibrar_isotonico(p, y)
    assert m.melhorou
    assert m.ece_depois < 0.02
    assert m.ece_antes > 0.15


def test_mapa_calibrado_aproxima_a_relacao_verdadeira():
    """O modelo diz p, acontece 0,6p. O mapa tem de aprender isso."""
    p, y = modelo_que_superestima(n=20000)
    m = recalibrar_isotonico(p, y)
    for dito in (0.3, 0.5, 0.7, 0.9):
        assert m.aplicar(dito) == pytest.approx(0.6 * dito, abs=0.05)


def test_mapa_e_monotonico():
    """Probabilidade maior nunca pode virar probabilidade menor."""
    p, y = modelo_que_superestima(n=4000)
    m = recalibrar_isotonico(p, y)
    saidas = [m.aplicar(x / 100) for x in range(0, 101)]
    assert all(saidas[i] <= saidas[i + 1] + 1e-12
               for i in range(len(saidas) - 1))


def test_mapa_respeita_os_limites():
    p, y = modelo_que_superestima(n=1000)
    m = recalibrar_isotonico(p, y)
    for x in (-5.0, 0.0, 0.5, 1.0, 7.0):
        assert 0.0 <= m.aplicar(x) <= 1.0


def test_mapa_vazio_e_identidade():
    """Sem ajuste, aplicar não pode inventar transformação."""
    from investai.validation.calibracao import ModeloCalibrado
    assert ModeloCalibrado().aplicar(0.73) == pytest.approx(0.73)


def test_recalibracao_sem_dados_devolve_mapa_vazio():
    m = recalibrar_isotonico([], [])
    assert m.pontos == []
    assert m.n_ajuste == 0


def test_recalibracao_avisa_que_o_ece_depois_e_otimista():
    """Medir na mesma amostra do ajuste sempre favorece o mapa.

    Quem lê o relatório precisa poder notar isso, senão a recalibração vira
    uma forma elegante de overfitting.
    """
    p, y = modelo_que_superestima(n=1000)
    d = recalibrar_isotonico(p, y).to_dict()
    assert "otimista" in d["observacao"]
    assert "não participaram do ajuste" in d["observacao"]


def test_recalibracao_de_modelo_ja_calibrado_nao_piora_muito():
    p, y = modelo_honesto(n=6000)
    m = recalibrar_isotonico(p, y)
    assert m.ece_depois <= m.ece_antes + 0.01


def test_empates_recebem_o_mesmo_valor_calibrado():
    """Duas previsões iguais não podem sair calibradas diferentes."""
    p = [0.4] * 100 + [0.8] * 100
    y = [True] * 30 + [False] * 70 + [True] * 50 + [False] * 50
    m = recalibrar_isotonico(p, y)
    assert m.aplicar(0.4) == pytest.approx(m.aplicar(0.4))
    assert m.aplicar(0.4) == pytest.approx(0.30, abs=0.02)
    assert m.aplicar(0.8) == pytest.approx(0.50, abs=0.02)


def test_relacao_invertida_e_achatada_pelo_isotonico():
    """Isotônico só admite relação monotônica crescente.

    Se o modelo tem relação invertida, o ajuste correto é achatar para uma
    constante — que é a forma de dizer "esta previsão não informa nada".
    """
    rng = random.Random(4)
    p = [rng.random() for _ in range(2000)]
    y = [rng.random() < (1.0 - pi) for pi in p]
    m = recalibrar_isotonico(p, y)
    saidas = [m.aplicar(x / 10) for x in range(11)]
    assert max(saidas) - min(saidas) < 0.15


# ------------------------------------------------------------- relatório
def test_relatorio_serializa_completo():
    p, y = modelo_honesto(n=500)
    d = avaliar_calibracao(p, y).to_dict()
    assert {"veredicto", "calibrado", "ece", "mce", "taxa_base", "vies",
            "direcao_vies", "faixas", "brier", "avisos"} <= set(d)
    assert "leitura" in d["brier"]
    assert "inútil" in d["brier"]["leitura"]


def test_mce_ignora_faixa_nao_conclusiva():
    """Erro máximo medido em faixa de 2 casos seria ruído, não medição."""
    p = [0.5] * 300 + [0.95] * 2
    y = [True] * 150 + [False] * 150 + [False] * 2
    r = avaliar_calibracao(p, y)
    assert r.mce < 0.1, "a faixa de 2 casos tem erro de 0,95 e não deve contar"


def test_min_por_faixa_e_o_corte_documentado():
    p = [0.55] * MIN_POR_FAIXA
    y = [True] * 5 + [False] * 5
    r = avaliar_calibracao(p, y)
    faixa = [f for f in r.faixas if f.n == MIN_POR_FAIXA][0]
    assert faixa.conclusiva
