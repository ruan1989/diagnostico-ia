"""Camada de ML: amostras, modelo, registro e o agente.

O fio condutor destes testes é uma regra só: **probabilidade sem calibração
medida fora da amostra não sai daqui**. Vários testes existem só para
garantir que não há caminho lateral que devolva a saída crua do logístico.
"""
import random

import pytest

from investai.agents.base import ContextoAnalise, Postura
from investai.agents.ml import AgenteML, limiar_neutro
from investai.data import AssetClass
from investai.exchanges import SyntheticProvider
from investai.ml import (
    FEATURES, Amostra, ConjuntoAmostras, Hiperparametros, MIN_TREINO,
    ModelRegistry, ModeloProbabilidade, RegistroError, TreinoError,
    dividir_no_tempo, hash_modelo, montar_amostras, rotular, sigmoide,
    treinar, validar, vetor_features,
)
from investai.models import Candle, Side

TS0 = 1_600_000_000_000
HORA = 3_600_000


# =====================================================================
# Amostras: rótulo vem SEMPRE de barras posteriores à decisão
# =====================================================================
def velas_de(precos, ts0=TS0):
    return [Candle(ts=ts0 + i * HORA, open=p, high=p, low=p, close=p,
                   volume=100.0) for i, p in enumerate(precos)]


def test_rotulo_ignora_a_vela_da_decisao():
    """A barra que gerou o sinal não pode rotular a si mesma.

    O máximo dela já é informação do futuro no instante da decisão. Aqui a
    vela `i` toca o alvo e as seguintes tocam o stop: o rótulo correto é 0.
    """
    velas = [
        Candle(ts=TS0, open=100, high=100, low=100, close=100, volume=1),
        # Vela da decisão: tocaria o alvo se fosse considerada.
        Candle(ts=TS0 + HORA, open=100, high=130, low=100, close=100, volume=1),
        Candle(ts=TS0 + 2 * HORA, open=100, high=101, low=80, close=85, volume=1),
    ]
    y, barras = rotular(velas, 1, side=Side.LONG, entry=100, stop=90,
                        alvo=120, max_barras=10)
    assert y == 0, "a vela da decisão foi usada para rotular"
    assert barras == 1


def test_alvo_antes_do_stop_e_rotulo_um():
    velas = velas_de([100, 100]) + [
        Candle(ts=TS0 + 2 * HORA, open=100, high=125, low=99, close=120, volume=1)]
    y, _ = rotular(velas, 1, side=Side.LONG, entry=100, stop=90, alvo=120,
                   max_barras=10)
    assert y == 1


def test_empate_na_mesma_vela_resolve_para_o_stop():
    """Sem tick data não se sabe a ordem; supor o favorável infla o backtest."""
    velas = velas_de([100, 100]) + [
        Candle(ts=TS0 + 2 * HORA, open=100, high=125, low=85, close=100, volume=1)]
    y, _ = rotular(velas, 1, side=Side.LONG, entry=100, stop=90, alvo=120,
                   max_barras=10)
    assert y == 0


def test_sem_desfecho_no_horizonte_nao_e_rotulado():
    """Expirar não é perder.

    Rotular 0 ensinaria o modelo que ficar de lado é igual a tomar stop, o
    que enviesa a probabilidade para baixo justo quando o mercado não anda.
    """
    velas = velas_de([100] * 20)
    y, _ = rotular(velas, 1, side=Side.LONG, entry=100, stop=90, alvo=120,
                   max_barras=10)
    assert y is None


def test_short_inverte_as_barreiras():
    velas = velas_de([100, 100]) + [
        Candle(ts=TS0 + 2 * HORA, open=100, high=101, low=75, close=80, volume=1)]
    y, _ = rotular(velas, 1, side=Side.SHORT, entry=100, stop=110, alvo=80,
                   max_barras=10)
    assert y == 1


def test_montar_amostras_produz_conjunto_utilizavel():
    p = SyntheticProvider(seed=5, agora_ms=1_726_000_000_000)
    velas = p.candles("BTCUSDT", "1H", limit=2500)
    c = montar_amostras("BTCUSDT", "1H", velas, side=Side.LONG)
    assert len(c) > 500
    assert all(len(a.x) == len(FEATURES) for a in c.amostras)
    assert set(c.ys) <= {0, 1}


def test_taxa_base_bate_com_a_geometria_do_plano():
    """Com RR 2 em série quase aleatória, a taxa de acerto tende a 1/3.

    Não é um requisito do sistema: é uma checagem de sanidade do rotulador.
    Uma taxa muito acima disso denunciaria lookahead.
    """
    p = SyntheticProvider(seed=5, agora_ms=1_726_000_000_000)
    velas = p.candles("BTCUSDT", "1H", limit=3000)
    c = montar_amostras("BTCUSDT", "1H", velas, side=Side.LONG, rr=2.0)
    assert 0.20 < c.taxa_base < 0.50, (
        f"taxa base {c.taxa_base:.3f} fora do razoável para RR 2")


def test_serie_curta_devolve_conjunto_vazio_sem_explodir():
    assert len(montar_amostras("X", "1H", velas_de([100] * 50))) == 0


def test_divisao_e_temporal_nunca_sorteada():
    """Sortear misturaria passado e futuro dentro da mesma janela de indicador."""
    ams = [Amostra(ts=TS0 + i * HORA, indice=i, x=[0.0] * len(FEATURES),
                   y=i % 2, symbol="X", side="long", barras_ate_desfecho=1,
                   entry=1, stop=0.9, alvo=1.2) for i in range(100)]
    tr, te = dividir_no_tempo(ConjuntoAmostras(ams), frac_treino=0.7)
    assert len(tr) == 70 and len(te) == 30
    assert tr.intervalo_ts[1] < te.intervalo_ts[0]


def test_fracao_invalida_e_recusada():
    with pytest.raises(ValueError):
        dividir_no_tempo(ConjuntoAmostras([]), frac_treino=1.5)


def test_vetor_de_features_nao_usa_preco_absoluto():
    """Modelo treinado em dólares de BTC não serviria para ETH.

    Duas séries com a mesma forma e escalas muito diferentes têm de produzir
    o mesmo vetor.
    """
    class F:
        def __init__(self, k):
            self.close = 100.0 * k
            self.atr = 2.0 * k
            self.ema_fast = 99.0 * k
            self.ema_slow = 98.0 * k
            self.ema_trend = 95.0 * k
            self.rsi = 55.0
            self.macd_hist = 0.5 * k
            self.adx = 30.0
            self.di_plus = 25.0
            self.di_minus = 15.0
            self.bb_upper = 104.0 * k
            self.bb_lower = 96.0 * k
            self.bb_width_pct = 8.0
            self.donchian_high = 105.0 * k
            self.donchian_low = 95.0 * k
            self.volume_ratio = 1.2
            self.ret_pct_lookback = 3.0

    a = vetor_features(F(1))
    b = vetor_features(F(1000))
    assert a == pytest.approx(b, abs=1e-9)


def test_atr_zero_nao_gera_divisao_por_zero():
    class F:
        close = 100.0
        atr = 0.0
        ema_fast = ema_slow = ema_trend = 100.0
        rsi = 50.0
        macd_hist = 0.0
        adx = di_plus = di_minus = 0.0
        bb_upper = bb_lower = 100.0
        bb_width_pct = 0.0
        donchian_high = donchian_low = 100.0
        volume_ratio = 1.0
        ret_pct_lookback = 0.0
    v = vetor_features(F())
    assert all(abs(x) < 1e6 for x in v)
    assert not any(x != x for x in v)         # nenhum NaN


# =====================================================================
# Modelo
# =====================================================================
def conjunto_com_sinal(n, ts0, seed=3):
    """Amostras em que só as duas primeiras features informam."""
    rng = random.Random(seed)
    ams = []
    for k in range(n):
        x = [rng.gauss(0, 1) for _ in range(len(FEATURES))]
        z = -0.4 + 1.1 * x[0] - 0.8 * x[1]
        p = sigmoide(z)
        ams.append(Amostra(ts=ts0 + k * HORA, indice=k, x=x,
                           y=1 if rng.random() < p else 0, symbol="X",
                           side="long", barras_ate_desfecho=5,
                           entry=1.0, stop=0.9, alvo=1.2))
    return ConjuntoAmostras(ams)


def conjunto_sem_sinal(n, ts0, seed=9):
    rng = random.Random(seed)
    ams = []
    for k in range(n):
        x = [rng.gauss(0, 1) for _ in range(len(FEATURES))]
        ams.append(Amostra(ts=ts0 + k * HORA, indice=k, x=x,
                           y=1 if rng.random() < 0.35 else 0, symbol="X",
                           side="long", barras_ate_desfecho=5,
                           entry=1.0, stop=0.9, alvo=1.2))
    return ConjuntoAmostras(ams)


def test_sigmoide_estavel_nos_extremos():
    assert sigmoide(-1000) == pytest.approx(0.0, abs=1e-12)
    assert sigmoide(1000) == pytest.approx(1.0, abs=1e-12)
    assert sigmoide(0) == pytest.approx(0.5)


def test_treino_recusa_amostra_pequena():
    with pytest.raises(TreinoError, match=str(MIN_TREINO)):
        treinar(conjunto_com_sinal(50, TS0))


def test_treino_recusa_poucas_observacoes_por_parametro():
    """Folga pequena faz o modelo decorar em vez de generalizar.

    Com as 13 features atuais quem morde primeiro é `MIN_TREINO` (200 > 130).
    A regra por parâmetro existe para quando alguém acrescentar features: com
    40 delas, 400 observações passam a ser o mínimo. O teste usa um conjunto
    largo justamente para exercitar essa regra, e não a outra.
    """
    rng = random.Random(2)
    largura = 40
    ams = [Amostra(ts=TS0 + i * HORA, indice=i,
                   x=[rng.gauss(0, 1) for _ in range(largura)],
                   y=i % 2, symbol="X", side="long", barras_ate_desfecho=1,
                   entry=1, stop=0.9, alvo=1.2)
           for i in range(MIN_TREINO + 50)]
    with pytest.raises(TreinoError, match="por parâmetro"):
        treinar(ConjuntoAmostras(ams))


def test_treino_recusa_rotulo_unico():
    ams = [Amostra(ts=TS0 + i * HORA, indice=i, x=[0.1] * len(FEATURES), y=1,
                   symbol="X", side="long", barras_ate_desfecho=1,
                   entry=1, stop=0.9, alvo=1.2) for i in range(500)]
    with pytest.raises(TreinoError, match="mesmo rótulo"):
        treinar(ConjuntoAmostras(ams))


def test_modelo_recupera_o_sinal_verdadeiro():
    """Se houver estrutura, o treino tem de encontrá-la.

    Sem este teste, todos os outros seriam compatíveis com um modelo que
    nunca aprende nada e por isso nunca erra de forma visível.
    """
    m = treinar(conjunto_com_sinal(3000, TS0),
                hiper=Hiperparametros(epocas=600), symbol="X",
                timeframe="1H", side="long")
    assert m.coeficientes[0] == pytest.approx(1.1, abs=0.25)
    assert m.coeficientes[1] == pytest.approx(-0.8, abs=0.25)
    # As features irrelevantes têm de ficar perto de zero.
    assert all(abs(c) < 0.25 for c in m.coeficientes[2:])


def test_intercepto_parte_da_taxa_base():
    conj = conjunto_sem_sinal(2000, TS0)
    m = treinar(conj, hiper=Hiperparametros(epocas=50))
    p_media = sum(m.prever(a.x) for a in conj.amostras) / len(conj)
    assert p_media == pytest.approx(conj.taxa_base, abs=0.05)


def test_padronizacao_usa_so_o_treino():
    """Padronizar com o conjunto completo seria vazamento silencioso."""
    conj = conjunto_com_sinal(3000, TS0)
    tr, _ = dividir_no_tempo(conj)
    m = treinar(tr, hiper=Hiperparametros(epocas=50))
    esperado = sum(a.x[0] for a in tr.amostras) / len(tr)
    assert m.padronizador.medias[0] == pytest.approx(esperado)


def test_feature_constante_nao_quebra_padronizacao():
    rng = random.Random(1)
    ams = []
    for k in range(1000):
        x = [rng.gauss(0, 1) for _ in range(len(FEATURES))]
        x[3] = 7.0                       # feature constante
        ams.append(Amostra(ts=TS0 + k * HORA, indice=k, x=x,
                           y=1 if rng.random() < 0.4 else 0, symbol="X",
                           side="long", barras_ate_desfecho=1,
                           entry=1, stop=0.9, alvo=1.2))
    m = treinar(ConjuntoAmostras(ams), hiper=Hiperparametros(epocas=30))
    assert all(v == v for v in m.coeficientes)


def test_vetor_de_tamanho_errado_e_recusado():
    """Treinar com uma ordem de features e prever com outra é indetectável.

    O formato do vetor não denuncia a troca, então o modelo confere o
    tamanho — a única verificação barata que existe.
    """
    m = treinar(conjunto_com_sinal(1000, TS0), hiper=Hiperparametros(epocas=20))
    with pytest.raises(ValueError, match="sem sentido"):
        m.prever([0.1, 0.2])


# ------------------------------------------ a regra central: sem calibração
def test_modelo_recem_treinado_nao_esta_calibrado():
    m = treinar(conjunto_com_sinal(1000, TS0), hiper=Hiperparametros(epocas=20))
    assert not m.calibrado
    assert "sem calibração medida fora da amostra" in m.motivo_nao_calibrado


def test_prever_calibrado_devolve_none_sem_calibracao():
    """O caminho lateral que não pode existir.

    Se isto devolvesse a saída crua, todo o resto do módulo seria teatro.
    """
    m = treinar(conjunto_com_sinal(1000, TS0), hiper=Hiperparametros(epocas=20))
    assert m.prever_calibrado([0.0] * len(FEATURES)) is None
    # E a saída crua continua disponível, para quem souber o que está fazendo.
    assert 0.0 <= m.prever([0.0] * len(FEATURES)) <= 1.0


def test_validacao_dentro_do_treino_e_recusada():
    """Medir calibração em dados já vistos produz número indistinguível."""
    conj = conjunto_com_sinal(2000, TS0)
    m = treinar(conj, hiper=Hiperparametros(epocas=20))
    with pytest.raises(TreinoError, match="dentro do período de treino"):
        validar(m, conj)


def test_validacao_fora_da_amostra_calibra():
    tr = conjunto_com_sinal(3000, TS0)
    te = conjunto_com_sinal(1500, TS0 + 3000 * HORA, seed=4)
    m = treinar(tr, hiper=Hiperparametros(epocas=600), symbol="X",
                timeframe="1H", side="long")
    validar(m, te)
    assert m.calibrado
    assert m.calibracao.veredicto == "CALIBRADO"
    assert m.calibracao.brier.skill > 0.1
    p = m.prever_calibrado(te.amostras[0].x)
    assert p is not None and 0.0 <= p <= 1.0


def test_modelo_sem_sinal_nao_passa_na_calibracao():
    """Ruído puro tem de ser reprovado, não aprovado por acaso."""
    tr = conjunto_sem_sinal(3000, TS0)
    te = conjunto_sem_sinal(1500, TS0 + 3000 * HORA, seed=10)
    m = treinar(tr, hiper=Hiperparametros(epocas=300), symbol="X",
                timeframe="1H", side="long")
    validar(m, te)
    assert not m.calibrado
    assert m.calibracao.veredicto == "SEM_PODER_DISCRIMINANTE"
    assert m.prever_calibrado(te.amostras[0].x) is None


def test_validacao_avisa_que_o_mapa_foi_ajustado_ali_mesmo():
    tr = conjunto_com_sinal(3000, TS0)
    te = conjunto_com_sinal(1500, TS0 + 3000 * HORA, seed=4)
    m = validar(treinar(tr, hiper=Hiperparametros(epocas=100)), te)
    assert any("otimista" in a for a in m.calibracao.avisos)


def test_modelo_serializa_a_procedencia():
    tr = conjunto_com_sinal(3000, TS0)
    te = conjunto_com_sinal(1500, TS0 + 3000 * HORA, seed=4)
    m = validar(treinar(tr, hiper=Hiperparametros(epocas=100), symbol="BTCUSDT",
                        timeframe="1H", side="long"), te)
    d = m.to_dict()
    assert d["symbol"] == "BTCUSDT"
    assert d["periodo_treino"] == [TS0, TS0 + 2999 * HORA]
    assert d["periodo_validacao"][0] > d["periodo_treino"][1]
    assert len(d["importancias"]) == len(FEATURES)


# =====================================================================
# ModelRegistry
# =====================================================================
_CACHE_MODELOS: dict = {}


def modelo_calibrado(symbol="BTCUSDT", side="long", seed=3):
    """Modelo treinado e validado, em cache.

    O treino é determinístico (semente fixa nos hiperparâmetros e nos
    conjuntos), então repetir a mesma combinação devolve exatamente o mesmo
    modelo. Sem o cache, este arquivo passava 40 segundos refazendo o mesmo
    ajuste dezenas de vezes.

    Cada chamada devolve uma CÓPIA: vários testes mexem no modelo (zeram a
    calibração, por exemplo), e um objeto compartilhado transformaria isso
    em interferência entre testes.
    """
    import copy
    chave = (symbol, side, seed)
    if chave not in _CACHE_MODELOS:
        tr = conjunto_com_sinal(3000, TS0, seed=seed)
        te = conjunto_com_sinal(1500, TS0 + 3000 * HORA, seed=seed + 1)
        m = treinar(tr, hiper=Hiperparametros(epocas=400), symbol=symbol,
                    timeframe="1H", side=side)
        _CACHE_MODELOS[chave] = validar(m, te)
    return copy.deepcopy(_CACHE_MODELOS[chave])


def test_registrar_e_obter():
    reg = ModelRegistry()
    v = reg.registrar(modelo_calibrado(), agora_ms=TS0)
    assert v.versao == 1
    assert reg.obter(v.id) is v


def test_modelo_identico_nao_duplica_versao():
    reg = ModelRegistry()
    m = modelo_calibrado()
    a = reg.registrar(m, agora_ms=TS0)
    b = reg.registrar(m, agora_ms=TS0)
    assert a.id == b.id
    assert len(reg.listar()) == 1


def test_hiperparametro_diferente_e_outra_versao():
    """Mudar o ajuste e manter o número da versão seria a armadilha."""
    reg = ModelRegistry()
    tr = conjunto_com_sinal(3000, TS0)
    te = conjunto_com_sinal(1500, TS0 + 3000 * HORA, seed=4)
    a = validar(treinar(tr, hiper=Hiperparametros(epocas=100), symbol="X",
                        timeframe="1H", side="long"), te)
    b = validar(treinar(tr, hiper=Hiperparametros(epocas=101), symbol="X",
                        timeframe="1H", side="long"), te)
    assert hash_modelo(a) != hash_modelo(b)
    reg.registrar(a, agora_ms=TS0)
    reg.registrar(b, agora_ms=TS0)
    assert len(reg.listar()) == 2


def test_modelo_sem_par_nao_e_registrado():
    """Modelo aplicado ao ativo errado produz número plausível e falso."""
    reg = ModelRegistry()
    m = treinar(conjunto_com_sinal(1000, TS0), hiper=Hiperparametros(epocas=20))
    with pytest.raises(RegistroError, match="sem par"):
        reg.registrar(m)


def test_modelo_nao_treinado_nao_e_registrado():
    with pytest.raises(RegistroError, match="não treinado"):
        ModelRegistry().registrar(ModeloProbabilidade())


def test_promover_exige_calibracao():
    """Sem esta recusa, o registro seria um enfeite."""
    reg = ModelRegistry()
    tr = conjunto_sem_sinal(3000, TS0)
    te = conjunto_sem_sinal(1500, TS0 + 3000 * HORA, seed=10)
    m = validar(treinar(tr, hiper=Hiperparametros(epocas=100), symbol="X",
                        timeframe="1H", side="long"), te)
    v = reg.registrar(m, agora_ms=TS0)
    with pytest.raises(RegistroError, match="calibração válida"):
        reg.promover(v.id)
    assert reg.em_producao("X", "1H", "long") is None


def test_promover_troca_o_anterior():
    reg = ModelRegistry()
    a = reg.registrar(modelo_calibrado(seed=3), agora_ms=TS0)
    b = reg.registrar(modelo_calibrado(seed=21), agora_ms=TS0)
    reg.promover(a.id, agora_ms=TS0)
    reg.promover(b.id, agora_ms=TS0 + 1000)
    assert b.em_producao and not a.em_producao
    assert any(h["evento"] == "aposentado" for h in a.historico)


def test_em_producao_devolve_o_modelo_certo():
    reg = ModelRegistry()
    v = reg.registrar(modelo_calibrado(), agora_ms=TS0)
    reg.promover(v.id, agora_ms=TS0)
    assert reg.em_producao("BTCUSDT", "1H", "long") is v.modelo
    assert reg.em_producao("ETHUSDT", "1H", "long") is None
    assert reg.em_producao("BTCUSDT", "1H", "short") is None


def test_modelo_que_perde_calibracao_deixa_de_ser_servido():
    """Revalidar e reprovar tira o modelo do ar sem precisar de comando.

    É o caminho que o detector de drift vai usar: se a calibração cai, o
    número para de sair, mesmo que ninguém despromova.
    """
    reg = ModelRegistry()
    v = reg.registrar(modelo_calibrado(), agora_ms=TS0)
    reg.promover(v.id, agora_ms=TS0)
    assert reg.em_producao("BTCUSDT", "1H", "long") is not None
    v.modelo.calibracao = None
    assert reg.em_producao("BTCUSDT", "1H", "long") is None


def test_despromover_preserva_o_historico():
    reg = ModelRegistry()
    v = reg.registrar(modelo_calibrado(), agora_ms=TS0)
    reg.promover(v.id, agora_ms=TS0)
    reg.despromover(v.chave, "drift detectado", agora_ms=TS0 + 5)
    assert not v.em_producao
    assert reg.obter(v.id) is v
    assert any(h.get("motivo") == "drift detectado" for h in v.historico)


def test_resumo_do_registro():
    reg = ModelRegistry()
    v = reg.registrar(modelo_calibrado(), agora_ms=TS0)
    reg.promover(v.id, agora_ms=TS0)
    r = reg.resumo()
    assert r["total"] == 1 and r["calibrados"] == 1 and r["em_producao"] == 1
    assert "BTCUSDT|1H|long" in r["chaves"]


# =====================================================================
# Agente ML
# =====================================================================
class FeaturesFalsas:
    close = 100.0
    atr = 2.0
    ema_fast = 99.0
    ema_slow = 98.0
    ema_trend = 95.0
    rsi = 60.0
    macd_hist = 0.5
    adx = 30.0
    di_plus = 25.0
    di_minus = 15.0
    bb_upper = 104.0
    bb_lower = 96.0
    bb_width_pct = 8.0
    donchian_high = 105.0
    donchian_low = 95.0
    volume_ratio = 1.2
    ret_pct_lookback = 3.0


def contexto(features=True):
    return ContextoAnalise(
        symbol="BTCUSDT", asset_class=AssetClass.CRIPTO, timeframe="1H",
        features={"1H": FeaturesFalsas()} if features else {},
        agora_ms=TS0)


def test_limiar_de_equilibrio_usa_o_risco_retorno():
    """Comparar com 0,50 trataria um plano com RR 2 como ruim."""
    assert limiar_neutro(2.0) == pytest.approx(1 / 3)
    assert limiar_neutro(1.0) == pytest.approx(0.5)
    assert limiar_neutro(3.0) == pytest.approx(0.25)


def test_agente_sem_fonte_de_modelo_se_abstem():
    p = AgenteML().analisar(contexto(), +1)
    assert p.postura is Postura.SEM_DADOS
    assert not p.entra_no_calculo
    assert p.peso_efetivo == 0.0


def test_abstencao_fica_fora_do_calculo_e_nao_entra_como_neutro():
    """Entrar como neutro faria o sistema parecer ter consultado o modelo."""
    p = AgenteML().analisar(contexto(), +1)
    assert p.postura is not Postura.NEUTRO
    assert p.to_dict()["valor"] is None


def test_agente_com_modelo_descalibrado_se_abstem():
    tr = conjunto_sem_sinal(3000, TS0)
    te = conjunto_sem_sinal(1500, TS0 + 3000 * HORA, seed=10)
    m = validar(treinar(tr, hiper=Hiperparametros(epocas=100),
                        symbol="BTCUSDT", timeframe="1H", side="long"), te)
    agente = AgenteML(lambda s, t, l: m)
    p = agente.analisar(contexto(), +1)
    assert p.postura is Postura.SEM_DADOS
    assert "calibração" in " ".join(p.evidencias)


def test_agente_sem_features_se_abstem():
    agente = AgenteML(lambda s, t, l: modelo_calibrado())
    p = agente.analisar(contexto(features=False), +1)
    assert p.postura is Postura.SEM_DADOS
    assert "features" in p.dados_faltando


def test_agente_com_modelo_calibrado_opina():
    agente = AgenteML(lambda s, t, l: modelo_calibrado())
    p = agente.analisar(contexto(), +1)
    assert p.entra_no_calculo
    assert 0.0 <= p.metricas["p_alvo_antes_do_stop"] <= 1.0
    assert p.metricas["equilibrio_rr"] == pytest.approx(1 / 3, abs=1e-4)
    assert any("probabilidade calibrada" in e for e in p.evidencias)


def modelo_de_probabilidade_fixa(p_alvo):
    """Modelo calibrado que sempre devolve `p_alvo`.

    Construído com coeficientes zerados e intercepto no log-odds desejado,
    em vez de trocar o método `prever` — a classe usa `slots`, e substituir
    método em instância não funcionaria. Fazer pelo intercepto também exercita
    o caminho real de predição.
    """
    import math
    base = modelo_calibrado()
    return ModeloProbabilidade(
        coeficientes=[0.0] * len(FEATURES),
        intercepto=math.log(p_alvo / (1 - p_alvo)),
        padronizador=base.padronizador, features=FEATURES,
        symbol="BTCUSDT", timeframe="1H", side="long",
        n_treino=base.n_treino, n_validacao=base.n_validacao,
        calibracao=base.calibracao, mapa_calibracao=None)


def test_probabilidade_abaixo_do_equilibrio_vira_contraindicacao():
    m = modelo_de_probabilidade_fixa(0.20)
    p = AgenteML(lambda s, t, l: m).analisar(contexto(), +1)
    assert p.metricas["p_alvo_antes_do_stop"] == pytest.approx(0.20, abs=1e-3)
    assert p.valor < 0
    assert any("empatar" in c for c in p.contraindicacoes)


def test_probabilidade_acima_do_equilibrio_e_favoravel():
    m = modelo_de_probabilidade_fixa(0.55)
    p = AgenteML(lambda s, t, l: m).analisar(contexto(), +1)
    assert p.metricas["p_alvo_antes_do_stop"] == pytest.approx(0.55, abs=1e-3)
    assert p.valor > 0
    assert all("empatar" not in c for c in p.contraindicacoes)


def test_fonte_de_modelo_que_explode_nao_derruba_o_agente():
    def explode(s, t, l):
        raise RuntimeError("banco fora")
    p = AgenteML(explode).analisar(contexto(), +1)
    assert p.postura is Postura.SEM_DADOS


def test_confianca_cai_com_calibracao_ruim():
    """Modelo muito confiante e mal calibrado tem de pesar pouco."""
    bom = modelo_calibrado()
    ruim = modelo_calibrado()
    ruim.calibracao.ece = 0.09
    p_bom = AgenteML(lambda s, t, l: bom).analisar(contexto(), +1)
    p_ruim = AgenteML(lambda s, t, l: ruim).analisar(contexto(), +1)
    assert p_ruim.confianca < p_bom.confianca


def test_registro_alimenta_o_agente_direto():
    """O caminho de produção completo, sem cola manual."""
    reg = ModelRegistry()
    v = reg.registrar(modelo_calibrado(), agora_ms=TS0)
    reg.promover(v.id, agora_ms=TS0)
    agente = AgenteML(reg.buscador())
    assert agente.analisar(contexto(), +1).entra_no_calculo
    # E para um par sem modelo, silêncio.
    ctx = contexto()
    ctx.symbol = "DOGEUSDT"
    assert agente.analisar(ctx, +1).postura is Postura.SEM_DADOS


# =====================================================================
# O agente de ML não pode piorar a cobertura do consenso
# =====================================================================
def test_sem_modelo_em_producao_o_agente_ml_nem_entra():
    """Um agente que se absteria sempre derrubaria a cobertura em 18 pontos.

    A cobertura é medida como fração do peso NOMINAL dos agentes que puderam
    opinar. Incluir o agente de ML "para ficar completo", sem modelo
    treinado, faria o sistema reprovar análises que hoje passam — ou seja,
    a capacidade nova tornaria o sistema pior.
    """
    from investai.agents import ChiefInvestmentEngine
    chief = ChiefInvestmentEngine(registro_modelos=ModelRegistry())
    assert not any(a.nome == "ml" for a in chief.agentes)
    assert chief.peso_nominal_total == pytest.approx(1.0, abs=0.01)


def test_com_modelo_em_producao_o_agente_ml_entra():
    from investai.agents import ChiefInvestmentEngine
    reg = ModelRegistry()
    v = reg.registrar(modelo_calibrado(), agora_ms=TS0)
    reg.promover(v.id, agora_ms=TS0)
    chief = ChiefInvestmentEngine(registro_modelos=reg)
    assert any(a.nome == "ml" for a in chief.agentes)
    assert chief.peso_nominal_total > 1.0


def test_recarregar_agentes_acompanha_a_promocao():
    """Promover um modelo depois do boot tem de refletir no consenso."""
    from investai.agents import ChiefInvestmentEngine
    reg = ModelRegistry()
    chief = ChiefInvestmentEngine(registro_modelos=reg)
    antes = chief.peso_nominal_total
    assert "ml" not in chief.recarregar_agentes()

    v = reg.registrar(modelo_calibrado(), agora_ms=TS0)
    reg.promover(v.id, agora_ms=TS0)
    assert "ml" in chief.recarregar_agentes()
    assert chief.peso_nominal_total > antes


def test_tem_producao_ignora_modelo_que_perdeu_calibracao():
    reg = ModelRegistry()
    v = reg.registrar(modelo_calibrado(), agora_ms=TS0)
    reg.promover(v.id, agora_ms=TS0)
    assert reg.tem_producao()
    v.modelo.calibracao = None
    assert not reg.tem_producao()


def test_agentes_explicitos_nao_sao_sobrescritos():
    """Quem passou a lista de agentes manda; recarregar não pode mexer."""
    from investai.agents import ChiefInvestmentEngine
    from investai.agents.especialistas import AgenteTecnico
    chief = ChiefInvestmentEngine([AgenteTecnico()],
                                  registro_modelos=ModelRegistry())
    assert chief.recarregar_agentes() == ["tecnico"]
    assert len(chief.agentes) == 1
