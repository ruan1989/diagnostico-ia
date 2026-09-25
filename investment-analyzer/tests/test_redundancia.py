"""Evidência redundante: quantas opiniões independentes existem de verdade.

RSI, MACD e cruzamento de médias derivam todos do mesmo preço. Quando os três
concordam, isso soa como três confirmações e é uma só, vista de três ângulos.
Contar três infla a confiança exatamente quando ela deveria ser questionada:
em tendência forte, indicadores de tendência concordam por construção.
"""
import random

import pytest

from investai.analysis.redundancia import (
    FATOR_ALERTA, LIMIAR_REDUNDANCIA, MIN_OBSERVACOES, HistoricoOpinioes,
    avaliar_redundancia, correlacao,
)

N = 300


def ruido(seed, n=N):
    rng = random.Random(seed)
    return [rng.gauss(0, 1) for _ in range(n)]


def eco(base, seed, sigma=0.02):
    """A mesma série com um pouco de ruído: informação idêntica."""
    rng = random.Random(seed)
    return [b + rng.gauss(0, sigma) for b in base]


# ------------------------------------------------------------- correlação
def test_correlacao_de_serie_com_ela_mesma():
    a = ruido(1)
    assert correlacao(a, a) == pytest.approx(1.0)


def test_correlacao_de_series_opostas():
    a = ruido(1)
    assert correlacao(a, [-x for x in a]) == pytest.approx(-1.0)


def test_correlacao_de_amostra_curta_e_none():
    """None é diferente de zero.

    Zero afirmaria "medi e são independentes". None diz "não consegui medir",
    e é a informação que permite ser conservador.
    """
    assert correlacao([1, 2, 3], [1, 2, 4]) is None


def test_correlacao_de_serie_constante_e_none():
    assert correlacao([5.0] * N, ruido(2)) is None


def test_limite_exato_da_amostra():
    a = ruido(3, MIN_OBSERVACOES)
    assert correlacao(a, a) is not None
    assert correlacao(a[:-1], a[:-1]) is None


# ------------------------------------------------------- peso efetivo
def test_fontes_independentes_conservam_quase_todo_o_peso():
    fontes = {f"f{i}": ruido(10 + i) for i in range(3)}
    r = avaliar_redundancia(fontes, {f"f{i}": 0.2 for i in range(3)})
    assert r.peso_total == pytest.approx(0.6)
    # Não é exatamente 0,6: a matriz usa |r|, então ruído de amostra finita
    # sempre parece um pouco de redundância. O erro é para o lado seguro.
    assert 0.52 < r.peso_efetivo <= 0.6
    assert r.fator > 0.85
    assert not r.material
    assert r.grupos == []


def test_tres_fontes_identicas_valem_uma():
    """O caso RSI + MACD + EMA."""
    base = ruido(20)
    fontes = {"rsi": eco(base, 1), "macd": eco(base, 2), "ema": eco(base, 3)}
    r = avaliar_redundancia(fontes, {"rsi": 0.2, "macd": 0.2, "ema": 0.2})
    assert r.peso_efetivo == pytest.approx(0.2, abs=0.02)
    assert r.fator == pytest.approx(1 / 3, abs=0.05)
    assert r.material


def test_mistura_fica_entre_os_dois_extremos():
    base = ruido(30)
    fontes = {"rsi": eco(base, 1), "macd": eco(base, 2),
              "funding": ruido(31)}
    r = avaliar_redundancia(fontes, {k: 0.2 for k in fontes})
    assert 0.25 < r.peso_efetivo < 0.5
    assert r.grupos == [["macd", "rsi"]]
    assert "funding" not in [f for g in r.grupos for f in g]


def test_correlacao_negativa_tambem_e_informacao_compartilhada():
    """Duas fontes espelhadas não são duas evidências.

    O que importa é quanta informação é compartilhada, e isso é |r|. Uma
    fonte que é o negativo exato da outra não acrescenta nada.
    """
    base = ruido(40)
    r = avaliar_redundancia({"a": base, "b": [-x for x in base]},
                            {"a": 0.3, "b": 0.3})
    assert r.peso_efetivo == pytest.approx(0.3, abs=0.02)
    assert r.pares_redundantes[0][2] < 0


def test_peso_efetivo_nunca_passa_do_total():
    fontes = {f"f{i}": ruido(50 + i) for i in range(5)}
    r = avaliar_redundancia(fontes, {f"f{i}": 0.1 for i in range(5)})
    assert r.peso_efetivo <= r.peso_total + 1e-9


def test_pesos_desiguais_redundantes_colapsam_para_perto_do_maior():
    base = ruido(60)
    r = avaliar_redundancia({"grande": base, "pequeno": eco(base, 7)},
                            {"grande": 0.5, "pequeno": 0.1})
    # O ideal seria 0,5 (só a grande informa). A fórmula é conservadora e
    # fica um pouco abaixo, que é o lado certo para errar.
    assert 0.40 < r.peso_efetivo < 0.52


def test_pesos_desiguais_independentes_conservam_o_peso():
    r = avaliar_redundancia({"grande": ruido(70), "pequeno": ruido(71)},
                            {"grande": 0.5, "pequeno": 0.1})
    assert r.peso_efetivo > 0.55


def test_fonte_unica_tem_fator_um():
    r = avaliar_redundancia({"so_ela": ruido(80)}, {"so_ela": 0.4})
    assert r.fator == pytest.approx(1.0)
    assert r.peso_efetivo == pytest.approx(0.4)


def test_conjunto_vazio_nao_explode():
    r = avaliar_redundancia({}, {})
    assert r.fontes == []
    assert r.fator == 1.0


# ------------------------------------------- ausência de medição é conservadora
def test_amostra_curta_trata_tudo_como_redundante():
    """Supor independência no escuro aceitaria repetição como evidência nova."""
    fontes = {"a": ruido(90, 5), "b": ruido(91, 5), "c": ruido(92, 5)}
    r = avaliar_redundancia(fontes, {k: 0.2 for k in fontes})
    assert not r.mensurado
    assert r.peso_efetivo == pytest.approx(0.2, abs=0.01)
    assert any(str(MIN_OBSERVACOES) in a for a in r.avisos)
    assert any("conservadora" in a for a in r.avisos)


def test_serie_constante_entra_como_nao_medida():
    fontes = {"viva": ruido(100), "morta": [0.0] * N}
    r = avaliar_redundancia(fontes, {"viva": 0.3, "morta": 0.3})
    assert ("morta", "viva") in r.nao_medidos or ("viva", "morta") in r.nao_medidos
    assert any("sem correlação mensurável" in a for a in r.avisos)


# ----------------------------------------------------------------- grupos
def test_grupos_juntam_fontes_ligadas_em_cadeia():
    base = ruido(110)
    fontes = {"a": eco(base, 1), "b": eco(base, 2), "c": eco(base, 3),
              "z": ruido(111)}
    r = avaliar_redundancia(fontes, {k: 0.2 for k in fontes})
    assert r.grupos == [["a", "b", "c"]]


def test_dois_grupos_separados():
    b1, b2 = ruido(120), ruido(121)
    fontes = {"a1": eco(b1, 1), "a2": eco(b1, 2),
              "b1": eco(b2, 3), "b2": eco(b2, 4)}
    r = avaliar_redundancia(fontes, {k: 0.15 for k in fontes})
    assert sorted(r.grupos) == [["a1", "a2"], ["b1", "b2"]]


def test_limiar_configuravel():
    base = ruido(130)
    rng = random.Random(9)
    # Correlação intermediária: passa em limiar baixo, não em limiar alto.
    meio = [0.6 * b + 0.8 * rng.gauss(0, 1) for b in base]
    fontes = {"a": base, "b": meio}
    assert avaliar_redundancia(fontes, limiar=0.9).grupos == []
    assert avaliar_redundancia(fontes, limiar=0.3).grupos == [["a", "b"]]


def test_limiar_padrao_e_o_documentado():
    base = ruido(140)
    fontes = {"a": base, "b": eco(base, 1)}
    r = avaliar_redundancia(fontes)
    assert abs(r.pares_redundantes[0][2]) >= LIMIAR_REDUNDANCIA


def test_relatorio_explica_a_leitura():
    base = ruido(150)
    r = avaliar_redundancia({"a": base, "b": eco(base, 1), "c": eco(base, 2)},
                            {"a": 0.2, "b": 0.2, "c": 0.2})
    d = r.to_dict()
    assert "valem 0,2, não" in d["leitura"]
    assert "desconto silencioso" in d["leitura"]
    assert d["material"] is True
    assert d["fator"] < FATOR_ALERTA


# ------------------------------------------------------------- histórico
def test_historico_acumula_e_mede():
    h = HistoricoOpinioes()
    base = ruido(160)
    for k, v in enumerate(base):
        h.registrar({"rsi": v, "macd": v + 0.01, "funding": ruido(161)[k]})
    assert h.n_rodadas == N
    r = h.avaliar({"rsi": 0.2, "macd": 0.2, "funding": 0.2})
    assert r.grupos == [["macd", "rsi"]]


def test_historico_usa_so_rodadas_completas():
    """Séries desalinhadas correlacionariam coisas diferentes.

    Uma rodada em que uma fonte se absteve não pode entrar só para as
    outras: isso deslocaria as séries umas em relação às outras.
    """
    h = HistoricoOpinioes()
    h.registrar({"a": 1.0, "b": 2.0})
    h.registrar({"a": 3.0})                 # b se absteve
    h.registrar({"a": 5.0, "b": 6.0})
    s = h.series(["a", "b"])
    assert s == {"a": [1.0, 5.0], "b": [2.0, 6.0]}


def test_historico_respeita_o_limite():
    h = HistoricoOpinioes(maximo=10)
    for i in range(50):
        h.registrar({"a": float(i)})
    assert h.n_rodadas == 10
    assert h.series(["a"])["a"][0] == 40.0


def test_historico_ignora_rodada_vazia():
    h = HistoricoOpinioes()
    h.registrar({})
    assert h.n_rodadas == 0


def test_historico_sem_dados_nao_explode():
    assert HistoricoOpinioes().avaliar().fontes == []


# ----------------------------------------------- integração com o consenso
def test_consenso_sem_historico_funciona_igual():
    """A capacidade nova não pode ser requisito para o que já funcionava."""
    from investai.agents import ChiefInvestmentEngine
    chief = ChiefInvestmentEngine()
    assert chief.historico_opinioes is None


def test_consenso_reporta_redundancia_material():
    """Com fontes que se movem juntas, o consenso tem de dizer isso.

    E tem de dizer como contraindicação, não descontando o score em
    silêncio: um score descontado em segredo é irreproduzível na mão.
    """
    from investai.agents.base import (
        AgenteBase, ContextoAnalise, ParecerAgente, postura_de_valor,
    )
    from investai.agents.chief import ChiefInvestmentEngine
    from investai.data import AssetClass

    class Gemeo(AgenteBase):
        """Agente que sempre devolve o valor que lhe mandarem."""

        def __init__(self, nome, peso, fonte):
            self.nome = nome
            self.peso = peso
            self._fonte = fonte
            self._i = 0

        def analisar(self, ctx, direcao):
            v = self._fonte[self._i % len(self._fonte)]
            self._i += 1
            return ParecerAgente(agente=self.nome,
                                 postura=postura_de_valor(v), valor=v,
                                 confianca=0.9, peso_base=self.peso)

    base = [min(1.0, max(-1.0, x / 3)) for x in ruido(170)]
    agentes = [Gemeo("a", 0.3, base), Gemeo("b", 0.3, list(base)),
               Gemeo("c", 0.3, [min(1.0, max(-1.0, x / 3))
                                for x in ruido(171)])]
    chief = ChiefInvestmentEngine(agentes,
                                  historico_opinioes=HistoricoOpinioes())
    ctx = ContextoAnalise(symbol="BTCUSDT", asset_class=AssetClass.CRIPTO,
                          timeframe="1H")
    for _ in range(N):
        c = chief.consolidar(ctx, +1)

    assert c.redundancia is not None
    assert c.redundancia.material
    assert ["a", "b"] in c.redundancia.grupos
    assert any("redundância" in f for f in c.fatores_contrarios)
    assert "redundancia" in c.to_dict()
    assert c.to_dict()["redundancia"]["fator"] < FATOR_ALERTA
