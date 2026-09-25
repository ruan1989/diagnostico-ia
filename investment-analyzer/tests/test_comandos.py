"""Comandos operacionais: status, diagnóstico e parada de emergência.

A parada de emergência é o comando menos usado e o mais importante. Ele
precisa funcionar quando tudo o mais está errado, então os testes aqui
quebram as coisas de propósito.
"""
import pytest
from fastapi.testclient import TestClient

from investai.api import AppState, criar_app
from investai.config import Settings
from investai.exchanges import SyntheticProvider
from investai.models import Regime, Side, Signal, SignalGrade
from investai.ops.comandos import (
    SAIDA_AVISO, SAIDA_BLOQUEIO, SAIDA_OK, CHAVE_TRAVA, Checagem,
    Diagnostico, TravaOperacao, diagnostico, liberar_trava,
    parada_emergencia, status, status_texto,
)
from investai.risk.manager import DecisaoRisco
from investai.strategies import Fase

TOKEN = "token-de-teste-123"
AGORA = 1_700_000_000_000


@pytest.fixture
def estado(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    origem = Path(__file__).resolve().parent.parent / "data" / "fiis_snapshot.json"
    shutil.copy(origem, tmp_path / "fiis_snapshot.json")
    monkeypatch.setenv("INVESTAI_API_TOKEN", TOKEN)
    monkeypatch.setenv("INVESTAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INVESTAI_SYNTHETIC", "1")
    s = Settings.from_env()
    s.universo = ("BTCUSDT", "ETHUSDT")
    st = AppState(settings=s, usar_sintetico=True)
    st.provider = SyntheticProvider(seed=5, agora_ms=1_726_000_000_000)
    st.hub.provider = st.provider
    yield st
    st.store.close()


def sinal(entry=64000.0, stop=62720.0):
    return Signal(symbol="BTCUSDT", timeframe="1H", side=Side.LONG,
                  grade=SignalGrade.A, score=80.0, entry=entry, stop_loss=stop,
                  take_profits=[entry + (entry - stop) * 1.8], risk_reward=1.8,
                  atr=entry * 0.01, regime=Regime.TENDENCIA_ALTA)


# =====================================================================
# Trava de operação
# =====================================================================
def test_trava_comeca_inativa(estado):
    assert TravaOperacao(estado.store).ler().ativa is False


def test_trava_ativa_e_persiste(estado):
    t = TravaOperacao(estado.store)
    t.ativar("teste", agora_ms=AGORA)
    # Outra instância, lendo o mesmo banco — é o que acontece no restart.
    assert TravaOperacao(estado.store).ler().ativa is True
    assert TravaOperacao(estado.store).ler().motivo == "teste"


def test_trava_sobrevive_a_reinicio_do_estado(estado, tmp_path, monkeypatch):
    """Uma parada que se perde no restart é uma pausa, não uma parada."""
    TravaOperacao(estado.store).ativar("queda no meio da noite", agora_ms=AGORA)
    estado.store.close()

    s = Settings.from_env()
    s.universo = ("BTCUSDT",)
    novo = AppState(settings=s, usar_sintetico=True)
    try:
        assert novo.trava.ler().ativa is True
        assert "queda no meio da noite" in novo.trava.ler().motivo
    finally:
        novo.store.close()


def test_trava_liberada_fica_liberada(estado):
    t = TravaOperacao(estado.store)
    t.ativar("teste", agora_ms=AGORA)
    t.liberar(agora_ms=AGORA + 1000)
    assert TravaOperacao(estado.store).ler().ativa is False


def test_registro_corrompido_conta_como_travado(estado):
    """Na dúvida sobre se alguém pediu parada, não se opera."""
    estado.store.set_estado(CHAVE_TRAVA, "{isso nao e json")
    t = TravaOperacao(estado.store).ler()
    assert t.ativa is True
    assert "ilegível" in t.motivo


def test_registro_em_formato_inesperado_conta_como_travado(estado):
    estado.store.set_estado(CHAVE_TRAVA, [1, 2, 3])
    assert TravaOperacao(estado.store).ler().ativa is True


# =====================================================================
# A trava tem dentes: ela barra ordem real
# =====================================================================
def test_trava_bloqueia_ordem_real(estado):
    """É o teste que importa: a trava não é só um aviso na tela."""
    class Corretora:
        def __init__(self):
            self.ordens = []

        def saldo_usdt(self):
            return 1000.0

        def posicoes(self):
            return []

        def definir_alavancagem(self, *a, **k):
            return {}

        def definir_margin_mode(self, *a, **k):
            return {}

        def contrato(self, symbol):
            return {"volume_place": 4, "price_place": 1,
                    "min_trade_num": 0.0001, "min_trade_usdt": 5.0}

        def fechar_posicao(self, *a, **k):
            return {}

        def ajustar_stop(self, *a, **k):
            return {}

        def ordem_por_client_oid(self, symbol, client_oid):
            return None

        def abrir_posicao(self, symbol, side, size, leverage, stop_loss,
                          take_profit, client_oid, margin_mode, preco_limite):
            self.ordens.append(client_oid)
            return {"orderId": "1", "clientOid": client_oid}

    corretora = Corretora()
    estado.executor.backend = corretora
    estado.idempotencia.backend = corretora
    estado.executor.modo = "live"

    v = estado.strategies.criar("trava", {"rsi": 14})
    while v.fase is not Fase.REAL_LIMITADO:
        v = estado.strategies.promover(v.chave)
    estado.guarda.vincular(v.chave)
    estado.guarda.sincronizar_capital(1000.0)

    decisao = DecisaoRisco(True, "ok", size=0.0039, notional_usd=249.6,
                           risco_usd=5.0, alavancagem=1.0)
    # Sem trava, passa.
    assert estado.executor.abrir(sinal(), decisao, agora_ms=AGORA).ok
    assert len(corretora.ordens) == 1

    estado.trava.ativar("parada de teste", agora_ms=AGORA)
    res = estado.executor.abrir(sinal(entry=64100.0), decisao,
                                agora_ms=AGORA + 300_000)
    assert not res.ok
    assert "PARADA DE EMERGÊNCIA" in res.mensagem
    assert len(corretora.ordens) == 1, "ordem saiu com o sistema travado"


def test_trava_ilegivel_bloqueia_ordem_real(estado):
    """Não conseguir ler a trava é motivo para não operar."""
    from investai.trading import GuardaFase

    def trava_quebrada():
        raise RuntimeError("banco inacessível")

    g = GuardaFase(estado.strategies, capital_usd=1000.0,
                   trava=trava_quebrada)
    v = estado.strategies.criar("x", {"a": 1})
    while v.fase is not Fase.REAL_LIMITADO:
        v = estado.strategies.promover(v.chave)
    g.vincular(v.chave)
    aut = g.autorizar(sinal(), 100.0, client_oid="x", agora_ms=AGORA)
    assert not aut.liberado
    assert "trava de operação" in aut.motivo


def test_estado_da_guarda_reporta_a_trava(estado):
    estado.trava.ativar("motivo visível", agora_ms=AGORA)
    est = estado.guarda.estado()
    assert est["trava_ativa"] is True
    assert est["trava_motivo"] == "motivo visível"


# =====================================================================
# Diagnóstico
# =====================================================================
def test_diagnostico_roda_todas_as_checagens(estado):
    d = diagnostico(estado, agora_ms=AGORA)
    nomes = {c.nome for c in d.checagens}
    assert {"banco de dados", "trava de operação", "camada de dados",
            "credencial", "registro de estratégias", "guarda de fase",
            "idempotência de ordens", "gestão de risco", "shadow mode",
            "monitor de saúde"} <= nomes


def test_diagnostico_em_modo_sintetico_bloqueia(estado):
    """Dado simulado não pode habilitar operação real."""
    d = diagnostico(estado, agora_ms=AGORA)
    assert not d.pode_operar_real
    assert any("SINTÉTICO" in c.detalhe for c in d.bloqueios)


def test_diagnostico_ve_a_trava_como_bloqueio(estado):
    estado.trava.ativar("parada ativa", agora_ms=AGORA)
    d = diagnostico(estado, agora_ms=AGORA)
    assert any(c.nome == "trava de operação" and c.bloqueia
               for c in d.bloqueios)


def test_diagnostico_ve_kill_switch(estado):
    estado.risk.estado.kill_switch = True
    estado.risk.estado.motivo_kill = "perda diária no limite"
    d = diagnostico(estado, agora_ms=AGORA)
    assert any("kill switch ACIONADO" in c.detalhe for c in d.bloqueios)


def test_diagnostico_ve_envio_pendente(estado):
    estado.store.registrar_intencao_envio(
        client_oid="iai-p", symbol="BTCUSDT", side="long", size=0.001,
        entry=64000.0, stop_loss=62000.0, criado_em=AGORA)
    d = diagnostico(estado, agora_ms=AGORA)
    assert any("destino desconhecido" in c.detalhe for c in d.bloqueios)


def test_diagnostico_nao_quebra_quando_uma_checagem_explode(estado):
    """Um diagnóstico que morre no meio esconde tudo que vinha depois."""
    class RiscoQuebrado:
        @property
        def estado(self):
            raise RuntimeError("estado corrompido")

    estado.risk = RiscoQuebrado()
    d = diagnostico(estado, agora_ms=AGORA)
    # A checagem seguinte à que explodiu tem de ter rodado.
    assert any(c.nome == "monitor de saúde" for c in d.checagens)
    assert any("estado corrompido" in c.detalhe for c in d.checagens)


def test_codigo_de_saida_distingue_bloqueio_de_aviso():
    d = Diagnostico()
    assert d.codigo_saida == SAIDA_OK
    d.checagens.append(Checagem("a", False, "ruim", bloqueia=False))
    assert d.codigo_saida == SAIDA_AVISO
    d.checagens.append(Checagem("b", False, "pior", bloqueia=True))
    assert d.codigo_saida == SAIDA_BLOQUEIO


def test_texto_do_diagnostico_nao_promete_lucro(estado):
    """Diagnóstico verde não é opinião sobre a estratégia dar dinheiro."""
    d = Diagnostico()
    d.checagens.append(Checagem("tudo", True, "ok"))
    texto = d.texto()
    assert "NÃO é opinião sobre a estratégia dar lucro" in texto


# =====================================================================
# Status
# =====================================================================
def test_status_reporta_o_essencial(estado):
    s = status(estado)
    assert s["modo"] == "paper"
    assert s["armado_live"] is False
    assert s["provider"] == "sintetico"
    assert s["credencial"] == "não conectada"
    assert s["trava_operacao"]["ativa"] is False
    assert s["posicoes_abertas"] == 0


def test_status_mostra_a_trava_em_destaque(estado):
    estado.trava.ativar("parada de teste", agora_ms=AGORA)
    texto = status_texto(status(estado))
    assert "PARADA DE EMERGÊNCIA ATIVA" in texto
    assert "parada de teste" in texto


def test_status_lista_posicoes_abertas(estado):
    decisao = DecisaoRisco(True, "ok", size=0.0039, notional_usd=249.6,
                           risco_usd=5.0, alavancagem=1.0)
    estado.executor.abrir(sinal(), decisao, agora_ms=AGORA)
    s = status(estado)
    assert s["posicoes_abertas"] == 1
    assert s["posicoes"][0]["symbol"] == "BTCUSDT"
    assert "BTCUSDT" in status_texto(s)


def test_status_mostra_a_fase_e_o_que_falta(estado):
    v = estado.strategies.criar("s", {"a": 1})
    while v.fase is not Fase.SHADOW:
        v = estado.strategies.promover(v.chave)
    estado.guarda.vincular(v.chave)
    texto = status_texto(status(estado))
    assert "shadow" in texto
    assert "assistido" in texto      # entre as fases que faltam


# =====================================================================
# Parada de emergência
# =====================================================================
def test_parada_trava_e_desarma(estado):
    res = parada_emergencia(estado, "teste", agora_ms=AGORA)
    assert res["ok"]
    assert res["trava"]["ativa"] is True
    assert estado.executor.modo == "paper"
    assert any("desarmado" in p for p in res["passos"])


def test_parada_fecha_posicoes(estado):
    decisao = DecisaoRisco(True, "ok", size=0.0039, notional_usd=249.6,
                           risco_usd=5.0, alavancagem=1.0)
    estado.executor.abrir(sinal(), decisao, agora_ms=AGORA)
    assert len(estado.executor.posicoes()) == 1
    res = parada_emergencia(estado, "teste", agora_ms=AGORA)
    assert estado.executor.posicoes() == []
    assert res["posicoes_restantes"] == []


def test_parada_pode_travar_sem_fechar(estado):
    """Às vezes fechar a mercado é pior que manter com stop na corretora."""
    decisao = DecisaoRisco(True, "ok", size=0.0039, notional_usd=249.6,
                           risco_usd=5.0, alavancagem=1.0)
    estado.executor.abrir(sinal(), decisao, agora_ms=AGORA)
    res = parada_emergencia(estado, "teste", fechar_posicoes=False,
                            agora_ms=AGORA)
    assert res["trava"]["ativa"] is True
    assert len(estado.executor.posicoes()) == 1
    assert res["posicoes_restantes"] == ["BTCUSDT"]
    assert res["ok"] is False, "não pode reportar sucesso com posição aberta"
    assert "confira na bitget" in res["mensagem"].lower()


def test_trava_vem_antes_do_fechamento(estado):
    """Se fechar falhar, o sistema tem de ficar travado, não meio-aberto.

    Fechar posição é a parte que depende de rede. Se a trava viesse por
    último, uma falha ali deixaria o sistema livre para abrir posição nova.
    """
    def fechar_explode(*a, **k):
        raise RuntimeError("rede fora ao fechar")

    estado.engine.fechar_tudo = fechar_explode
    res = parada_emergencia(estado, "teste", agora_ms=AGORA)
    assert estado.trava.ler().ativa is True
    assert not res["ok"]
    assert any("rede fora ao fechar" in e for e in res["erros"])


def test_parada_registra_evento(estado):
    parada_emergencia(estado, "motivo auditável", agora_ms=AGORA)
    eventos = estado.store.eventos(limite=20)
    assert any("parada de emergência" in e["mensagem"] for e in eventos)


def test_liberar_trava_exige_acao_explicita(estado):
    parada_emergencia(estado, "teste", agora_ms=AGORA)
    res = liberar_trava(estado, agora_ms=AGORA + 1000)
    assert res["trava"]["ativa"] is False
    # Liberar a trava NÃO rearma o modo real.
    assert "ainda precisa ser armado" in res["mensagem"]
    assert estado.executor.modo == "paper"


# =====================================================================
# Endpoints
# =====================================================================
@pytest.fixture
def cliente(estado):
    with TestClient(criar_app(estado)) as c:
        c.estado = estado
        yield c


def auth():
    return {"X-API-Token": TOKEN}


def test_endpoint_diagnostico(cliente):
    corpo = cliente.get("/api/diagnostico").json()
    assert corpo["codigo_saida"] in (SAIDA_OK, SAIDA_AVISO, SAIDA_BLOQUEIO)
    assert "DIAGNÓSTICO DO SISTEMA" in corpo["texto"]
    assert isinstance(corpo["checagens"], list)


def test_endpoint_status_operacional(cliente):
    corpo = cliente.get("/api/operacao/status").json()
    assert corpo["modo"] == "paper"
    assert "STATUS" in corpo["texto"]


def test_endpoint_parada_exige_token(cliente):
    r = cliente.post("/api/operacao/parada-emergencia", json={})
    assert r.status_code in (401, 403)


def test_endpoint_parada_trava(cliente):
    r = cliente.post("/api/operacao/parada-emergencia", headers=auth(),
                     json={"motivo": "pelo painel"})
    assert r.status_code == 200
    assert r.json()["trava"]["ativa"] is True
    assert cliente.estado.trava.ler().ativa is True


def test_endpoint_liberar_exige_frase(cliente):
    cliente.post("/api/operacao/parada-emergencia", headers=auth(), json={})
    r = cliente.post("/api/operacao/liberar-trava", headers=auth(),
                     json={"confirmacao": "sim"})
    assert r.status_code == 400
    assert cliente.estado.trava.ler().ativa is True


def test_endpoint_liberar_com_frase_correta(cliente):
    cliente.post("/api/operacao/parada-emergencia", headers=auth(), json={})
    r = cliente.post("/api/operacao/liberar-trava", headers=auth(),
                     json={"confirmacao": "LIBERAR OPERACAO"})
    assert r.status_code == 200
    assert cliente.estado.trava.ler().ativa is False


# =====================================================================
# Linha de comando
# =====================================================================
def _cli(argv, tmp_path, monkeypatch):
    """Roda um subcomando com o ambiente isolado em tmp_path."""
    import shutil
    import sys
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    shutil.copy(raiz / "data" / "fiis_snapshot.json",
                tmp_path / "fiis_snapshot.json")
    monkeypatch.setenv("INVESTAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INVESTAI_SYNTHETIC", "1")
    monkeypatch.setenv("INVESTAI_API_TOKEN", TOKEN)
    monkeypatch.syspath_prepend(str(raiz / "scripts"))
    if "cli" in sys.modules:
        del sys.modules["cli"]
    from cli import main
    return main(argv)


def test_cli_status_roda(tmp_path, monkeypatch, capsys):
    codigo = _cli(["status"], tmp_path, monkeypatch)
    saida = capsys.readouterr().out
    assert "STATUS" in saida
    assert codigo == 0


def test_cli_diagnostico_devolve_bloqueio_em_sintetico(tmp_path, monkeypatch,
                                                       capsys):
    """Modo sintético é bloqueio para operação real, e a saída diz isso."""
    codigo = _cli(["diagnostico"], tmp_path, monkeypatch)
    saida = capsys.readouterr().out
    assert "DIAGNÓSTICO DO SISTEMA" in saida
    assert codigo == SAIDA_BLOQUEIO


def test_cli_diagnostico_json(tmp_path, monkeypatch, capsys):
    import json
    _cli(["diagnostico", "--json"], tmp_path, monkeypatch)
    corpo = json.loads(capsys.readouterr().out)
    assert "checagens" in corpo
    assert corpo["pode_operar_real"] is False


def test_cli_parada_sem_confirmacao_nao_faz_nada(tmp_path, monkeypatch, capsys):
    """Um comando que gasta dinheiro não dispara por histórico do shell."""
    codigo = _cli(["parar-tudo"], tmp_path, monkeypatch)
    assert codigo == 1
    assert "--sim" in capsys.readouterr().err


def test_cli_parada_com_sim_trava(tmp_path, monkeypatch, capsys):
    codigo = _cli(["parar-tudo", "--sim", "--motivo", "teste cli"],
                  tmp_path, monkeypatch)
    assert codigo == 0
    assert "trava de operação ATIVADA" in capsys.readouterr().out

    # E o status de um processo novo tem de ver a trava.
    _cli(["status"], tmp_path, monkeypatch)
    assert "PARADA DE EMERGÊNCIA ATIVA" in capsys.readouterr().out


def test_cli_status_sinaliza_trava_no_codigo_de_saida(tmp_path, monkeypatch,
                                                      capsys):
    """Para cron: código 2 quando há algo exigindo atenção."""
    _cli(["parar-tudo", "--sim"], tmp_path, monkeypatch)
    capsys.readouterr()
    assert _cli(["status"], tmp_path, monkeypatch) == 2


def test_cli_liberar_exige_sim(tmp_path, monkeypatch, capsys):
    _cli(["parar-tudo", "--sim"], tmp_path, monkeypatch)
    capsys.readouterr()
    assert _cli(["liberar-operacao"], tmp_path, monkeypatch) == 1
    assert _cli(["status"], tmp_path, monkeypatch) == 2   # continua travado


def test_cli_liberar_com_sim(tmp_path, monkeypatch, capsys):
    _cli(["parar-tudo", "--sim"], tmp_path, monkeypatch)
    capsys.readouterr()
    assert _cli(["liberar-operacao", "--sim"], tmp_path, monkeypatch) == 0
    capsys.readouterr()
    assert _cli(["status"], tmp_path, monkeypatch) == 0
