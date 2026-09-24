"""Detecção de drift.

O erro que estes testes protegem contra é chamar azar de drift. Uma
sequência ruim de dez operações é comum em uma estratégia boa — a
distribuição de resultados em R tem cauda. Um sistema que desliga a cada
série azarada e religa a cada série sortuda transformou ruído em decisão.
"""
import random

import pytest

from investai.ops.drift import (
    MIN_JANELA, MIN_TRADES, PSI_ESTAVEL, PSI_MODERADO, avaliar_drift,
    drift_de_conceito, drift_de_dado, drift_de_performance, drift_de_regime,
    psi,
)

N = 400


def normal(seed, mu=0.0, sigma=1.0, n=N):
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


# =====================================================================
# PSI
# =====================================================================
def test_psi_de_distribuicoes_iguais_e_baixo():
    assert psi(normal(1), normal(2)) < PSI_ESTAVEL


def test_psi_de_serie_com_ela_mesma_e_zero():
    a = normal(3)
    assert psi(a, a) == pytest.approx(0.0, abs=1e-9)


def test_psi_de_distribuicao_deslocada_e_alto():
    assert psi(normal(4), normal(5, mu=1.5)) > PSI_MODERADO


def test_psi_detecta_mudanca_de_dispersao():
    """Média igual, variância diferente, ainda é outra distribuição."""
    assert psi(normal(6), normal(7, sigma=3.0)) > PSI_MODERADO


def test_psi_de_janela_curta_e_none():
    """Comparar distribuições com poucas observações compara ruído."""
    assert psi(normal(8, n=MIN_JANELA - 1), normal(9)) is None
    assert psi(normal(8), normal(9, n=MIN_JANELA - 1)) is None


def test_psi_no_limite_da_janela_mede():
    assert psi(normal(10, n=MIN_JANELA), normal(11, n=MIN_JANELA)) is not None


def test_psi_com_referencia_constante_nao_explode():
    assert psi([5.0] * N, normal(12)) == 0.0


def test_psi_com_balde_vazio_nao_vira_infinito():
    """Um balde vazio não é evidência infinita de mudança."""
    ref = normal(13)
    atual = [100.0] * N          # nenhuma sobreposição
    v = psi(ref, atual)
    assert v is not None and v < 100.0


def test_psi_usa_quantis_e_nao_largura_fixa():
    """Em distribuição assimétrica, faixas fixas deixam tudo num balde só.

    Aqui a referência é muito concentrada perto de zero com cauda longa. Uma
    mudança real na massa precisa ser detectada.
    """
    rng = random.Random(14)
    ref = [rng.expovariate(1.0) for _ in range(N)]
    atual = [rng.expovariate(1.0) + 2.0 for _ in range(N)]
    assert psi(ref, atual) > PSI_MODERADO


# =====================================================================
# Drift de dado
# =====================================================================
def test_dado_estavel():
    a = drift_de_dado({"f1": normal(20), "f2": normal(21)},
                      {"f1": normal(22), "f2": normal(23)})
    assert a.nivel == "estavel"


def test_dado_com_uma_feature_deslocada_e_drift():
    """Basta uma feature sair da faixa: o modelo passa a extrapolar."""
    a = drift_de_dado({"f1": normal(24), "f2": normal(25)},
                      {"f1": normal(26), "f2": normal(27, mu=2.0)})
    assert a.nivel == "drift"
    assert "f2" in a.detalhe
    assert "revalidar" in a.acao


def test_dado_reporta_o_pior_caso_nao_a_media():
    """Uma feature muito deslocada não pode ser diluída pelas estáveis."""
    ref = {f"f{i}": normal(30 + i) for i in range(10)}
    atual = {f"f{i}": normal(50 + i) for i in range(10)}
    atual["f9"] = normal(99, mu=3.0)
    a = drift_de_dado(ref, atual)
    assert a.nivel == "drift"
    assert "f9" in a.detalhe


def test_dado_sem_feature_comum_e_sem_amostra():
    a = drift_de_dado({"a": normal(60)}, {"b": normal(61)})
    assert a.nivel == "sem_amostra"


def test_dado_com_janela_curta_e_sem_amostra():
    a = drift_de_dado({"a": normal(62, n=10)}, {"a": normal(63, n=10)})
    assert a.nivel == "sem_amostra"
    assert str(MIN_JANELA) in a.detalhe


# =====================================================================
# Drift de performance — o que mais se confunde com azar
# =====================================================================
def test_sequencia_azarada_nao_e_drift():
    """Trinta e cinco operações da MESMA distribuição não podem acusar drift.

    Este é o teste central do módulo. Se ele falhasse, o sistema desligaria
    estratégias boas por azar de amostra.
    """
    base = normal(70, mu=0.2, n=300)
    for seed in range(71, 86):
        recente = normal(seed, mu=0.2, n=MIN_TRADES + 5)
        a = drift_de_performance(base, recente)
        assert a.nivel != "drift", (
            f"semente {seed} acusou drift em amostra da mesma distribuição: "
            f"{a.detalhe}")


def test_queda_real_e_drift():
    base = normal(90, mu=0.25, n=300)
    recente = normal(91, mu=-0.6, n=60)
    a = drift_de_performance(base, recente)
    assert a.nivel == "drift"
    assert "não é explicada por azar" in a.acao
    assert a.valor < -1.96


def test_queda_intermediaria_e_atencao_e_nao_conclusao():
    base = normal(92, mu=0.30, sigma=1.0, n=400)
    recente = normal(93, mu=0.02, sigma=1.0, n=60)
    a = drift_de_performance(base, recente)
    assert a.nivel in ("atencao", "drift")
    if a.nivel == "atencao":
        assert "não é conclusiva" in a.acao


def test_melhora_nunca_e_drift():
    base = normal(94, mu=0.1, n=300)
    recente = normal(95, mu=0.8, n=60)
    assert drift_de_performance(base, recente).nivel == "estavel"


def test_amostra_recente_pequena_e_sem_amostra():
    base = normal(96, mu=0.2, n=300)
    a = drift_de_performance(base, normal(97, mu=-2.0, n=MIN_TRADES - 1))
    assert a.nivel == "sem_amostra"
    assert "nenhuma conclusão é possível" in a.detalhe


def test_baseline_pequeno_e_sem_amostra():
    a = drift_de_performance(normal(98, n=MIN_TRADES - 1), normal(99, n=100))
    assert a.nivel == "sem_amostra"


def test_variancia_maior_na_amostra_recente_e_considerada():
    """Welch: a variância costuma mudar junto com a média.

    Supor variâncias iguais superestimaria a significância quando a amostra
    recente é mais volátil — e acusaria drift onde há só mais ruído.
    """
    base = normal(100, mu=0.2, sigma=0.5, n=300)
    recente = normal(101, mu=-0.1, sigma=3.0, n=40)
    a = drift_de_performance(base, recente)
    assert a.nivel != "drift"


def test_variancia_zero_nao_divide_por_zero():
    a = drift_de_performance([0.5] * 100, [0.5] * 50)
    assert a.nivel == "estavel"


# =====================================================================
# Drift de regime
# =====================================================================
def test_regime_estavel():
    ref = ["tendencia_alta"] * 80 + ["lateral"] * 20
    atual = ["tendencia_alta"] * 78 + ["lateral"] * 22
    assert drift_de_regime(ref, atual).nivel == "estavel"


def test_troca_de_regime_dominante_e_drift():
    ref = ["tendencia_alta"] * 80 + ["lateral"] * 20
    atual = ["lateral"] * 80 + ["tendencia_alta"] * 20
    a = drift_de_regime(ref, atual)
    assert a.nivel == "drift"
    assert "não se transfere" in a.acao
    assert a.valor == pytest.approx(0.60, abs=0.01)


def test_mudanca_sem_troca_de_dominante_e_atencao():
    ref = ["tendencia_alta"] * 90 + ["lateral"] * 10
    atual = ["tendencia_alta"] * 55 + ["lateral"] * 45
    a = drift_de_regime(ref, atual)
    assert a.nivel == "atencao"


def test_regime_novo_que_nao_existia_na_referencia():
    ref = ["tendencia_alta"] * 100
    atual = ["alta_volatilidade"] * 60 + ["tendencia_alta"] * 40
    a = drift_de_regime(ref, atual)
    assert a.nivel == "drift"
    assert "alta_volatilidade" in a.detalhe


def test_regime_com_janela_curta_e_sem_amostra():
    a = drift_de_regime(["lateral"] * 10, ["tendencia_alta"] * 10)
    assert a.nivel == "sem_amostra"


# =====================================================================
# Drift de conceito
# =====================================================================
def conceito_calibrado(seed, n=600):
    rng = random.Random(seed)
    p = [rng.random() for _ in range(n)]
    y = [rng.random() < x for x in p]
    return p, y


def test_conceito_nao_acusa_drift_em_amostras_do_mesmo_processo():
    """Duas janelas do MESMO processo calibrado não podem virar drift.

    Testado como TAXA, não como um par de sementes: "atenção" está definida
    a 2 desvios e por construção dispara por acaso em alguns por cento das
    checagens. O que não pode acontecer é "drift", definido a 3 desvios.

    A primeira versão deste módulo usava cortes fixos (0,02 de ECE e -0,05
    de skill), ambos dentro do ruído a n=600; qualquer par de janelas iguais
    acusava mudança.
    """
    niveis = []
    for k in range(20):
        p1, y1 = conceito_calibrado(1100 + 2 * k)
        p2, y2 = conceito_calibrado(1101 + 2 * k)
        niveis.append(drift_de_conceito(p1, y1, p2, y2).nivel)
    assert "drift" not in niveis, "drift acusado em janelas do mesmo processo"
    assert niveis.count("estavel") >= 15, (
        f"atenção disparou demais em processo estável: {niveis}")


def test_limiares_de_conceito_acompanham_a_amostra():
    """Amostra menor tem de exigir diferença maior para significar algo."""
    from investai.ops.drift import piso_ruido_ece, sigma_skill
    assert piso_ruido_ece(200) > piso_ruido_ece(2000)
    assert sigma_skill(200) > sigma_skill(2000)
    # E os valores batem com o que foi medido.
    assert piso_ruido_ece(600) == pytest.approx(0.052, abs=0.005)
    assert sigma_skill(600) == pytest.approx(0.045, abs=0.005)


def test_relacao_que_desaparece_e_drift():
    """As entradas continuam familiares, as respostas não.

    É o drift que nada na distribuição das features denuncia — por isso ele
    precisa ser medido contra o desfecho.
    """
    p1, y1 = conceito_calibrado(112)
    rng = random.Random(113)
    p2 = [rng.random() for _ in range(600)]
    y2 = [rng.random() < 0.35 for _ in p2]
    a = drift_de_conceito(p1, y1, p2, y2)
    assert a.nivel == "drift"


def test_perda_de_poder_preditivo_pede_modelo_novo_nao_recalibracao():
    """Recalibrar não devolve poder preditivo que se perdeu."""
    p1, y1 = conceito_calibrado(114)
    rng = random.Random(115)
    p2 = [rng.random() for _ in range(600)]
    y2 = [rng.random() < 0.35 for _ in p2]
    a = drift_de_conceito(p1, y1, p2, y2)
    assert "treinar outro" in a.acao
    assert "recalibrar não resolve" in a.acao


def test_conceito_com_janela_curta_e_sem_amostra():
    p1, y1 = conceito_calibrado(116)
    a = drift_de_conceito(p1, y1, [0.5] * 10, [True] * 10)
    assert a.nivel == "sem_amostra"


def test_conceito_com_referencia_curta_e_sem_amostra():
    p2, y2 = conceito_calibrado(117)
    a = drift_de_conceito([0.5] * 10, [True] * 10, p2, y2)
    assert a.nivel == "sem_amostra"


# =====================================================================
# Relatório conjunto
# =====================================================================
def test_relatorio_vazio_nao_e_estavel():
    """Não ter medido não é o mesmo que não ter drift.

    Se isto devolvesse "estável", a tela ficaria verde por não ter dados —
    que é exatamente a confusão que este módulo existe para evitar.
    """
    r = avaliar_drift()
    assert r.nivel == "nao_medido"
    assert not r.exige_acao
    assert any("não é o mesmo que" in a for a in r.avisos)


def test_relatorio_roda_so_o_que_tem_dados():
    r = avaliar_drift(baseline_r=normal(120, mu=0.2, n=200),
                      recente_r=normal(121, mu=0.2, n=60))
    assert [a.tipo for a in r.achados] == ["performance"]
    assert r.nivel == "estavel"


def test_relatorio_junta_os_quatro():
    p1, y1 = conceito_calibrado(122)
    p2, y2 = conceito_calibrado(123)
    r = avaliar_drift(
        features_ref={"f": normal(124)}, features_atual={"f": normal(125)},
        previstas_ref=p1, ocorreu_ref=y1, previstas_atual=p2, ocorreu_atual=y2,
        baseline_r=normal(126, mu=0.2, n=200),
        recente_r=normal(127, mu=0.2, n=60),
        regimes_ref=["lateral"] * 100, regimes_atual=["lateral"] * 100)
    assert {a.tipo for a in r.achados} == {"dado", "conceito", "performance",
                                          "regime"}
    assert r.nivel == "estavel"


def test_um_drift_domina_o_nivel_do_relatorio():
    r = avaliar_drift(
        features_ref={"f": normal(130)},
        features_atual={"f": normal(131, mu=3.0)},
        baseline_r=normal(132, mu=0.2, n=200),
        recente_r=normal(133, mu=0.2, n=60))
    assert r.nivel == "drift"
    assert r.exige_acao
    assert any("DRIFT DE DADO" in a for a in r.avisos)


def test_sem_amostra_e_listado_e_nao_escondido():
    r = avaliar_drift(baseline_r=normal(134, n=200),
                      recente_r=normal(135, n=5))
    assert r.sem_amostra
    assert any("Ausência de medição não é ausência de drift" in a
               for a in r.avisos)


def test_relatorio_diz_que_nao_age_sozinho():
    """Retreinar e despromover mudam o que vai a mercado."""
    d = avaliar_drift(baseline_r=normal(136, n=200),
                      recente_r=normal(137, n=60)).to_dict()
    assert "não retreina nem despromove sozinho" in d["observacao"]
