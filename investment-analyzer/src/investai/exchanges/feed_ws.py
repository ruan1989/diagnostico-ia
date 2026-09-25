"""Cliente WebSocket da Bitget: público e privado.

O que ele acrescenta ao REST
----------------------------
Latência e completude. Preço por REST chega quando alguém pergunta; por
WebSocket, quando o mercado se move. Para gestão de posição aberta a
diferença é material: um stop conferido a cada 20 segundos pode ser
conferido depois de o preço já ter passado por ele.

O que ele NÃO substitui
-----------------------
O REST continua sendo a fonte de verdade para histórico e para preencher
lacunas. Um feed em tempo real é ótimo enquanto está entregando e perigoso
quando para — ver `feed_estado.py`, onde mora toda a lógica de frescor.

Este módulo cuida só do transporte: conectar, assinar, autenticar no canal
privado, manter vivo, reconectar. As decisões sobre o dado ficam lá.

Autenticação
------------
O canal privado exige login assinado. A assinatura usa o mesmo esquema do
REST (HMAC-SHA256 sobre timestamp + método + caminho), com `/user/verify`
como caminho. A `passphrase` e o `secret` nunca são registrados em log: o
`__repr__` desta classe e todas as mensagens de erro usam a máscara da
chave.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from typing import Any, Callable, Iterable, Sequence

from .feed_estado import EstadoFeed, Mensagem

log = logging.getLogger("investai.ws")

URL_PUBLICA = "wss://ws.bitget.com/v2/ws/public"
URL_PRIVADA = "wss://ws.bitget.com/v2/ws/private"

# Espera entre tentativas de reconexão, em segundos. Cresce e satura: bater
# a cada segundo em um endpoint fora do ar não ajuda e pode virar bloqueio.
ESPERAS_RECONEXAO = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0)

INTERVALO_PING_S = 20.0


def assinar_login(secret: str, timestamp: str) -> str:
    """Assinatura do login do canal privado.

    Mesmo esquema do REST, com `GET /user/verify` como alvo.
    """
    msg = f"{timestamp}GET/user/verify"
    bruto = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.b64encode(bruto).decode()


def _espera(tentativa: int) -> float:
    i = min(tentativa, len(ESPERAS_RECONEXAO) - 1)
    return ESPERAS_RECONEXAO[i]


class FeedWebSocket:
    """Conexão WebSocket com reconexão e detecção de silêncio.

    A interface é síncrona de propósito: o resto do sistema roda em threads,
    e expor `async` aqui obrigaria a contaminar tudo. O laço de eventos vive
    em uma thread própria.
    """

    def __init__(self, url: str = URL_PUBLICA, *,
                 canais: Sequence[dict[str, Any]] = (),
                 credenciais: Any = None,
                 tolerancias_ms: dict[str, int] | None = None,
                 relogio: Callable[[], int] | None = None,
                 conectar_fn: Callable[[str], Any] | None = None):
        self.url = url
        self.canais_pedidos = list(canais)
        self.cred = credenciais
        self._relogio = relogio or (lambda: int(time.time() * 1000))
        # Injetável para teste: permite apontar para um servidor local em
        # vez de exigir a internet.
        self._conectar_fn = conectar_fn
        self.estado_feed = EstadoFeed(tolerancias_ms or {})
        # Os canais assinados são registrados AGORA, não quando a primeira
        # mensagem chegar. Duas razões concretas:
        #
        #  * uma queda antes de qualquer dado precisa gerar lacuna. Sem
        #    registro prévio não havia canal nenhum, e a lacuna — justamente
        #    o buraco que o REST precisa preencher — não era gravada;
        #  * um canal assinado que nunca entrega tem de aparecer como VELHO.
        #    Sem registro, ele simplesmente não existia, e a lista de canais
        #    velhos vinha vazia, o que se lê como "está tudo fresco".
        for pedido in self.canais_pedidos:
            nome = self._nome_canal(pedido)
            if nome:
                self.estado_feed.registrar(
                    nome, tolerancia_ms=(tolerancias_ms or {}).get(nome))
        self.on_mensagem: Callable[[Mensagem], None] | None = None

        self._thread: threading.Thread | None = None
        self._parar = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tarefa: asyncio.Task | None = None
        self._lock = threading.RLock()
        self.autenticado = False
        self.tentativas = 0

    # ------------------------------------------------------------ segurança
    def __repr__(self) -> str:
        chave = "sem chave"
        if self.cred is not None:
            mascara = getattr(self.cred, "mascara", None)
            chave = mascara() if callable(mascara) else "chave conectada"
        return f"<FeedWebSocket {self.url} {chave}>"

    # ------------------------------------------------------------ ciclo
    def iniciar(self) -> str:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return "feed já está rodando"
            self._parar.clear()
            self._thread = threading.Thread(target=self._rodar,
                                            name="investai-ws", daemon=True)
            self._thread.start()
        return f"feed iniciado em {self.url}"

    def parar(self, timeout: float = 5.0) -> str:
        """Interrompe o feed de verdade.

        Sinalizar um `threading.Event` não basta: a thread fica parada dentro
        de `await conexao.recv()`, que não olha a flag. É preciso CANCELAR a
        tarefa no laço de eventos dela. Sem isso, `parar()` retornava na
        hora e a thread continuava viva reconectando — o processo não
        encerrava e o socket vazava.
        """
        self._parar.set()
        loop, tarefa = self._loop, self._tarefa
        if loop is not None and tarefa is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(tarefa.cancel)
            except RuntimeError:
                pass
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)
        self.estado_feed.desconectou(self._relogio(), "parada solicitada")
        return "feed parado"

    @property
    def rodando(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _rodar(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)

        async def principal() -> None:
            # Guardar a tarefa é o que permite `parar()` cancelá-la de fora.
            self._tarefa = asyncio.current_task()
            await self._laco()

        try:
            loop.run_until_complete(principal())
        except asyncio.CancelledError:
            pass
        except Exception as exc:                        # noqa: BLE001
            log.error("laço do feed terminou com erro: %s", exc)
            self.estado_feed.desconectou(self._relogio(), str(exc))
        finally:
            # Drenar antes de fechar. Fechar o laço com tarefas vivas produz
            # "Event loop is closed" solto no stderr e deixa o socket aberto.
            try:
                pendentes = [t for t in asyncio.all_tasks(loop)
                             if not t.done()]
                for t in pendentes:
                    t.cancel()
                if pendentes:
                    loop.run_until_complete(
                        asyncio.gather(*pendentes, return_exceptions=True))
            except Exception:                           # noqa: BLE001
                pass
            try:
                loop.close()
            finally:
                self._loop = None
                self._tarefa = None

    async def _laco(self) -> None:
        while not self._parar.is_set():
            try:
                await self._sessao()
                self.tentativas = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:                    # noqa: BLE001
                self.estado_feed.desconectou(self._relogio(),
                                             f"{type(exc).__name__}: {exc}")
                log.warning("feed caiu: %s", exc)
            if self._parar.is_set():
                break
            espera = _espera(self.tentativas)
            self.tentativas += 1
            await asyncio.sleep(espera)

    async def _abrir(self):
        if self._conectar_fn is not None:
            return await self._conectar_fn(self.url)
        import websockets
        return await websockets.connect(self.url, ping_interval=None,
                                        open_timeout=10, close_timeout=5)

    async def _sessao(self) -> None:
        conexao = await self._abrir()
        agora = self._relogio()
        self.estado_feed.conectou(agora)
        self.autenticado = False
        try:
            if self.cred is not None:
                await self._login(conexao)
            await self._assinar_canais(conexao)
            # `wait` com FIRST_COMPLETED, e não `gather`: quando o servidor
            # fecha, o laço de recepção termina, mas o de ping continua
            # dormindo os 20s do intervalo. Com `gather`, a sessão só
            # terminaria depois disso, e a reconexão levaria vinte segundos
            # a mais do que deveria.
            tarefas = {asyncio.create_task(self._receber(conexao)),
                       asyncio.create_task(self._pingar(conexao))}
            try:
                prontas, pendentes = await asyncio.wait(
                    tarefas, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in tarefas:
                    t.cancel()
                await asyncio.gather(*tarefas, return_exceptions=True)
            for t in prontas:
                exc = t.exception()
                if exc is not None:
                    raise exc
        finally:
            self.estado_feed.desconectou(self._relogio(), "sessão encerrada")
            try:
                await conexao.close()
            except Exception:                           # noqa: BLE001
                pass

    async def _login(self, conexao) -> None:
        ts = str(int(time.time()))
        payload = {
            "op": "login",
            "args": [{
                "apiKey": self.cred.api_key,
                "passphrase": self.cred.passphrase,
                "timestamp": ts,
                "sign": assinar_login(self.cred.api_secret, ts),
            }],
        }
        await conexao.send(json.dumps(payload))

    async def _assinar_canais(self, conexao) -> None:
        if not self.canais_pedidos:
            return
        await conexao.send(json.dumps({"op": "subscribe",
                                       "args": self.canais_pedidos}))

    async def _pingar(self, conexao) -> None:
        """Ping de aplicação.

        A Bitget espera a string literal `ping` e responde `pong`. O ping do
        protocolo WebSocket não serve: ele é respondido pela pilha de rede
        mesmo quando a aplicação do outro lado travou.
        """
        while not self._parar.is_set():
            await asyncio.sleep(INTERVALO_PING_S)
            try:
                await conexao.send("ping")
            except Exception:                           # noqa: BLE001
                return

    async def _receber(self, conexao) -> None:
        async for bruto in conexao:
            if self._parar.is_set():
                return
            self.processar(bruto)

    # ------------------------------------------------------------ mensagens
    def processar(self, bruto: Any) -> Mensagem | None:
        """Interpreta uma mensagem crua. Público para poder ser testado.

        Mensagens de controle (pong, ack de inscrição, login) não viram
        dado: elas confirmam o transporte, não o mercado. Contá-las como
        atividade faria um feed inscrito e mudo parecer saudável.
        """
        agora = self._relogio()
        if isinstance(bruto, bytes):
            bruto = bruto.decode("utf-8", "replace")
        if isinstance(bruto, str):
            texto = bruto.strip()
            if texto == "pong":
                return None
            try:
                corpo = json.loads(texto)
            except ValueError:
                self.estado_feed.erros.append(f"mensagem não-JSON: {texto[:80]}")
                return None
        else:
            corpo = bruto

        if not isinstance(corpo, dict):
            return None

        evento = corpo.get("event")
        if evento == "login":
            self.autenticado = bool(corpo.get("code") in (0, "0", None))
            if not self.autenticado:
                self.estado_feed.erros.append(
                    f"login recusado: {corpo.get('msg', 'sem detalhe')}")
            return None
        if evento in ("subscribe", "unsubscribe"):
            return None
        if evento == "error":
            self.estado_feed.erros.append(
                f"erro do servidor: {corpo.get('msg', corpo)}")
            return None

        arg = corpo.get("arg") or {}
        canal = self._nome_canal(arg)
        dados = corpo.get("data")
        if canal is None or dados is None:
            return None

        seq = corpo.get("ts")
        try:
            seq = int(seq) if seq is not None else None
        except (TypeError, ValueError):
            seq = None

        msg = self.estado_feed.mensagem(canal, dados, agora, sequencia=seq)
        if self.on_mensagem is not None:
            try:
                self.on_mensagem(msg)
            except Exception as exc:                    # noqa: BLE001
                log.warning("callback de mensagem falhou: %s", exc)
        return msg

    @staticmethod
    def _nome_canal(arg: dict[str, Any]) -> str | None:
        canal = arg.get("channel")
        if not canal:
            return None
        inst = arg.get("instId") or arg.get("coin") or ""
        return f"{canal}:{inst}" if inst else str(canal)

    # ------------------------------------------------------------ leitura
    def ultimo(self, canal: str) -> Mensagem | None:
        """Última mensagem do canal, ou None se estiver velha."""
        return self.estado_feed.ultimo(canal, self._relogio())

    def confiavel(self) -> bool:
        return self.estado_feed.confiavel(self._relogio())

    def lacunas(self) -> list[dict[str, Any]]:
        """Intervalos que precisam ser preenchidos por REST."""
        return [g.to_dict() for g in self.estado_feed.consumir_lacunas()]

    def estado(self) -> dict[str, Any]:
        agora = self._relogio()
        return {
            "url": self.url,
            "rodando": self.rodando,
            "autenticado": self.autenticado,
            "tentativas_de_reconexao": self.tentativas,
            "canais_pedidos": self.canais_pedidos,
            **self.estado_feed.estado(agora),
        }


def canais_publicos(symbols: Iterable[str], *,
                    product_type: str = "USDT-FUTURES",
                    incluir_candle: str | None = "1H"
                    ) -> list[dict[str, Any]]:
    """Monta a lista de inscrição para os pares informados."""
    args: list[dict[str, Any]] = []
    for s in symbols:
        symbol = s.upper()
        args.append({"instType": product_type, "channel": "ticker",
                     "instId": symbol})
        if incluir_candle:
            args.append({"instType": product_type,
                         "channel": f"candle{incluir_candle}",
                         "instId": symbol})
    return args


__all__ = [
    "ESPERAS_RECONEXAO", "FeedWebSocket", "INTERVALO_PING_S", "URL_PRIVADA",
    "URL_PUBLICA", "assinar_login", "canais_publicos",
]
