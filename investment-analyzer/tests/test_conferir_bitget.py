"""O verificador do conector Bitget precisa DETECTAR problema, não só dizer ok.

Um health check que sempre passa é pior do que nenhum: ele dá permissão para
confiar em dados que podem estar errados. Estes testes sobem um servidor
local que imita a API v2 da Bitget e verificam que cada forma de quebra
conhecida é apanhada.

As quebras simuladas não são hipotéticas — são as que de fato acontecem
quando uma exchange muda a API: relógio fora de sincronia (assinatura
recusada), nome de campo alterado (preço vira zero), paginação reinterpretada
(histórico repetido em vez de estendido) e granularidade diferente da pedida
(todo indicador medindo outro período).
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

PASSO_1H = 3_600_000


def _linhas(n: int, fim_ms: int | None = None,
            passo: int = PASSO_1H) -> list[list[str]]:
    fim = fim_ms or int(time.time() * 1000)
    fim -= fim % passo
    saida = []
    for i in range(n):
        ts = fim - (n - i) * passo
        base = 60_000 + i * 10
        saida.append([str(ts), str(base), str(base + 120), str(base - 90),
                      str(base + 30), "1234.5", "74000000"])
    return saida


def _fabricar_handler(modo: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):      # silencia o log do servidor
            pass

        def _responder(self, data):
            corpo = json.dumps({"code": "00000", "msg": "success",
                                "data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def do_GET(self):                                     # noqa: N802
            from urllib.parse import parse_qs, urlparse
            u = urlparse(self.path)
            q = parse_qs(u.query)
            agora = int(time.time() * 1000)

            if u.path == "/api/v2/public/time":
                desvio = 300_000 if modo == "relogio" else 0
                return self._responder({"serverTime": str(agora + desvio)})

            if u.path == "/api/v2/mix/market/contracts":
                c = {"symbol": "BTCUSDT", "symbolStatus": "normal",
                     "pricePlace": "1", "volumePlace": "3",
                     "sizeMultiplier": "0.001", "minTradeNum": "0.001",
                     "minTradeUSDT": "5", "maxLever": "125"}
                if q.get("symbol"):
                    return self._responder([c])
                return self._responder([c, {**c, "symbol": "ETHUSDT"}])

            if u.path == "/api/v2/mix/market/ticker":
                if modo == "campo":
                    # A exchange renomeou lastPr; o cliente lê zero.
                    return self._responder([{"symbol": "BTCUSDT",
                                             "price": "61000"}])
                return self._responder([{"symbol": "BTCUSDT",
                                         "lastPr": "61030",
                                         "usdtVolume": "890000000",
                                         "fundingRate": "0.0001",
                                         "holdingAmount": "51000"}])

            if u.path == "/api/v2/mix/market/candles":
                fim = q.get("endTime", [None])[0]
                passo = 900_000 if modo == "granularidade" else PASSO_1H
                if modo == "paginacao" and fim:
                    # Ignora endTime: devolve sempre a página mais recente.
                    return self._responder(_linhas(200, None, passo))
                linhas = _linhas(200, int(fim) if fim else None, passo)
                if modo == "ordem":
                    # A Bitget devolve do mais novo para o mais antigo: é o
                    # caso NORMAL, e o cliente reordena. Não pode virar falha.
                    linhas = list(reversed(linhas))
                return self._responder(linhas)

            if u.path == "/api/v2/mix/market/current-fund-rate":
                return self._responder([{"symbol": "BTCUSDT",
                                         "fundingRate": "0.0001"}])

            self._responder([])

    return Handler


@pytest.fixture
def bitget_falso():
    """Sobe um servidor que imita a API v2 e devolve a url base."""
    servidores = []

    def subir(modo: str) -> str:
        srv = HTTPServer(("127.0.0.1", 0), _fabricar_handler(modo))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servidores.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield subir
    for srv in servidores:
        srv.shutdown()


def _conferir(base_url: str, monkeypatch) -> tuple[int, str]:
    import cli
    # O cliente usa httpx, que honra proxy do ambiente; 127.0.0.1 não deve
    # passar por proxy nenhum.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    import io
    import contextlib
    buf = io.StringIO()
    args = cli.construir().parse_args(
        ["conferir-bitget", "--base-url", base_url])
    with contextlib.redirect_stdout(buf):
        codigo = args.func(args)
    return codigo, buf.getvalue()


def test_api_saudavel_passa(bitget_falso, monkeypatch):
    codigo, saida = _conferir(bitget_falso("saudavel"), monkeypatch)
    assert codigo == 0, saida
    assert "Conector OK" in saida
    # E não promete mais do que testou.
    assert "Envio de ordem exige chave" in saida


def test_candles_do_mais_novo_para_o_mais_antigo_nao_e_falha(
        bitget_falso, monkeypatch):
    """É como a Bitget responde de verdade, e o cliente já reordena.

    Se isto reprovasse, o verificador acusaria a operação normal da exchange.
    """
    codigo, saida = _conferir(bitget_falso("ordem"), monkeypatch)
    assert codigo == 0, saida


@pytest.mark.parametrize("modo,trecho", [
    ("relogio", "relógio local"),
    ("campo", "preço zero ou ausente"),
    ("paginacao", "não é anterior"),
    ("granularidade", "espaçamento inesperado"),
])
def test_cada_quebra_conhecida_e_detectada(bitget_falso, monkeypatch,
                                           modo, trecho):
    codigo, saida = _conferir(bitget_falso(modo), monkeypatch)
    assert codigo == 1, f"quebra '{modo}' passou despercebida:\n{saida}"
    assert trecho in saida, saida
    # E a saída precisa dizer o que fazer, não só que falhou.
    assert "NÃO use dados reais" in saida


def test_relogio_dessincronizado_explica_a_consequencia(bitget_falso,
                                                        monkeypatch):
    """Relógio fora do ar é a causa clássica de "assinatura inválida"."""
    _, saida = _conferir(bitget_falso("relogio"), monkeypatch)
    assert "assinatura" in saida


# ------------------------------------------------- rede fora do ar
def test_exchange_inalcancavel_nao_vira_defeito_do_conector(monkeypatch):
    """Nenhuma chamada chegando = problema de rede, e o texto precisa dizer.

    Um proxy corporativo, um firewall ou o bloqueio por região da própria
    Bitget produzem exatamente isto. Se a saída falasse só em "falha", o
    operador iria procurar defeito no código — onde não há nenhum.
    """
    import cli
    import contextlib
    import io
    import socket

    # Uma porta sem ninguém escutando: a conexão é recusada de imediato.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()

    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)

    args = cli.construir().parse_args([
        "conferir-bitget", "--base-url", f"http://127.0.0.1:{porta}",
        "--tentativas", "1"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        codigo = args.func(args)
    saida = buf.getvalue()

    # Código 2 separa "não cheguei lá" de 1, que é "cheguei e algo está errado".
    assert codigo == 2, saida
    assert "bloqueio de REDE" in saida
    assert "não defeito do conector" in saida
    assert "SEM CONEXÃO" in saida
    # E oferece uma saída imediata que não depende de rede.
    assert "INVESTAI_SYNTHETIC=1" in saida


def test_erro_vindo_da_exchange_nao_e_tratado_como_rede(bitget_falso,
                                                        monkeypatch):
    """Alcançar a Bitget e receber erro é outro problema, com outro código."""
    codigo, saida = _conferir(bitget_falso("campo"), monkeypatch)
    assert codigo == 1, saida
    assert "bloqueio de REDE" not in saida


def test_exchange_unreachable_e_subclasse_de_exchange_error():
    """Quem já trata ExchangeError continua tratando — sem regressão."""
    from investai.exchanges import ExchangeError, ExchangeUnreachable
    assert issubclass(ExchangeUnreachable, ExchangeError)
