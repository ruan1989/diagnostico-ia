"""Feed em tempo real: máquina de estado e transporte.

O arquivo tem duas partes. A máquina de estado é testada de forma síncrona e
exaustiva, porque é onde os erros moram — frescor, lacuna, sequência fora de
ordem. O transporte é testado contra um servidor WebSocket de verdade rodando
em 127.0.0.1, porque só um socket real prova que conectar, assinar, receber e
reconectar funcionam.

A regra central: **um feed que congela em silêncio é pior que um feed que
cai.** Se a conexão cai, o sistema percebe e para. Se ela fica aberta e muda,
o último preço recebido continua na memória parecendo atual, e o sistema
segue dimensionando posição e conferindo stop contra um número que já não
existe.
"""
import asyncio
import json
import threading
import time

import pytest

from investai.exchanges.feed_estado import (
    SILENCIO_PARA_RECONECTAR_MS, TOLERANCIA_PADRAO_MS, EstadoFeed,
)
from investai.exchanges.feed_ws import (
    FeedWebSocket, assinar_login, canais_publicos,
)

T0 = 1_700_000_000_000


# =====================================================================
# Máquina de estado
# =====================================================================
def test_canal_sem_mensagem_nao_e_fresco():
    f = EstadoFeed({"ticker": 5_000})
    assert f.ultimo("ticker", T0) is None
    assert f.canais_velhos(T0) == ["ticker"]


def test_mensagem_recente_e_devolvida():
    f = EstadoFeed({"ticker": 5_000})
    f.mensagem("ticker", {"p": 1}, T0)
    assert f.ultimo("ticker", T0 + 1_000).dados == {"p": 1}


def test_mensagem_velha_devolve_none_em_vez_do_valor():
    """O comportamento mais importante do módulo.

    Quem chama tem como tratar ausência; ninguém trata bem um número que
    parece atual e não é.
    """
    f = EstadoFeed({"ticker": 5_000})
    f.mensagem("ticker", {"p": 1}, T0)
    assert f.ultimo("ticker", T0 + 5_001) is None


def test_no_limite_exato_ainda_e_fresco():
    f = EstadoFeed({"ticker": 5_000})
    f.mensagem("ticker", {"p": 1}, T0)
    assert f.ultimo("ticker", T0 + 5_000) is not None


def test_dado_velho_pode_ser_lido_com_a_idade_junto():
    """Para quem decide conscientemente usar dado velho.

    Recebe a idade junto, para não poder alegar que não sabia.
    """
    f = EstadoFeed({"ticker": 1_000})
    f.mensagem("ticker", {"p": 1}, T0)
    msg, recebida = f.ultimo_mesmo_velho("ticker")
    assert msg.dados == {"p": 1}
    assert recebida == T0
    assert f.ultimo("ticker", T0 + 9_999) is None


def test_tolerancia_por_canal():
    """Ticker chega várias vezes por segundo; candle de 1H, uma vez por hora."""
    f = EstadoFeed({"ticker": 5_000, "candle1H": 4_000_000})
    f.mensagem("ticker", {}, T0)
    f.mensagem("candle1H", {}, T0)
    depois = T0 + 60_000
    assert f.ultimo("ticker", depois) is None
    assert f.ultimo("candle1H", depois) is not None


def test_tolerancia_padrao_quando_nao_informada():
    f = EstadoFeed()
    f.mensagem("novo", {}, T0)
    assert f.canais["novo"].tolerancia_ms == TOLERANCIA_PADRAO_MS


# ------------------------------------------------------------ sequência
def test_sequencia_atrasada_e_descartada():
    """Aplicar atualização antiga por cima de nova faria o preço andar para
    trás sem que nada denunciasse."""
    f = EstadoFeed({"ticker": 60_000})
    f.mensagem("ticker", {"p": 1}, T0, sequencia=100)
    f.mensagem("ticker", {"p": 2}, T0 + 10, sequencia=200)
    f.mensagem("ticker", {"p": 3}, T0 + 20, sequencia=150)   # atrasada
    assert f.ultimo("ticker", T0 + 30).dados == {"p": 2}
    assert f.canais["ticker"].fora_de_ordem == 1
    assert f.canais["ticker"].mensagens == 2


def test_sequencia_repetida_e_descartada():
    f = EstadoFeed({"ticker": 60_000})
    f.mensagem("ticker", {"p": 1}, T0, sequencia=100)
    f.mensagem("ticker", {"p": 2}, T0 + 10, sequencia=100)
    assert f.ultimo("ticker", T0 + 20).dados == {"p": 1}
    assert f.canais["ticker"].fora_de_ordem == 1


def test_sem_sequencia_toda_mensagem_entra():
    f = EstadoFeed({"ticker": 60_000})
    f.mensagem("ticker", {"p": 1}, T0)
    f.mensagem("ticker", {"p": 2}, T0 + 10)
    assert f.ultimo("ticker", T0 + 20).dados == {"p": 2}
    assert f.canais["ticker"].fora_de_ordem == 0


# ------------------------------------------------------------- lacunas
def test_reconexao_registra_lacuna():
    """Entre a queda e a volta, o sistema não viu o mercado."""
    f = EstadoFeed({"ticker": 5_000})
    f.conectou(T0)
    f.desconectou(T0 + 10_000, "queda")
    f.conectou(T0 + 30_000)
    assert f.reconexoes == 1
    assert len(f.lacunas) == 1
    g = f.lacunas[0]
    assert g.inicio_ms == T0 + 10_000
    assert g.fim_ms == T0 + 30_000
    assert g.duracao_ms == 20_000


def test_primeira_conexao_nao_e_lacuna():
    f = EstadoFeed({"ticker": 5_000})
    f.conectou(T0)
    assert f.lacunas == []
    assert f.reconexoes == 0


def test_lacuna_por_canal():
    f = EstadoFeed({"ticker": 5_000, "candle": 5_000})
    f.conectou(T0)
    f.desconectou(T0 + 1_000)
    f.conectou(T0 + 2_000)
    assert {g.canal for g in f.lacunas} == {"ticker", "candle"}


def test_lacuna_pendente_torna_o_feed_nao_confiavel():
    f = EstadoFeed({"ticker": 60_000})
    f.conectou(T0)
    f.mensagem("ticker", {}, T0)
    assert f.confiavel(T0 + 1_000)
    f.desconectou(T0 + 2_000)
    f.conectou(T0 + 3_000)
    f.mensagem("ticker", {}, T0 + 3_000)
    assert not f.confiavel(T0 + 3_500), "lacuna pendente não pode ser ignorada"


def test_consumir_lacunas_devolve_e_limpa():
    """Quem consome assume a responsabilidade de preencher por REST."""
    f = EstadoFeed({"ticker": 60_000})
    f.conectou(T0)
    f.desconectou(T0 + 1_000)
    f.conectou(T0 + 2_000)
    f.mensagem("ticker", {}, T0 + 2_000)
    assert len(f.consumir_lacunas()) == 1
    assert f.consumir_lacunas() == []
    assert f.confiavel(T0 + 2_500), "sem lacuna pendente, volta a ser confiável"


# ------------------------------------------------------- reconexão preventiva
def test_silencio_longo_pede_reconexao():
    """Um socket pode ficar aberto e parar de entregar.

    Esperar o timeout do TCP significaria minutos operando com preço
    congelado.
    """
    f = EstadoFeed({"ticker": 60_000})
    f.conectou(T0)
    f.mensagem("ticker", {}, T0)
    assert not f.precisa_reconectar(T0 + 1_000)
    assert f.precisa_reconectar(T0 + SILENCIO_PARA_RECONECTAR_MS + 1)


def test_desconectado_sempre_pede_reconexao():
    f = EstadoFeed({"ticker": 60_000})
    assert f.precisa_reconectar(T0)


def test_um_canal_ativo_impede_reconexao_desnecessaria():
    """Candle de 1H fica quieto por definição; ticker não.

    Reconectar porque o canal lento está quieto derrubaria o rápido junto.
    """
    f = EstadoFeed({"ticker": 60_000, "candle1H": 4_000_000})
    f.conectou(T0)
    f.mensagem("candle1H", {}, T0)
    f.mensagem("ticker", {}, T0 + SILENCIO_PARA_RECONECTAR_MS + 5_000)
    assert not f.precisa_reconectar(T0 + SILENCIO_PARA_RECONECTAR_MS + 6_000)


def test_conectado_sem_nenhuma_mensagem_reconecta_apos_o_silencio():
    f = EstadoFeed({"ticker": 60_000})
    f.conectou(T0)
    assert not f.precisa_reconectar(T0 + 1_000)
    assert f.precisa_reconectar(T0 + SILENCIO_PARA_RECONECTAR_MS + 1)


def test_estado_explica_o_perigo():
    f = EstadoFeed({"ticker": 5_000})
    d = f.estado(T0)
    assert "congela em silêncio é pior" in d["observacao"]
    assert d["canais_velhos"] == ["ticker"]


# =====================================================================
# Interpretação de mensagem
# =====================================================================
def relogio_fixo(valor=T0):
    return lambda: valor


def test_pong_nao_conta_como_dado():
    """Mensagem de controle confirma o transporte, não o mercado.

    Contá-la como atividade faria um feed inscrito e mudo parecer saudável.
    """
    feed = FeedWebSocket(relogio=relogio_fixo())
    assert feed.processar("pong") is None
    assert feed.estado_feed.canais == {}


def test_ack_de_inscricao_nao_conta_como_dado():
    feed = FeedWebSocket(relogio=relogio_fixo())
    assert feed.processar(json.dumps({"event": "subscribe",
                                      "arg": {"channel": "ticker"}})) is None
    assert feed.estado_feed.canais == {}


def test_login_aceito_marca_autenticado():
    feed = FeedWebSocket(relogio=relogio_fixo())
    feed.processar(json.dumps({"event": "login", "code": 0}))
    assert feed.autenticado


def test_login_recusado_registra_erro_sem_autenticar():
    feed = FeedWebSocket(relogio=relogio_fixo())
    feed.processar(json.dumps({"event": "login", "code": 30012,
                               "msg": "assinatura inválida"}))
    assert not feed.autenticado
    assert any("login recusado" in e for e in feed.estado_feed.erros)


def test_erro_do_servidor_e_registrado():
    feed = FeedWebSocket(relogio=relogio_fixo())
    feed.processar(json.dumps({"event": "error", "msg": "canal inexistente"}))
    assert any("canal inexistente" in e for e in feed.estado_feed.erros)


def test_mensagem_de_dados_vira_canal():
    feed = FeedWebSocket(relogio=relogio_fixo())
    msg = feed.processar(json.dumps({
        "arg": {"channel": "ticker", "instId": "BTCUSDT"},
        "data": [{"lastPr": "64000"}], "ts": T0}))
    assert msg.canal == "ticker:BTCUSDT"
    assert feed.ultimo("ticker:BTCUSDT").dados == [{"lastPr": "64000"}]


def test_mensagem_sem_dados_e_ignorada():
    feed = FeedWebSocket(relogio=relogio_fixo())
    assert feed.processar(json.dumps({"arg": {"channel": "ticker"}})) is None


def test_json_invalido_vira_erro_e_nao_excecao():
    feed = FeedWebSocket(relogio=relogio_fixo())
    assert feed.processar("{isso nao e json") is None
    assert any("não-JSON" in e for e in feed.estado_feed.erros)


def test_bytes_sao_decodificados():
    feed = FeedWebSocket(relogio=relogio_fixo())
    msg = feed.processar(json.dumps({
        "arg": {"channel": "ticker", "instId": "ETHUSDT"},
        "data": [{"lastPr": "3000"}]}).encode())
    assert msg.canal == "ticker:ETHUSDT"


def test_callback_que_explode_nao_derruba_o_feed():
    feed = FeedWebSocket(relogio=relogio_fixo())
    feed.on_mensagem = lambda m: (_ for _ in ()).throw(RuntimeError("x"))
    msg = feed.processar(json.dumps({
        "arg": {"channel": "ticker", "instId": "BTCUSDT"},
        "data": [{}], "ts": T0}))
    assert msg is not None


# ------------------------------------------------------------- segurança
def test_repr_nao_vaza_o_segredo():
    """Segredo em log é segredo vazado."""
    class Cred:
        api_key = "bg_chave_secreta_completa"
        api_secret = "segredo_que_nao_pode_aparecer"
        passphrase = "frase_secreta"

        def mascara(self):
            return "bg_c…leta"

    texto = repr(FeedWebSocket(credenciais=Cred()))
    assert "segredo_que_nao_pode_aparecer" not in texto
    assert "frase_secreta" not in texto
    assert "bg_chave_secreta_completa" not in texto
    assert "bg_c…leta" in texto


def test_assinatura_de_login_e_deterministica():
    a = assinar_login("segredo", "1700000000")
    b = assinar_login("segredo", "1700000000")
    assert a == b
    assert a != assinar_login("segredo", "1700000001")
    assert a != assinar_login("outro", "1700000000")


def test_canais_publicos_monta_a_inscricao():
    args = canais_publicos(["btcusdt", "ETHUSDT"], incluir_candle="1H")
    assert len(args) == 4
    assert {a["instId"] for a in args} == {"BTCUSDT", "ETHUSDT"}
    assert any(a["channel"] == "candle1H" for a in args)


def test_canais_publicos_sem_candle():
    args = canais_publicos(["BTCUSDT"], incluir_candle=None)
    assert len(args) == 1
    assert args[0]["channel"] == "ticker"


# =====================================================================
# Transporte, contra um servidor WebSocket de verdade
# =====================================================================
pytest.importorskip("websockets")


class ServidorFalso:
    """Servidor WS local. Roda o próprio laço de eventos em uma thread."""

    def __init__(self, porta):
        self.porta = porta
        self.recebidas: list[str] = []
        self.conexoes = 0
        self.derrubar = False
        self._loop = None
        self._thread = None
        self._servidor = None
        self._pronto = threading.Event()

    async def _handler(self, ws):
        self.conexoes += 1
        try:
            async for bruto in ws:
                self.recebidas.append(bruto)
                if bruto == "ping":
                    await ws.send("pong")
                    continue
                corpo = json.loads(bruto)
                if corpo.get("op") == "login":
                    await ws.send(json.dumps({"event": "login", "code": 0}))
                elif corpo.get("op") == "subscribe":
                    for arg in corpo["args"]:
                        await ws.send(json.dumps({"event": "subscribe",
                                                  "arg": arg}))
                    for i in range(3):
                        if self.derrubar:
                            await ws.close()
                            return
                        await ws.send(json.dumps({
                            "arg": {"instType": "USDT-FUTURES",
                                    "channel": "ticker", "instId": "BTCUSDT"},
                            "data": [{"lastPr": str(64000 + i)}],
                            "ts": T0 + i * 1000}))
                        await asyncio.sleep(0.01)
        except Exception:                               # noqa: BLE001
            pass

    def _rodar(self):
        import websockets
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)

        async def subir():
            self._servidor = await websockets.serve(self._handler,
                                                    "127.0.0.1", self.porta)
            self._pronto.set()
            await asyncio.Future()

        try:
            loop.run_until_complete(subir())
        except asyncio.CancelledError:
            pass
        finally:
            loop.close()

    def __enter__(self):
        self._thread = threading.Thread(target=self._rodar, daemon=True)
        self._thread.start()
        assert self._pronto.wait(5), "servidor de teste não subiu"
        return self

    def __exit__(self, *_):
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3)


def cliente_para(porta, **kw):
    import websockets

    async def conectar(url):
        return await websockets.connect(url)

    return FeedWebSocket(f"ws://127.0.0.1:{porta}", conectar_fn=conectar, **kw)


def esperar(condicao, limite=5.0, passo=0.05):
    fim = time.time() + limite
    while time.time() < fim:
        if condicao():
            return True
        time.sleep(passo)
    return False


def test_conecta_assina_e_recebe():
    """O teste que prova o transporte: socket de verdade, dados de verdade."""
    with ServidorFalso(8811) as srv:
        feed = cliente_para(8811,
                            canais=canais_publicos(["BTCUSDT"],
                                                   incluir_candle=None))
        feed.iniciar()
        try:
            assert esperar(lambda: feed.ultimo("ticker:BTCUSDT") is not None)
            assert feed.estado_feed.conectado
            assert feed.ultimo("ticker:BTCUSDT").dados == [{"lastPr": "64002"}]
            assert feed.estado_feed.canais["ticker:BTCUSDT"].mensagens == 3
            assert any("subscribe" in r for r in srv.recebidas)
        finally:
            feed.parar()
        assert not feed.rodando


def test_autentica_no_canal_privado():
    class Cred:
        api_key = "chave"
        api_secret = "segredo"
        passphrase = "frase"

        def mascara(self):
            return "cha…ve"

    with ServidorFalso(8812) as srv:
        feed = cliente_para(8812, credenciais=Cred(),
                            canais=canais_publicos(["BTCUSDT"],
                                                   incluir_candle=None))
        feed.iniciar()
        try:
            assert esperar(lambda: feed.autenticado)
            login = [r for r in srv.recebidas if '"login"' in r]
            assert login, "o cliente não mandou login"
            corpo = json.loads(login[0])["args"][0]
            assert corpo["apiKey"] == "chave"
            assert "sign" in corpo
            assert corpo["sign"] != "segredo"
        finally:
            feed.parar()


def test_reconecta_e_registra_a_lacuna():
    """A queda tem de virar lacuna, não emenda silenciosa de série."""
    with ServidorFalso(8813) as srv:
        srv.derrubar = True
        feed = cliente_para(8813,
                            canais=canais_publicos(["BTCUSDT"],
                                                   incluir_candle=None))
        feed.iniciar()
        try:
            assert esperar(lambda: srv.conexoes >= 2, limite=8.0), (
                "o cliente não reconectou depois da queda")
            assert esperar(lambda: feed.estado_feed.reconexoes >= 1,
                           limite=8.0)
            assert feed.lacunas(), "reconexão sem lacuna registrada"
        finally:
            feed.parar()


def test_parar_e_idempotente():
    with ServidorFalso(8814):
        feed = cliente_para(8814)
        feed.iniciar()
        assert esperar(lambda: feed.estado_feed.conectado)
        feed.parar()
        assert feed.parar() == "feed parado"


def test_iniciar_duas_vezes_nao_duplica():
    with ServidorFalso(8815):
        feed = cliente_para(8815)
        feed.iniciar()
        try:
            assert "já está rodando" in feed.iniciar()
        finally:
            feed.parar()


def test_estado_serializa():
    feed = FeedWebSocket(relogio=relogio_fixo())
    d = feed.estado()
    assert d["rodando"] is False
    assert d["autenticado"] is False
    assert "conectado" in d and "canais" in d


def test_canal_assinado_aparece_como_velho_antes_da_primeira_mensagem():
    """Um canal assinado que nunca entrega não pode desaparecer do relatório.

    Sem registro prévio a lista de canais velhos vinha vazia, o que se lê
    como "está tudo fresco" quando na verdade nada chegou.
    """
    feed = FeedWebSocket(canais=canais_publicos(["BTCUSDT"],
                                                incluir_candle=None),
                         relogio=relogio_fixo())
    assert feed.estado()["canais_velhos"] == ["ticker:BTCUSDT"]
    assert not feed.confiavel()


def test_queda_antes_de_qualquer_dado_ainda_gera_lacuna():
    """É a lacuna que o REST precisa preencher; perdê-la emenda a série."""
    feed = FeedWebSocket(canais=canais_publicos(["BTCUSDT"],
                                                incluir_candle=None),
                         relogio=relogio_fixo())
    feed.estado_feed.conectou(T0)
    feed.estado_feed.desconectou(T0 + 1_000, "queda")
    feed.estado_feed.conectou(T0 + 5_000)
    lacunas = feed.lacunas()
    assert len(lacunas) == 1
    assert lacunas[0]["canal"] == "ticker:BTCUSDT"
    assert lacunas[0]["duracao_ms"] == 4_000
