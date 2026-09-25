"""Provider sintético e hub de dados."""
import pytest

from investai.datahub import DataHub
from investai.exchanges import ExchangeError, MarketDataProvider, SyntheticProvider
from investai.exchanges.synthetic import PRECO_BASE


def test_cumpre_o_contrato(provider):
    assert isinstance(provider, MarketDataProvider)


def test_simbolo_desconhecido_e_recusado(provider):
    """Inventar dados para um texto qualquer transformaria erro de digitação
    em análise plausível."""
    with pytest.raises(ExchangeError, match="não existe"):
        provider.candles("MOEDAFALSA", "1H")
    with pytest.raises(ExchangeError):
        provider.ticker("MOEDAFALSA")
    with pytest.raises(ExchangeError):
        provider.contrato("MOEDAFALSA")


def test_aceita_minusculo(provider):
    assert provider.candles("btcusdt", "1H", limit=10)


def test_candles_ordenados_e_contiguos(provider):
    velas = provider.candles("BTCUSDT", "1H", limit=200)
    assert len(velas) == 200
    assert all(velas[i].ts < velas[i + 1].ts for i in range(len(velas) - 1))
    assert all(velas[i + 1].ts - velas[i].ts == 3_600_000
               for i in range(len(velas) - 1))


def test_ohlc_internamente_coerente(provider):
    for c in provider.candles("ETHUSDT", "1H", limit=300):
        assert c.low <= min(c.open, c.close)
        assert c.high >= max(c.open, c.close)
        assert c.low > 0 and c.volume > 0


def test_determinismo_entre_instancias():
    a = SyntheticProvider(seed=42, agora_ms=1_700_000_000_000)
    b = SyntheticProvider(seed=42, agora_ms=1_700_000_000_000)
    assert ([c.close for c in a.candles("BTCUSDT", "1H", limit=100)]
            == [c.close for c in b.candles("BTCUSDT", "1H", limit=100)])


def test_seed_diferente_gera_serie_diferente():
    a = SyntheticProvider(seed=1, agora_ms=1_700_000_000_000)
    b = SyntheticProvider(seed=2, agora_ms=1_700_000_000_000)
    assert ([c.close for c in a.candles("BTCUSDT", "1H", limit=50)]
            != [c.close for c in b.candles("BTCUSDT", "1H", limit=50)])


def test_janela_menor_e_sufixo_da_maior(provider):
    """Pedir menos candles tem que devolver o FINAL da mesma série."""
    grande = provider.candles("SOLUSDT", "1H", limit=500)
    pequena = provider.candles("SOLUSDT", "1H", limit=50)
    assert [c.ts for c in pequena] == [c.ts for c in grande[-50:]]
    assert [c.close for c in pequena] == [c.close for c in grande[-50:]]


def test_end_ms_corta_a_serie(provider):
    completa = provider.candles("BTCUSDT", "1H", limit=300)
    corte = completa[-100].ts
    parcial = provider.candles("BTCUSDT", "1H", limit=300, end_ms=corte)
    assert parcial[-1].ts == corte


def test_precos_ficam_em_faixa_plausivel(provider):
    """Sem reversão ao âncora, mil barras de drift levariam o preço a valores
    absurdos e distorceriam qualquer métrica percentual."""
    for sym in ("BTCUSDT", "XRPUSDT"):
        closes = [c.close for c in provider.candles(sym, "1H", limit=2000)]
        ancora = PRECO_BASE[sym]
        assert min(closes) > ancora * 0.2
        assert max(closes) < ancora * 5.0


# ------------------------------------------------------------------- DataHub
def test_paginacao_devolve_serie_contigua(hub, provider):
    """Se a paginação concatenasse trechos incoerentes, todo backtest longo
    estaria medindo uma série que nunca existiu."""
    paginado = hub.historico("BTCUSDT", "1H", barras=2500)
    direto = provider.candles("BTCUSDT", "1H", limit=2500)
    assert [c.ts for c in paginado] == [c.ts for c in direto]
    assert [c.close for c in paginado] == [c.close for c in direto]
    assert all(paginado[i + 1].ts - paginado[i].ts == 3_600_000
               for i in range(len(paginado) - 1))


def test_paginacao_para_no_fim_do_historico(hub):
    """Pedir mais que o disponível não pode entrar em laço infinito."""
    velas = hub.historico("BTCUSDT", "1H", barras=50_000)
    assert 0 < len(velas) <= 50_000


def test_cache_evita_segunda_chamada(provider):
    class _Contador:
        def __init__(self, base):
            self.base = base
            self.chamadas = 0

        def candles(self, *a, **k):
            self.chamadas += 1
            return self.base.candles(*a, **k)

        def ticker(self, s):
            return self.base.ticker(s)

    contador = _Contador(provider)
    hub = DataHub(contador, ttl_candles=60.0)
    hub.candles("BTCUSDT", "1H", limit=100)
    hub.candles("BTCUSDT", "1H", limit=100)
    assert contador.chamadas == 1
    hub.limpar_cache()
    hub.candles("BTCUSDT", "1H", limit=100)
    assert contador.chamadas == 2


def test_ticker_com_falha_degrada_sem_derrubar_o_scan(provider):
    class _Quebrado:
        def candles(self, *a, **k):
            return provider.candles(*a, **k)

        def ticker(self, symbol):
            raise RuntimeError("API fora do ar")

    assert DataHub(_Quebrado()).ticker("BTCUSDT") is None


def test_tickers_em_lote_usa_endpoint_unico(hub):
    d = hub.tickers(["BTCUSDT", "ETHUSDT"])
    assert set(d) == {"BTCUSDT", "ETHUSDT"}


def test_tickers_ignora_simbolo_ausente(hub):
    assert hub.tickers(["BTCUSDT", "INEXISTENTE"]) .keys() == {"BTCUSDT"}
