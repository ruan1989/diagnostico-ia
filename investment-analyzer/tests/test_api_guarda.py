"""Endpoints da guarda de fase.

A guarda vive no servidor; o painel só mostra. Estes testes garantem que o
contrato entre os dois diz a verdade: quando a API responde que algo está
autorizado, está mesmo.
"""
import pytest
from fastapi.testclient import TestClient

from investai.api import AppState, criar_app
from investai.config import Settings
from investai.exchanges import SyntheticProvider
from investai.strategies import Fase

TOKEN = "token-de-teste-123"


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    origem = Path(__file__).resolve().parent.parent / "data" / "fiis_snapshot.json"
    shutil.copy(origem, tmp_path / "fiis_snapshot.json")
    monkeypatch.setenv("INVESTAI_API_TOKEN", TOKEN)
    monkeypatch.setenv("INVESTAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INVESTAI_SYNTHETIC", "1")
    s = Settings.from_env()
    s.universo = ("BTCUSDT", "ETHUSDT")
    estado = AppState(settings=s, usar_sintetico=True)
    estado.provider = SyntheticProvider(seed=5, agora_ms=1_726_000_000_000)
    estado.hub.provider = estado.provider
    with TestClient(criar_app(estado)) as c:
        c.estado = estado
        yield c


def auth():
    return {"X-API-Token": TOKEN}


def levar_a(estado, fase: Fase) -> str:
    v = estado.strategies.criar("api-teste", {"rsi": 14})
    while v.fase is not fase:
        v = estado.strategies.promover(v.chave)
    return v.chave


def test_guarda_comeca_sem_vinculo(cliente):
    r = cliente.get("/api/guarda")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["guarda"]["chave_vinculada"] is None
    assert corpo["guarda"]["versao"] is None
    assert corpo["propostas_pendentes"] == []


def test_guarda_exige_token(cliente):
    r = cliente.post("/api/guarda/vincular", json={"chave": "x@v1"})
    assert r.status_code in (401, 403)


def test_vincular_chave_inexistente_da_400(cliente):
    r = cliente.post("/api/guarda/vincular", headers=auth(),
                     json={"chave": "fantasma@v9"})
    assert r.status_code == 400
    assert "desconhecida" in r.json()["detail"]


def test_vincular_estrategia_real(cliente):
    chave = levar_a(cliente.estado, Fase.REAL_LIMITADO)
    r = cliente.post("/api/guarda/vincular", headers=auth(),
                     json={"chave": chave})
    assert r.status_code == 200
    assert r.json()["guarda"]["chave_vinculada"] == chave
    assert r.json()["guarda"]["versao"]["fase"] == "real_limitado"


def test_guarda_mostra_o_que_falta_para_fase_baixa(cliente):
    chave = levar_a(cliente.estado, Fase.BACKTEST)
    cliente.post("/api/guarda/vincular", headers=auth(), json={"chave": chave})
    corpo = cliente.get("/api/guarda").json()
    assert corpo["guarda"]["versao"]["faltam_fases"] == [
        "out_of_sample", "paper_trading", "shadow", "assistido",
        "real_limitado"]
    assert corpo["guarda"]["versao"]["operavel_real"] is False


def test_desvincular_volta_ao_estado_seguro(cliente):
    chave = levar_a(cliente.estado, Fase.REAL_LIMITADO)
    cliente.post("/api/guarda/vincular", headers=auth(), json={"chave": chave})
    r = cliente.post("/api/guarda/desvincular", headers=auth())
    assert r.status_code == 200
    assert r.json()["guarda"]["chave_vinculada"] is None


def test_armar_live_sem_vinculo_explica_a_fase(cliente):
    """A mensagem de erro tem de dizer o que fazer, não só que falhou."""
    r = cliente.post("/api/motor/armar-live", headers=auth(),
                     json={"confirmacao": "OPERAR COM DINHEIRO REAL"})
    assert r.status_code == 400
    # Sem chave de API conectada, a chave é o primeiro obstáculo; com ela
    # conectada, a fase. Qualquer das duas mensagens serve, desde que não
    # seja um sucesso.
    assert "chave de API" in r.json()["detail"] or "vinculada" in r.json()["detail"]


def test_confirmar_proposta_inexistente_da_400(cliente):
    r = cliente.post("/api/guarda/confirmar", headers=auth(),
                     json={"client_oid": "iai-nao-existe"})
    assert r.status_code == 400


def test_recusar_proposta_inexistente_da_400(cliente):
    r = cliente.post("/api/guarda/recusar", headers=auth(),
                     json={"client_oid": "iai-nao-existe"})
    assert r.status_code == 400


def test_status_do_motor_inclui_guarda(cliente):
    """O painel lê /api/status; a fase tem de estar visível ali.

    Se a guarda só aparecesse em um endpoint próprio, a tela principal
    poderia mostrar "modo real" sem mostrar que nada está autorizado.
    """
    chave = levar_a(cliente.estado, Fase.SHADOW)
    cliente.post("/api/guarda/vincular", headers=auth(), json={"chave": chave})
    corpo = cliente.get("/api/status").json()
    assert corpo["guarda"]["chave_vinculada"] == chave
    assert corpo["guarda"]["versao"]["fase"] == "shadow"
    assert corpo["guarda"]["versao"]["operavel_real"] is False
    assert corpo["propostas_pendentes"] == []


# =====================================================================
# Endpoints da idempotência
# =====================================================================
def test_envios_comeca_vazio(cliente):
    corpo = cliente.get("/api/envios").json()
    assert corpo["idempotencia"]["pendentes"] == 0
    assert corpo["reconciliacao_subida"]["conferidas"] == 0
    assert corpo["reconciliacao_subida"]["exige_atencao"] is False


def test_envios_mostra_pendencia_gravada(cliente):
    cliente.estado.store.registrar_intencao_envio(
        client_oid="iai-pendente", symbol="BTCUSDT", side="long",
        size=0.001, entry=64000.0, stop_loss=62000.0,
        criado_em=1_700_000_000_000)
    corpo = cliente.get("/api/envios").json()
    assert corpo["idempotencia"]["pendentes"] == 1


def test_reconciliar_exige_token(cliente):
    r = cliente.post("/api/envios/reconciliar")
    assert r.status_code in (401, 403)


def test_reconciliar_sem_conexao_nao_conclui_nada(cliente):
    """Em modo sintético não há consulta de ordem; nada pode ser concluído.

    O endpoint tem de dizer isso, não fingir que conferiu.
    """
    cliente.estado.store.registrar_intencao_envio(
        client_oid="iai-x", symbol="BTCUSDT", side="long", size=0.001,
        entry=64000.0, stop_loss=62000.0, criado_em=1_700_000_000_000)
    corpo = cliente.post("/api/envios/reconciliar", headers=auth()).json()
    res = corpo["resultado"]
    assert res["conferidas"] == 1
    assert res["ausentes"] == []
    assert res["exige_atencao"] is True
    assert cliente.estado.store.envio("iai-x")["estado"] == "pendente"
