"""Métricas por segmento, com origens que nunca se misturam.

Duas coisas estão sob teste. Primeiro, que backtest, paper e real nunca
entram na mesma média — a média dos três é sempre melhor que a real, porque
as fontes otimistas costumam ter mais operações. Segundo, que a quebra por
segmento revela o que a média esconde.
"""
import random

import pytest

from investai.models import Side, Trade
from investai.reporting.segmentado import (
    MIN_SEGMENTO, Metrica, Origem, OrigemMisturada, REALISMO, TradeAnotado,
    agregar, calcular, comparar_grupos, comparar_origens, separar_por_origem,
)


def t(r, regime="lateral", origem=Origem.PAPER, symbol="BTCUSDT",
      timeframe="1H", estrategia="v1"):
    return TradeAnotado(
        Trade(symbol=symbol, side=Side.LONG, entry=1.0, exit=1.0, size=1.0,
              opened_at=0, closed_at=0, pnl_usd=r * 10, pnl_r=r,
              motivo_saida="alvo" if r > 0 else "stop"),
        origem=origem, regime=regime, timeframe=timeframe,
        estrategia=estrategia)


def lote(n, media, regime="lateral", origem=Origem.PAPER, seed=1, **kw):
    rng = random.Random(seed)
    return [t(rng.gauss(media, 0.4), regime, origem, **kw) for _ in range(n)]


# =====================================================================
# Métrica básica
# =====================================================================
def test_metrica_de_lista_vazia():
    m = calcular([])
    assert m.n == 0
    assert not m.conclusiva
    assert not m.positiva_com_confianca


def test_metrica_conta_ganhos_e_perdas():
    m = calcular([1.0, 2.0, -1.0, -1.0, 0.0])
    assert m.n == 5
    assert m.ganhos == 2
    assert m.perdas == 3        # zero conta como não-ganho
    assert m.expectativa_r == pytest.approx(0.2)
    assert m.melhor_r == 2.0 and m.pior_r == -1.0


def test_amostra_pequena_nao_e_conclusiva():
    assert not calcular([1.0] * (MIN_SEGMENTO - 1)).conclusiva
    assert calcular([1.0] * MIN_SEGMENTO).conclusiva


def test_media_positiva_com_piso_negativo_nao_e_afirmacao():
    """É média positiva de amostra pequena, não vantagem demonstrada."""
    rng = random.Random(3)
    m = calcular([rng.gauss(0.05, 1.0) for _ in range(40)])
    assert m.expectativa_r > 0 or m.expectativa_r < 0
    if m.ic_expectativa.inferior <= 0:
        assert not m.positiva_com_confianca


def test_vantagem_clara_e_afirmavel():
    rng = random.Random(4)
    m = calcular([rng.gauss(0.5, 0.2) for _ in range(200)])
    assert m.positiva_com_confianca
    assert m.ic_expectativa.inferior > 0


# =====================================================================
# Origens nunca se misturam
# =====================================================================
def test_origem_misturada_e_recusada():
    """Recusa, não aviso: um aviso seria ignorado uma vez e depois sempre."""
    misto = lote(20, 0.3, origem=Origem.BACKTEST) + lote(20, 0.0,
                                                         origem=Origem.LIVE)
    with pytest.raises(OrigemMisturada, match="mistura origens"):
        agregar(misto, "regime")


def test_mensagem_explica_por_que_misturar_engana():
    misto = lote(5, 0.3, origem=Origem.BACKTEST) + lote(5, 0.0,
                                                        origem=Origem.PAPER)
    with pytest.raises(OrigemMisturada) as exc:
        agregar(misto, "regime")
    assert "sempre melhor que a real" in str(exc.value)


def test_lote_vazio_e_recusado():
    with pytest.raises(OrigemMisturada, match="nenhum trade"):
        agregar([], "regime")


def test_separar_por_origem_e_o_passo_obrigatorio():
    misto = lote(10, 0.3, origem=Origem.BACKTEST) + lote(5, 0.0,
                                                         origem=Origem.LIVE)
    partes = separar_por_origem(misto)
    assert set(partes) == {Origem.BACKTEST, Origem.LIVE}
    assert len(partes[Origem.BACKTEST]) == 10
    # E cada parte agrega sem reclamar.
    assert agregar(partes[Origem.LIVE], "regime").origem is Origem.LIVE


def test_relatorio_carimba_a_origem():
    d = agregar(lote(40, 0.2, origem=Origem.SHADOW), "regime").to_dict()
    assert d["origem"] == "shadow"
    assert "NÃO são somados" in d["observacao"]


def test_origem_nao_real_recebe_ressalva():
    r = agregar(lote(40, 0.2, origem=Origem.BACKTEST), "regime")
    assert any("Só resultado em dinheiro real" in a for a in r.avisos)


def test_origem_real_nao_recebe_ressalva():
    r = agregar(lote(40, 0.2, origem=Origem.LIVE), "regime")
    assert not any("Só resultado em dinheiro real" in a for a in r.avisos)


def test_ordem_de_realismo_vai_do_backtest_ao_real():
    assert (REALISMO[Origem.BACKTEST] < REALISMO[Origem.PAPER]
            < REALISMO[Origem.DEMO] < REALISMO[Origem.LIVE])


# =====================================================================
# Segmentação
# =====================================================================
def test_segmenta_por_regime():
    ams = lote(40, 0.5, "tendencia", seed=1) + lote(40, -0.2, "lateral",
                                                    seed=2)
    r = agregar(ams, "regime")
    assert set(r.segmentos) == {"tendencia", "lateral"}
    assert r.segmentos["tendencia"].expectativa_r > 0
    assert r.segmentos["lateral"].expectativa_r < 0


def test_segmenta_por_par():
    ams = (lote(40, 0.3, symbol="BTCUSDT", seed=1)
           + lote(40, -0.1, symbol="ETHUSDT", seed=2))
    r = agregar(ams, "symbol")
    assert set(r.segmentos) == {"BTCUSDT", "ETHUSDT"}


def test_segmenta_por_timeframe():
    ams = (lote(40, 0.3, timeframe="1H", seed=1)
           + lote(40, -0.1, timeframe="4H", seed=2))
    r = agregar(ams, "timeframe")
    assert set(r.segmentos) == {"1H", "4H"}


def test_segmenta_por_funcao():
    def por_lado(a):
        return a.trade.side.value
    r = agregar(lote(40, 0.2), por_lado)
    assert set(r.segmentos) == {"long"}
    assert r.chave == "por_lado"


def test_valor_ausente_vira_rotulo_explicito():
    ams = lote(40, 0.2, regime="")
    r = agregar(ams, "regime")
    assert "(sem valor)" in r.segmentos


def test_total_e_a_media_ponderada_dos_segmentos():
    """Fato que o módulo depende: por isso 'positivo no total e negativo em
    todos os segmentos' é impossível, e a checagem certa é outra."""
    ams = lote(50, 0.4, "a", seed=1) + lote(30, -0.3, "b", seed=2)
    r = agregar(ams, "regime")
    esperado = sum(m.n * m.expectativa_r for m in r.segmentos.values()) / r.total.n
    assert r.total.expectativa_r == pytest.approx(esperado)


def test_segmento_pequeno_aparece_marcado_nao_escondido():
    ams = lote(40, 0.3, "grande", seed=1) + lote(5, -0.9, "pequeno", seed=2)
    r = agregar(ams, "regime")
    assert "pequeno" in r.segmentos
    assert not r.segmentos["pequeno"].conclusiva
    assert "pequeno" not in r.conclusivos
    assert any("descrevem a amostra" in a for a in r.avisos)


def test_nenhum_segmento_conclusivo_e_dito():
    r = agregar(lote(10, 0.3, "a", seed=1) + lote(10, 0.3, "b", seed=2),
                "regime")
    assert not r.conclusivos
    assert any("não sustenta nenhuma conclusão" in a for a in r.avisos)


def test_aponta_onde_funciona_e_onde_nao():
    ams = lote(120, 0.6, "tendencia", seed=1) + lote(120, -0.4, "lateral",
                                                     seed=2)
    r = agregar(ams, "regime")
    assert any("operar só onde ela funciona" in a for a in r.avisos)


# =====================================================================
# Composição: a média vale enquanto a mistura se repetir
# =====================================================================
def test_composicao_reporta_a_mistura_medida():
    ams = lote(30, 0.2, "a", seed=1) + lote(90, 0.2, "b", seed=2)
    r = agregar(ams, "regime")
    assert r.composicao["a"] == pytest.approx(0.25)
    assert r.composicao["b"] == pytest.approx(0.75)


def test_dispersao_alta_vira_aviso():
    ams = lote(60, 0.6, "tendencia", seed=1) + lote(60, -0.3, "lateral",
                                                    seed=2)
    r = agregar(ams, "regime")
    assert r.dispersao_entre_segmentos > 0.30
    assert any("só vale enquanto a mistura" in a for a in r.avisos)


def test_reponderar_muda_a_expectativa():
    """Se o mercado virar, o número que vale é outro.

    É a diferença entre "esta estratégia tem 0,5R" e "esta estratégia teve
    0,5R na mistura de regimes que aconteceu".
    """
    ams = lote(40, 0.1, "lateral", seed=1) + lote(160, 0.6, "tendencia",
                                                  seed=2)
    r = agregar(ams, "regime")
    medido = r.total.expectativa_r
    invertido = r.reponderar({"lateral": 0.8, "tendencia": 0.2})
    assert invertido is not None
    assert invertido < medido - 0.2


def test_reponderar_com_segmento_sem_amostra_devolve_none():
    """Não inventa número para segmento que não foi medido."""
    r = agregar(lote(40, 0.3, "a", seed=1), "regime")
    assert r.reponderar({"nao_existe": 1.0}) is None


def test_reponderar_sem_composicao_devolve_none():
    r = agregar(lote(40, 0.3, "a", seed=1), "regime")
    assert r.reponderar({}) is None


def test_reponderar_normaliza_pesos():
    ams = lote(40, 0.2, "a", seed=1) + lote(40, 0.6, "b", seed=2)
    r = agregar(ams, "regime")
    assert r.reponderar({"a": 1, "b": 1}) == pytest.approx(
        r.reponderar({"a": 50, "b": 50}))


def test_dispersao_precisa_de_dois_segmentos_conclusivos():
    assert agregar(lote(40, 0.3, "unico", seed=1),
                   "regime").dispersao_entre_segmentos == 0.0


# =====================================================================
# Comparação entre origens
# =====================================================================
def test_comparacao_mostra_a_degradacao():
    misto = (lote(100, 0.4, origem=Origem.BACKTEST, seed=1)
             + lote(60, 0.05, origem=Origem.PAPER, seed=2)
             + lote(40, -0.02, origem=Origem.LIVE, seed=3))
    c = comparar_origens(misto)
    assert [l["origem"] for l in c["linhas"]] == ["backtest", "paper", "live"]
    assert c["origem_mais_real"] == "live"
    assert len(c["degradacoes"]) == 2
    assert c["degradacoes"][0]["de"] == "backtest"


def test_comparacao_diz_qual_numero_conta():
    misto = (lote(100, 0.4, origem=Origem.BACKTEST, seed=1)
             + lote(60, 0.05, origem=Origem.PAPER, seed=2))
    c = comparar_origens(misto)
    assert c["origem_mais_real"] == "paper"
    assert c["expectativa_que_conta"] == c["linhas"][-1]["expectativa_r"]
    assert "não tem 0,2R" in c["observacao"]


def test_comparacao_com_uma_origem_so():
    c = comparar_origens(lote(40, 0.3, origem=Origem.LIVE))
    assert len(c["linhas"]) == 1
    assert c["degradacoes"] == []


# =====================================================================
# Paradoxo de Simpson — onde ele de fato existe
# =====================================================================
def test_detecta_paradoxo_de_simpson_entre_grupos():
    """A ganha no total e perde em TODOS os segmentos.

    Acontece quando os dois foram medidos em misturas diferentes de
    mercado: o total compara a sorte da composição, não as estratégias.
    Decidir pelo total escolheria a pior das duas em qualquer cenário.
    """
    A = lote(40, 0.10, "lateral", seed=1) + lote(160, 0.60, "tendencia",
                                                  seed=2)
    B = lote(160, 0.25, "lateral", seed=3) + lote(40, 0.75, "tendencia",
                                                  seed=4)
    c = comparar_grupos(A, B, "regime", nome_a="A", nome_b="B")
    assert c["vence_no_total"] == "A"
    assert all(l["vence"] == "B" for l in c["por_segmento"])
    assert c["paradoxo_de_simpson"]
    assert any("PARADOXO DE SIMPSON" in a for a in c["avisos"])


def test_sem_paradoxo_quando_o_vencedor_e_o_mesmo():
    A = lote(100, 0.6, "lateral", seed=1) + lote(100, 0.7, "tendencia",
                                                  seed=2)
    B = lote(100, 0.1, "lateral", seed=3) + lote(100, 0.2, "tendencia",
                                                  seed=4)
    c = comparar_grupos(A, B, "regime")
    assert not c["paradoxo_de_simpson"]
    assert c["vence_no_total"] == "A"


def test_comparar_grupos_de_origens_diferentes_e_recusado():
    """Mediria a diferença entre as fontes, não entre as estratégias."""
    A = lote(50, 0.4, origem=Origem.BACKTEST, seed=1)
    B = lote(50, 0.1, origem=Origem.LIVE, seed=2)
    with pytest.raises(OrigemMisturada, match="não as estratégias"):
        comparar_grupos(A, B, "regime")


def test_comparacao_sem_segmento_comum_e_dita():
    A = lote(50, 0.4, "so_no_a", seed=1)
    B = lote(50, 0.1, "so_no_b", seed=2)
    c = comparar_grupos(A, B, "regime")
    assert c["segmentos_comparaveis"] == 0
    assert any("não pôde ser feita" in a for a in c["avisos"])


def test_comparacao_expoe_as_composicoes():
    A = lote(40, 0.1, "lateral", seed=1) + lote(160, 0.6, "tendencia", seed=2)
    B = lote(160, 0.25, "lateral", seed=3) + lote(40, 0.75, "tendencia",
                                                   seed=4)
    c = comparar_grupos(A, B, "regime")
    assert c["composicao_a"]["tendencia"] == pytest.approx(0.8)
    assert c["composicao_b"]["tendencia"] == pytest.approx(0.2)


def test_metrica_serializa():
    d = Metrica().to_dict()
    assert {"n", "win_rate", "expectativa_r", "conclusiva",
            "positiva_com_confianca"} <= set(d)
