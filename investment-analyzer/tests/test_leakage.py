"""Auditoria automática de vazamento de informação futura.

Este arquivo tem duas metades, e as duas são necessárias:

1. funções deliberadamente defeituosas, para provar que o auditor DETECTA
   vazamento. Sem elas, um "LIMPO" no código de produção não significaria
   nada — poderia ser um auditor que nunca acha nada;
2. o código de produção, auditado de verdade.
"""
import pytest

from investai.analysis.features import SerieFeatures, extrair_features
from investai.exchanges import SyntheticProvider
from investai.ml.amostras import rotular
from investai.ml.modelo import Padronizador
from investai.models import Candle, Side
from investai.validation.leakage import (
    FATOR_SENTINELA, auditar_divisao_temporal,
    auditar_invariancia_de_prefixo, auditar_ordem_temporal,
    auditar_padronizacao, auditar_rotulagem, auditar_sentinela, juntar,
)

HORA = 3_600_000
TS0 = 1_700_000_000_000


@pytest.fixture(scope="module")
def velas():
    p = SyntheticProvider(seed=5, agora_ms=1_726_000_000_000)
    return p.candles("BTCUSDT", "1H", limit=1200)


# =====================================================================
# METADE 1: o auditor pega vazamento?
# =====================================================================
def honesta(vs, i):
    """Só usa a fatia até i. É o comportamento correto."""
    janela = vs[:i + 1]
    return {"media": sum(c.close for c in janela[-20:]) / 20,
            "ultimo": janela[-1].close}


def olha_a_ultima_barra(vs, i):
    """Defeito clássico: usa `vs[-1]` em vez de `vs[i]`."""
    return {"media": sum(c.close for c in vs[:i + 1][-20:]) / 20,
            "ultimo": vs[-1].close}


def olha_uma_barra_adiante(vs, i):
    """Defeito sutil: um índice a mais no fim da fatia."""
    janela = vs[:i + 2]
    return {"media": sum(c.close for c in janela[-20:]) / 20,
            "ultimo": janela[-1].close}


def normaliza_pela_serie_toda(vs, i):
    """Defeito silencioso: escala usando o máximo da série completa.

    Este é o pior tipo, porque o resultado continua parecendo uma feature
    normalizada razoável.
    """
    maximo = max(c.high for c in vs)
    return {"preco_rel": vs[i].close / maximo}


def test_funcao_honesta_passa(velas):
    r = auditar_invariancia_de_prefixo(honesta, velas, alvo="honesta")
    assert r.veredicto == "LIMPO"
    assert r.verificacoes == 6


def test_pega_uso_da_ultima_barra(velas):
    r = auditar_invariancia_de_prefixo(olha_a_ultima_barra, velas)
    assert r.veredicto == "VAZAMENTO"
    assert r.violacoes[0].tipo == "prefixo"
    assert "ultimo" in r.violacoes[0].detalhe


def test_pega_uma_barra_adiante(velas):
    """Um índice a mais na fatia é o erro mais fácil de cometer."""
    r = auditar_invariancia_de_prefixo(olha_uma_barra_adiante, velas)
    assert r.veredicto == "VAZAMENTO"


def test_pega_normalizacao_pela_serie_completa(velas):
    """Normalizar pelo máximo global é o vazamento mais silencioso.

    E é o caso que mostra por que as duas checagens são necessárias: nesta
    série o máximo global ocorre no começo, então o máximo do prefixo é igual
    ao total em todo índice testado, e a invariância de prefixo PASSA por
    coincidência do dado. A sentinela força a diferença a aparecer.
    """
    prefixo = auditar_invariancia_de_prefixo(normaliza_pela_serie_toda, velas)
    sentinela = auditar_sentinela(normaliza_pela_serie_toda, velas)
    assert sentinela.veredicto == "VAZAMENTO"
    assert "preco_rel" in sentinela.violacoes[0].detalhe
    # A auditoria conjunta é o que se usa de verdade, e ela pega.
    assert juntar(prefixo, sentinela).veredicto == "VAZAMENTO"


def test_sentinela_pega_o_mesmo(velas):
    r = auditar_sentinela(olha_a_ultima_barra, velas)
    assert r.veredicto == "VAZAMENTO"
    assert r.violacoes[0].tipo == "sentinela"
    assert str(FATOR_SENTINELA) in r.violacoes[0].detalhe


def test_sentinela_aprova_a_honesta(velas):
    assert auditar_sentinela(honesta, velas).veredicto == "LIMPO"


def test_sentinela_pega_o_que_o_prefixo_poderia_perder():
    """Série constante: prefixo e completo coincidem por acaso do dado.

    Com todos os preços iguais, usar `vs[-1]` em vez de `vs[i]` não muda
    nada, e a invariância de prefixo passa. A sentinela força a diferença a
    aparecer.
    """
    constantes = [Candle(ts=TS0 + i * HORA, open=100, high=100, low=100,
                         close=100, volume=1) for i in range(200)]
    assert auditar_invariancia_de_prefixo(
        olha_a_ultima_barra, constantes).veredicto == "LIMPO"
    assert auditar_sentinela(
        olha_a_ultima_barra, constantes).veredicto == "VAZAMENTO"


def test_serie_curta_nao_e_aprovada_por_omissao():
    """Não poder testar é diferente de passar no teste."""
    curta = [Candle(ts=TS0 + i * HORA, open=1, high=1, low=1, close=1,
                    volume=1) for i in range(5)]
    r = auditar_invariancia_de_prefixo(honesta, curta)
    assert r.veredicto == "NAO_TESTADO"
    assert not r.limpo
    assert r.nao_testado


def test_funcao_que_explode_entra_como_nao_testada(velas):
    def quebra(vs, i):
        raise RuntimeError("erro de propósito")
    r = auditar_invariancia_de_prefixo(quebra, velas)
    assert r.veredicto == "NAO_TESTADO"
    assert len(r.nao_testado) >= 1


def test_indices_podem_ser_escolhidos(velas):
    r = auditar_invariancia_de_prefixo(honesta, velas, indices=[300, 400])
    assert r.verificacoes == 2


def test_relatorio_nao_promete_teorema(velas):
    d = auditar_invariancia_de_prefixo(honesta, velas).to_dict()
    assert "uma rede, não um teorema" in d["observacao"]


# ------------------------------------------------------------- rotulagem
def rotulador_honesto(vs, i):
    entry = vs[i].close
    return rotular(vs, i, side=Side.LONG, entry=entry, stop=entry * 0.98,
                   alvo=entry * 1.04, max_barras=48)


def rotulador_que_usa_a_barra_da_decisao(vs, i):
    """Inclui a barra `i`, cujo máximo já é informação do futuro."""
    entry = vs[i].close
    stop, alvo = entry * 0.98, entry * 1.04
    for n, v in enumerate(vs[i: i + 49], start=0):
        if v.low <= stop:
            return 0, n
        if v.high >= alvo:
            return 1, n
    return None, 48


def rotulador_sem_horizonte(vs, i):
    """Procura o desfecho até o fim da série, ignorando o horizonte."""
    entry = vs[i].close
    stop, alvo = entry * 0.98, entry * 1.04
    for n, v in enumerate(vs[i + 1:], start=1):
        if v.low <= stop:
            return 0, n
        if v.high >= alvo:
            return 1, n
    return None, len(vs) - i - 1


def test_rotulador_honesto_passa(velas):
    r = auditar_rotulagem(rotulador_honesto, velas, horizonte=48)
    assert r.veredicto == "LIMPO"


def test_pega_rotulador_que_usa_a_barra_da_decisao(velas):
    """A barra que gerou o sinal não pode rotular a si mesma."""
    r = auditar_rotulagem(rotulador_que_usa_a_barra_da_decisao, velas,
                          horizonte=48)
    assert r.veredicto == "VAZAMENTO"
    assert any(v.tipo == "barra_da_decisao" for v in r.violacoes)
    assert any("dentro da vela que originou o sinal" in v.detalhe
               for v in r.violacoes)


def test_pega_rotulador_que_ignora_o_horizonte():
    """Usar mais futuro do que declara torna o rótulo incomparável.

    A execução real desiste depois do horizonte; um rótulo que espera
    indefinidamente mede outra regra.

    Esta violação só é visível em dado onde o horizonte MORDE. Na série
    sintética todo desfecho ocorre em cerca de 14 barras contra um horizonte
    de 48, e o rotulador defeituoso passa — o que é um limite real da
    checagem, documentado no módulo. Aqui a série é construída para ficar
    parada além do horizonte e só então andar.
    """
    parada = [Candle(ts=TS0 + i * HORA, open=100.0, high=100.2, low=99.8,
                     close=100.0, volume=1.0) for i in range(80)]
    # A partir de i=10, o alvo (104) só é tocado na barra 70 — bem além do
    # horizonte de 20.
    movimento = [Candle(ts=TS0 + (80 + i) * HORA, open=100.0, high=106.0,
                        low=99.9, close=105.0, volume=1.0) for i in range(40)]
    serie = parada + movimento

    def rot(vs, i):
        # Entrada fixa em 100 para que o plano seja o mesmo em todo índice
        # testado; o que varia aqui é só quando o alvo é tocado.
        stop, alvo = 98.0, 104.0
        for n, v in enumerate(vs[i + 1:], start=1):
            if v.low <= stop:
                return 0, n
            if v.high >= alvo:
                return 1, n
        return None, len(vs) - i - 1

    r = auditar_rotulagem(rot, serie, horizonte=20, indices=[10, 20])
    assert r.veredicto == "VAZAMENTO"
    assert any(v.tipo == "horizonte" for v in r.violacoes)


def test_rotulagem_com_serie_curta_nao_e_testada():
    curta = [Candle(ts=TS0 + i * HORA, open=1, high=1, low=1, close=1,
                    volume=1) for i in range(20)]
    r = auditar_rotulagem(rotulador_honesto, curta, horizonte=48)
    assert r.veredicto == "NAO_TESTADO"


# ----------------------------------------------------- divisão temporal
def test_divisao_sem_sobreposicao_passa():
    treino = [TS0 + i * HORA for i in range(100)]
    teste = [TS0 + (100 + i) * HORA for i in range(50)]
    assert auditar_divisao_temporal(treino, teste).veredicto == "LIMPO"


def test_pega_sobreposicao_temporal():
    """O número otimista é indistinguível de um número válido.

    É o que torna este erro tão perigoso: nada na saída denuncia.
    """
    treino = [TS0 + i * HORA for i in range(100)]
    teste = [TS0 + (80 + i) * HORA for i in range(50)]
    r = auditar_divisao_temporal(treino, teste)
    assert r.veredicto == "VAZAMENTO"
    assert any(v.tipo == "sobreposicao" for v in r.violacoes)
    assert any(v.tipo == "instantes_repetidos" for v in r.violacoes)


def test_pega_instante_repetido_sem_sobreposicao_de_faixa():
    """Uma única observação em comum já é contaminação."""
    treino = [TS0, TS0 + HORA, TS0 + 500 * HORA]
    teste = [TS0 + 400 * HORA, TS0 + 500 * HORA]
    r = auditar_divisao_temporal(treino, teste)
    assert r.veredicto == "VAZAMENTO"


def test_divisao_com_janela_vazia_nao_e_testada():
    assert auditar_divisao_temporal([], [TS0]).veredicto == "NAO_TESTADO"


# ------------------------------------------------------- ordem temporal
def test_serie_ordenada_passa():
    assert auditar_ordem_temporal(
        [TS0 + i * HORA for i in range(50)]).veredicto == "LIMPO"


def test_pega_serie_fora_de_ordem():
    """Sem ordem, `velas[:i+1]` deixa de significar 'tudo até i'."""
    ts = [TS0 + i * HORA for i in range(50)]
    ts[20], ts[30] = ts[30], ts[20]
    r = auditar_ordem_temporal(ts)
    assert r.veredicto == "VAZAMENTO"
    assert "fatiar por índice não significa fatiar por tempo" in \
        r.violacoes[0].detalhe


def test_pega_instante_duplicado_na_serie():
    ts = [TS0, TS0 + HORA, TS0 + HORA, TS0 + 2 * HORA]
    assert auditar_ordem_temporal(ts).veredicto == "VAZAMENTO"


# --------------------------------------------------------- padronização
def test_padronizacao_do_treino_passa():
    xs = [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]]
    pad = Padronizador.ajustar(xs)
    assert auditar_padronizacao(pad.medias, xs).veredicto == "LIMPO"


def test_pega_padronizacao_com_o_conjunto_completo():
    """A média do teste entrando no treino melhora as métricas em silêncio."""
    treino = [[1.0], [2.0], [3.0]]
    completo = treino + [[100.0], [200.0]]
    pad_errado = Padronizador.ajustar(completo)
    r = auditar_padronizacao(pad_errado.medias, treino)
    assert r.veredicto == "VAZAMENTO"
    assert "vieram de outro conjunto" in r.violacoes[0].detalhe


def test_padronizacao_sem_dados_nao_e_testada():
    assert auditar_padronizacao([], []).veredicto == "NAO_TESTADO"


# ------------------------------------------------------------- conjunto
def test_juntar_preserva_a_origem(velas):
    a = auditar_invariancia_de_prefixo(olha_a_ultima_barra, velas,
                                       alvo="quebrada")
    b = auditar_invariancia_de_prefixo(honesta, velas, alvo="boa")
    t = juntar(a, b)
    assert t.veredicto == "VAZAMENTO"
    assert "quebrada" in t.violacoes[0].onde
    assert t.verificacoes == a.verificacoes + b.verificacoes


def test_texto_do_relatorio_lista_as_violacoes(velas):
    texto = auditar_invariancia_de_prefixo(olha_a_ultima_barra,
                                           velas).texto()
    assert "VAZAMENTO" in texto
    assert "prefixo" in texto


# =====================================================================
# METADE 2: o código de produção
# =====================================================================
def test_serie_features_nao_olha_adiante(velas):
    """`SerieFeatures` pré-calcula tudo de uma vez; é onde lookahead cabe.

    Ela existe porque recalcular indicadores por índice torna o backtest
    O(N²). O ganho de desempenho é exatamente o tipo de otimização que
    introduz vazamento sem parecer errada.
    """
    def at(vs, i):
        return SerieFeatures("BTCUSDT", "1H", vs).at(i)
    r = juntar(auditar_invariancia_de_prefixo(at, velas, alvo="SerieFeatures"),
               auditar_sentinela(at, velas, alvo="SerieFeatures"))
    assert r.veredicto == "LIMPO", r.texto()
    assert r.verificacoes >= 6


def test_extrair_features_nao_olha_adiante(velas):
    def extrai(vs, i):
        return extrair_features("BTCUSDT", "1H", vs, indice=i)
    r = juntar(auditar_invariancia_de_prefixo(extrai, velas,
                                              alvo="extrair_features"),
               auditar_sentinela(extrai, velas, alvo="extrair_features"))
    assert r.veredicto == "LIMPO", r.texto()


def test_os_dois_caminhos_de_features_concordam(velas):
    """`SerieFeatures.at` e `extrair_features` têm de dar o mesmo resultado.

    Se divergissem, um dos dois estaria errado — e o backtest usaria um
    enquanto o painel usa o outro.
    """
    serie = SerieFeatures("BTCUSDT", "1H", velas)
    for i in (400, 700, 1000):
        a = serie.at(i)
        b = extrair_features("BTCUSDT", "1H", velas, indice=i)
        assert a.close == pytest.approx(b.close)
        assert a.rsi == pytest.approx(b.rsi, abs=1e-9)
        assert a.atr == pytest.approx(b.atr, abs=1e-9)
        assert a.macd_hist == pytest.approx(b.macd_hist, abs=1e-9)
        assert a.adx == pytest.approx(b.adx, abs=1e-9)


def test_rotulador_de_producao_nao_vaza(velas):
    r = auditar_rotulagem(rotulador_honesto, velas, horizonte=48,
                          alvo="ml.amostras.rotular")
    assert r.veredicto == "LIMPO", r.texto()


def test_provider_sintetico_entrega_serie_ordenada(velas):
    r = auditar_ordem_temporal([c.ts for c in velas], alvo="provider")
    assert r.veredicto == "LIMPO"


def test_divisao_do_ml_e_temporal():
    """`dividir_no_tempo` não pode deixar sobreposição."""
    from investai.ml.amostras import (
        Amostra, ConjuntoAmostras, FEATURES, dividir_no_tempo,
    )
    ams = [Amostra(ts=TS0 + i * HORA, indice=i, x=[0.0] * len(FEATURES),
                   y=i % 2, symbol="X", side="long", barras_ate_desfecho=1,
                   entry=1, stop=0.9, alvo=1.2) for i in range(200)]
    tr, te = dividir_no_tempo(ConjuntoAmostras(ams))
    r = auditar_divisao_temporal([a.ts for a in tr.amostras],
                                 [a.ts for a in te.amostras],
                                 alvo="dividir_no_tempo")
    assert r.veredicto == "LIMPO"


def test_padronizador_do_ml_usa_so_o_treino():
    from investai.ml.amostras import (
        Amostra, ConjuntoAmostras, FEATURES, dividir_no_tempo,
    )
    import random
    rng = random.Random(3)
    ams = [Amostra(ts=TS0 + i * HORA, indice=i,
                   x=[rng.gauss(0, 1) for _ in range(len(FEATURES))],
                   y=i % 2, symbol="X", side="long", barras_ate_desfecho=1,
                   entry=1, stop=0.9, alvo=1.2) for i in range(400)]
    tr, _ = dividir_no_tempo(ConjuntoAmostras(ams))
    pad = Padronizador.ajustar(tr.xs)
    r = auditar_padronizacao(pad.medias, tr.xs, alvo="Padronizador")
    assert r.veredicto == "LIMPO"
