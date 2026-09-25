"""Camada de dados: normalização, qualidade e registro de fontes."""
import pytest

from investai.data import (
    FONTE_NAO_CONFIGURADA, AssetClass, DataKind, DataProvenance, DataRegistry,
    FreezeDetector, MarketKind, ProviderInfo, ProviderState, QualityPolicy,
    QualityStatus, SymbolError, avaliar_serie, comparar_fontes,
    mesmo_fator_risco, mesmo_instrumento, normalizar, para_provedor,
    registry_padrao,
)
from investai.models import Candle

HORA = 3_600_000
AGORA = 1_700_000_000_000


# ============================================================ normalização
@pytest.mark.parametrize("bruto,base,quote", [
    ("BTCUSDT", "BTC", "USDT"),
    ("BTC-USDT", "BTC", "USDT"),
    ("BTC_USDT", "BTC", "USDT"),
    ("btcusdt", "BTC", "USDT"),
    ("ETHBTC", "ETH", "BTC"),
    ("SOLUSDC", "SOL", "USDC"),
    ("BTCBRL", "BTC", "BRL"),
])
def test_divisao_de_par_cripto(bruto, base, quote):
    s = normalizar(bruto)
    assert (s.base, s.quote) == (base, quote)


def test_quote_longa_vence_a_curta():
    """USDT tem que ser testado antes de USD, senão BTCUSDT viraria base
    BTCUSD + quote T."""
    assert normalizar("BTCUSDT").quote == "USDT"
    assert normalizar("BTCUSD").quote == "USD"
    assert normalizar("BTCTUSD").quote == "TUSD"


def test_alias_resolvido_antes_da_divisao():
    """XBTUSD quebraria em base XB + quote TUSD se o alias fosse aplicado
    depois — e o XBT→BTC nunca valeria."""
    s = normalizar("XBTUSD")
    assert (s.base, s.quote) == ("BTC", "USD")
    assert mesmo_instrumento("XBTUSD", "BTCUSD")


def test_wrapped_agrupa_no_mesmo_fator_de_risco():
    assert normalizar("WBTCUSDT").base == "BTC"
    assert mesmo_fator_risco("WBTCUSDT", "BTCUSDT")


@pytest.mark.parametrize("sufixo", ["-PERP", "_PERP", "-SWAP", "_UMCBL",
                                    "-PERPETUAL"])
def test_sufixos_de_perpetuo_reconhecidos(sufixo):
    assert normalizar(f"BTCUSDT{sufixo}").market is MarketKind.PERPETUO


def test_spot_e_perpetuo_sao_instrumentos_diferentes():
    """Basis é justamente a diferença entre eles; tratar como o mesmo ativo
    corromperia o cálculo."""
    assert not mesmo_instrumento("BTCUSDT", "BTCUSDT-PERP")


def test_spot_e_perpetuo_sao_o_mesmo_fator_de_risco():
    """Para controle de concentração, são uma única aposta."""
    assert mesmo_fator_risco("BTCUSDT", "BTCUSDT-PERP")


def test_venue_entra_no_canonico():
    s = normalizar("BTCUSDT", venue="bitget", market=MarketKind.PERPETUO)
    assert s.canonical == "CRIPTO:BTC/USDT:PERPETUO@BITGET"
    assert s.venue == "BITGET"


def test_dica_de_mercado_vence_heuristica():
    assert normalizar("BTCUSDT", market=MarketKind.PERPETUO).market is MarketKind.PERPETUO


@pytest.mark.parametrize("ticker,classe", [
    ("MXRF11", AssetClass.FII),
    ("HGLG11", AssetClass.FII),
    ("PETR4", AssetClass.ACAO),
    ("VALE3", AssetClass.ACAO),
    ("ITUB4", AssetClass.ACAO),
    ("BOVA11", AssetClass.ETF),
    ("IVVB11", AssetClass.ETF),
])
def test_classificacao_de_ativos_da_b3(ticker, classe):
    assert normalizar(ticker).asset_class is classe


def test_etf_brasileiro_nao_e_confundido_com_fii():
    """BOVA11 e HGLG11 têm o mesmo sufixo 11; só a lista explícita distingue."""
    assert normalizar("BOVA11").asset_class is AssetClass.ETF
    assert normalizar("HGLG11").asset_class is AssetClass.FII


def test_simbolo_indecifravel_e_recusado():
    """Adivinhar aqui produziria análise de um ativo que não existe."""
    with pytest.raises(SymbolError, match="cotação"):
        normalizar("LIXOALEATORIO")


def test_simbolo_vazio_recusado():
    with pytest.raises(SymbolError):
        normalizar("   ")


def test_canonico_faz_roundtrip():
    original = normalizar("BTCUSDT", venue="bitget", market=MarketKind.PERPETUO)
    assert normalizar(original.canonical).canonical == original.canonical


def test_canonico_invalido_recusado():
    with pytest.raises(SymbolError):
        normalizar("CLASSE_INEXISTENTE:A/B:SPOT")


@pytest.mark.parametrize("estilo,esperado", [
    ("colado", "BTCUSDT"), ("hifen", "BTC-USDT"),
    ("barra", "BTC/USDT"), ("underscore", "BTC_USDT"),
])
def test_conversao_de_volta_para_provedor(estilo, esperado):
    assert para_provedor(normalizar("BTCUSDT"), estilo) == esperado


def test_estilo_desconhecido_recusado():
    with pytest.raises(ValueError):
        para_provedor(normalizar("BTCUSDT"), "inventado")


def test_renda_fixa_com_dica_explicita():
    s = normalizar("TESOURO_IPCA_2035", asset_class=AssetClass.RENDA_FIXA)
    assert s.asset_class is AssetClass.RENDA_FIXA
    assert s.market is MarketKind.NAO_APLICAVEL


# ================================================================ qualidade
def _serie(n=100, passo=HORA, base=100.0, fim=AGORA, repetidos=0):
    out = []
    for i in range(n):
        p = base if i >= n - repetidos else base + i * 0.5
        out.append(Candle(ts=fim - (n - 1 - i) * passo, open=p, high=p + 1,
                          low=p - 1, close=p, volume=100.0))
    return out


def test_serie_saudavel_e_ok():
    r = avaliar_serie(_serie(), HORA, agora_ms=AGORA + HORA // 2)
    assert r.status is QualityStatus.OK
    assert r.pode_gerar_sinal


def test_dado_velho_nao_passa_como_tempo_real():
    r = avaliar_serie(_serie(fim=AGORA - 5 * HORA), HORA, agora_ms=AGORA)
    assert r.status is QualityStatus.INADEQUADO
    assert not r.pode_gerar_sinal
    assert any("NÃO é tempo real" in m for m in r.motivos)


def test_atraso_moderado_degrada_mas_nao_bloqueia():
    r = avaliar_serie(_serie(fim=AGORA - 2 * HORA), HORA, agora_ms=AGORA)
    assert r.status is QualityStatus.DEGRADADO
    assert r.pode_gerar_sinal


def test_feed_congelado_e_bloqueado():
    r = avaliar_serie(_serie(repetidos=10), HORA, agora_ms=AGORA + HORA // 2)
    assert r.status is QualityStatus.INADEQUADO
    assert any("congelado" in m for m in r.motivos)


def test_timestamp_no_futuro_e_bloqueado():
    """Relógio adiantado indica erro de fonte, não latência."""
    r = avaliar_serie(_serie(fim=AGORA + 3 * HORA), HORA, agora_ms=AGORA)
    assert r.status is QualityStatus.INADEQUADO
    assert any("futuro" in m for m in r.motivos)


def test_buracos_na_serie_degradam():
    s = _serie(100)
    del s[30:40]
    r = avaliar_serie(s, HORA, agora_ms=AGORA + HORA // 2)
    assert r.status is QualityStatus.DEGRADADO
    assert r.detalhes["completude"] < 0.95


def test_timestamps_fora_de_ordem_bloqueiam():
    s = _serie(50)
    s[10], s[11] = s[11], s[10]
    r = avaliar_serie(s, HORA, agora_ms=AGORA + HORA // 2)
    assert r.status is QualityStatus.INADEQUADO


def test_ohlc_inconsistente_bloqueia():
    s = _serie(50)
    s[10] = Candle(ts=s[10].ts, open=100, high=90, low=110, close=100, volume=1)
    r = avaliar_serie(s, HORA, agora_ms=AGORA + HORA // 2)
    assert r.status is QualityStatus.INADEQUADO
    assert any("OHLC" in m for m in r.motivos)


def test_serie_vazia_bloqueia():
    assert avaliar_serie([], HORA).status is QualityStatus.INADEQUADO


def test_latencia_alta_bloqueia():
    r = avaliar_serie(_serie(), HORA, agora_ms=AGORA + HORA // 2,
                      recebido_em_ms=AGORA + 120_000)
    assert r.status is QualityStatus.INADEQUADO
    assert any("latência" in m for m in r.motivos)


def test_politica_customizada_e_respeitada():
    rigida = QualityPolicy(idade_max_multiplo_tf=0.5,
                           idade_degradado_multiplo_tf=0.2)
    r = avaliar_serie(_serie(fim=AGORA - HORA), HORA, agora_ms=AGORA,
                      politica=rigida)
    assert r.status is QualityStatus.INADEQUADO


# ------------------------------------------------------------- multifonte
def test_fontes_concordantes_ok():
    r = comparar_fontes({"a": 64000.0, "b": 64010.0, "c": 63995.0})
    assert r.status is QualityStatus.OK


def test_divergencia_medida_pela_amplitude_nao_pelo_desvio_da_mediana():
    """Com duas fontes, o desvio contra a mediana é metade da discordância
    real — subestimaria o problema exatamente no caso mais difícil."""
    r = comparar_fontes({"a": 64000.0, "b": 66000.0})
    assert r.detalhes["amplitude_pct"] == pytest.approx(3.0769, abs=0.01)
    assert r.status is QualityStatus.INADEQUADO
    assert not r.pode_gerar_sinal


def test_uma_fonte_unica_e_degradado_nao_ok():
    """Sem confirmação cruzada não há como detectar erro da fonte."""
    r = comparar_fontes({"bitget": 64000.0})
    assert r.status is QualityStatus.DEGRADADO
    assert "sem confirmação cruzada" in r.motivos[0]


def test_nenhuma_fonte_devolve_nao_configurado():
    r = comparar_fontes({})
    assert r.status is QualityStatus.NAO_CONFIGURADO
    assert r.motivos == [FONTE_NAO_CONFIGURADA]
    assert not r.pode_gerar_sinal


def test_precos_invalidos_sao_descartados():
    r = comparar_fontes({"boa": 64000.0, "zero": 0.0, "nula": None})
    assert r.status is QualityStatus.DEGRADADO   # sobrou só uma válida


def test_fonte_discrepante_e_identificada():
    r = comparar_fontes({"a": 64000.0, "b": 64010.0, "ruim": 70000.0})
    assert r.detalhes["fonte_maxima"] == "ruim"
    assert r.status is QualityStatus.INADEQUADO


# ------------------------------------------------------- freeze detector
def test_detector_de_congelamento_escala_com_repeticoes():
    fd = FreezeDetector(limite=5)
    estados = [fd.observar("BTCUSDT", 64000.0).status for _ in range(5)]
    assert estados[0] is QualityStatus.OK
    assert estados[-1] is QualityStatus.INADEQUADO


def test_preco_novo_reinicia_o_detector():
    fd = FreezeDetector(limite=3)
    for _ in range(3):
        fd.observar("BTCUSDT", 64000.0)
    assert fd.observar("BTCUSDT", 64001.0).status is QualityStatus.OK


def test_detector_isola_por_simbolo():
    fd = FreezeDetector(limite=3)
    for _ in range(3):
        fd.observar("BTCUSDT", 1.0)
    assert fd.observar("ETHUSDT", 2.0).status is QualityStatus.OK


# ------------------------------------------------------------ provenance
def test_provenance_calcula_latencia_e_idade():
    p = DataProvenance(provider="bitget", venue="BITGET",
                       symbol="CRIPTO:BTC/USDT:PERPETUO", timeframe="1H",
                       timestamp_ms=AGORA, received_at_ms=AGORA + 1500)
    assert p.latencia_ms == 1500
    assert p.idade_ms(AGORA + 60_000) == 60_000
    assert p.confiavel and p.gera_sinal


def test_provenance_inadequada_nao_gera_sinal():
    p = DataProvenance(provider="x", venue="X", symbol="S", timeframe="1H",
                       timestamp_ms=AGORA, received_at_ms=AGORA,
                       quality=QualityStatus.INADEQUADO)
    assert not p.gera_sinal


# ================================================================= registro
def test_registro_marca_lacunas_em_vez_de_omitir():
    """Declarar o que o sistema não sabe é melhor que a interface preencher
    com um número plausível."""
    reg = registry_padrao(bitget_ok=lambda: False, brapi_ok=lambda: False)
    reg.revalidar()
    cov = reg.cobertura(AssetClass.ACAO, DataKind.FUNDAMENTOS)
    assert not cov.disponivel
    assert FONTE_NAO_CONFIGURADA in cov.mensagem
    assert cov.instrucao


def test_provedor_conectado_habilita_cobertura():
    reg = registry_padrao(bitget_ok=lambda: True, brapi_ok=lambda: False)
    reg.revalidar()
    cov = reg.cobertura(AssetClass.CRIPTO, DataKind.OHLCV)
    assert cov.disponivel
    assert "bitget" in cov.provedores


def test_fonte_unica_marca_falta_de_confirmacao_cruzada():
    reg = registry_padrao(bitget_ok=lambda: True)
    reg.revalidar()
    assert reg.cobertura(AssetClass.CRIPTO,
                         DataKind.OHLCV).status is QualityStatus.DEGRADADO


def test_duas_fontes_habilitam_confirmacao_cruzada():
    reg = DataRegistry()
    for nome in ("a", "b"):
        reg.registrar(ProviderInfo(
            nome=nome, descricao="", classes=(AssetClass.CRIPTO,),
            tipos=(DataKind.OHLCV,), checar=lambda: True))
    reg.revalidar()
    cov = reg.cobertura(AssetClass.CRIPTO, DataKind.OHLCV)
    assert cov.status is QualityStatus.OK
    assert len(cov.provedores) == 2


def test_provedor_com_excecao_no_check_vira_erro():
    reg = DataRegistry()
    def explode():
        raise RuntimeError("conexão recusada")
    reg.registrar(ProviderInfo(nome="ruim", descricao="",
                               classes=(AssetClass.CRIPTO,),
                               tipos=(DataKind.OHLCV,), checar=explode))
    reg.revalidar()
    p = reg.provedores()[0]
    assert p.estado is ProviderState.ERRO
    assert "conexão recusada" in p.ultimo_erro
    cov = reg.cobertura(AssetClass.CRIPTO, DataKind.OHLCV)
    assert not cov.disponivel
    assert cov.status is QualityStatus.INADEQUADO


def test_combinacao_sem_nenhum_provedor_diz_que_nao_existe_conector():
    reg = DataRegistry()
    cov = reg.cobertura(AssetClass.COMMODITY, DataKind.TRADES)
    assert not cov.disponivel
    assert "nenhum provedor" in cov.mensagem


def test_resumo_conta_lacunas():
    reg = registry_padrao(sintetico=True)
    reg.revalidar()
    r = reg.resumo()
    assert r["conectados"] == ["sintetico"]
    assert r["combinacoes_sem_fonte"] > 0
    assert r["exemplos_sem_fonte"]


def test_matriz_de_cobertura_cobre_todas_as_classes():
    reg = registry_padrao(sintetico=True)
    reg.revalidar()
    m = reg.matriz_cobertura()
    assert AssetClass.CRIPTO.value in m
    assert AssetClass.DESCONHECIDO.value not in m
    assert m[AssetClass.CRIPTO.value][DataKind.OHLCV.value]["disponivel"] is True


def test_provedor_sintetico_declara_que_nao_e_cotacao():
    reg = registry_padrao(sintetico=True)
    info = next(p for p in reg.provedores() if p.nome == "sintetico")
    assert "NÃO são cotações" in info.descricao


def test_remover_provedor():
    reg = registry_padrao(sintetico=True)
    assert reg.remover("sintetico") is True
    assert reg.remover("sintetico") is False
