"""API HTTP: autenticação, contratos de resposta e as travas do modo real."""
import pytest
from fastapi.testclient import TestClient

from investai.api import AppState, criar_app
from investai.config import Settings
from investai.exchanges import SyntheticProvider
from investai.trading import CONFIRMACAO_LIVE

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
        yield c


def auth():
    return {"X-API-Token": TOKEN}


# ---------------------------------------------------------------- públicos
def test_health(cliente):
    d = cliente.get("/api/health").json()
    assert d["ok"] is True
    assert d["modo_dados"] == "sintetico"


def test_config_traz_aviso_e_limites(cliente):
    d = cliente.get("/api/config").json()
    assert "aviso" in d
    assert d["confirmacao_live"] == CONFIRMACAO_LIVE
    assert d["config"]["risk"]["risco_por_trade_pct"] > 0


def test_painel_e_servido(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert "InvestAI" in r.text


def test_css_e_js_servidos(cliente):
    assert cliente.get("/static/styles.css").status_code == 200
    assert cliente.get("/static/app.js").status_code == 200


# ------------------------------------------------------------ autenticação
@pytest.mark.parametrize("caminho", [
    "/api/motor/iniciar", "/api/motor/parar", "/api/motor/ciclo",
    "/api/motor/fechar-tudo", "/api/risco/rearmar",
])
def test_endpoints_de_mutacao_exigem_token(cliente, caminho):
    assert cliente.post(caminho).status_code == 401


def test_token_errado_e_rejeitado(cliente):
    r = cliente.post("/api/motor/ciclo", headers={"X-API-Token": "errado"})
    assert r.status_code == 401


def test_token_correto_e_aceito(cliente):
    assert cliente.post("/api/motor/ciclo", headers=auth()).status_code == 200


def test_conectar_bitget_exige_token(cliente):
    r = cliente.post("/api/bitget/conectar", json={
        "api_key": "k" * 10, "api_secret": "s" * 10,
        "passphrase": "p", "senha_mestra": "senha_forte_1"})
    assert r.status_code == 401


def test_apagar_credenciais_exige_token(cliente):
    assert cliente.delete("/api/bitget/credenciais").status_code == 401


# -------------------------------------------------------------------- scan
def test_scan_retorna_estrutura_completa(cliente):
    d = cliente.get("/api/scan?symbols=BTCUSDT,ETHUSDT").json()
    assert d["total_analisado"] == 2
    assert {"operaveis", "observacao", "analises", "aviso"} <= set(d)
    for a in d["analises"]:
        assert a["sinal_long"] and a["sinal_short"]


def test_scan_rapido_nao_traz_historico(cliente):
    d = cliente.get("/api/scan?symbols=BTCUSDT&com_historico=false").json()
    assert d["analises"][0]["sinal_long"]["historico"] is None


def test_sinal_expoe_fatores_auditaveis(cliente):
    d = cliente.get("/api/scan?symbols=BTCUSDT&com_historico=false").json()
    s = d["analises"][0]["sinal_long"]
    assert len(s["fatores"]) == 9
    assert sum(f["peso"] for f in s["fatores"]) == pytest.approx(1.0)
    assert all(f["detalhe"] for f in s["fatores"])
    assert s["invalidacao"]


def test_probabilidade_nunca_e_cem_por_cento(cliente):
    """Nenhum caminho da API pode devolver certeza."""
    d = cliente.get("/api/scan?symbols=BTCUSDT,ETHUSDT").json()
    for a in d["analises"]:
        for chave in ("sinal_long", "sinal_short"):
            assert 0.0 < a[chave]["prob_acerto_estimada"] < 1.0


def test_analise_individual(cliente):
    d = cliente.get("/api/analise/btcusdt").json()["analise"]
    assert d["symbol"] == "BTCUSDT"
    assert d["features_por_tf"]


def test_analise_de_par_inexistente(cliente):
    """Símbolo desconhecido tem que dar 404, não uma análise inventada."""
    r = cliente.get("/api/analise/PARINEXISTENTE")
    assert r.status_code == 404


# ---------------------------------------------------------------- backtest
def test_backtest_traz_premissas_explicitas(cliente):
    d = cliente.get("/api/backtest/BTCUSDT?lado=long&barras=1500").json()
    assert "premissas" in d
    assert "abertura seguinte" in d["premissas"]["execucao"]
    assert "stop primeiro" in d["premissas"]["empate_stop_alvo"]
    assert "aviso" in d
    assert d["stats"]["trades"] >= 0


def test_backtest_lado_invalido_recusado(cliente):
    assert cliente.get("/api/backtest/BTCUSDT?lado=neutro").status_code == 422


def test_backtest_barras_fora_da_faixa_recusado(cliente):
    assert cliente.get("/api/backtest/BTCUSDT?barras=10").status_code == 422


# -------------------------------------------------------------------- FIIs
def test_fiis_ranking(cliente):
    d = cliente.get("/api/fiis").json()
    assert d["total"] >= 10
    assert d["fundos"][0]["score"] >= d["fundos"][-1]["score"]
    assert "não é recomendação" in d["aviso"].lower()


def test_fiis_sinaliza_fonte_desatualizada(cliente):
    """O painel precisa desse campo para avisar antes de alguém aportar."""
    assert cliente.get("/api/fiis").json()["fonte"]["desatualizado"] is True


def test_fiis_filtra_por_ticker(cliente):
    d = cliente.get("/api/fiis?tickers=MXRF11,HGLG11").json()
    assert {f["ticker"] for f in d["fundos"]} == {"MXRF11", "HGLG11"}


def test_carteira_fii(cliente):
    d = cliente.post("/api/fiis/carteira", json={"capital": 50000}).json()
    assert d["investido"] <= 50000
    assert d["renda_mensal_estimada"] > 0
    assert d["aviso"]


def test_carteira_capital_invalido_recusado(cliente):
    assert cliente.post("/api/fiis/carteira", json={"capital": -100}).status_code == 422


# ------------------------------------------------------------------- motor
def test_status_inicial_em_simulacao(cliente):
    d = cliente.get("/api/status").json()
    assert d["motor"]["modo"] == "paper"
    assert d["motor"]["armado_live"] is False
    assert d["risco"]["kill_switch"] is False


def test_armar_live_sem_chave_e_bloqueado(cliente):
    r = cliente.post("/api/motor/armar-live", headers=auth(),
                     json={"confirmacao": CONFIRMACAO_LIVE})
    assert r.status_code == 400
    assert "chave de API" in r.json()["detail"]
    assert cliente.get("/api/status").json()["motor"]["modo"] == "paper"


def test_armar_live_com_frase_errada_e_bloqueado(cliente):
    r = cliente.post("/api/motor/armar-live", headers=auth(),
                     json={"confirmacao": "pode ir"})
    assert r.status_code == 400
    assert "confirmação incorreta" in r.json()["detail"]


def test_iniciar_e_parar_motor(cliente):
    assert cliente.post("/api/motor/iniciar", headers=auth()).status_code == 200
    assert cliente.get("/api/status").json()["motor"]["rodando"] is True
    assert cliente.post("/api/motor/parar", headers=auth()).status_code == 200
    assert cliente.get("/api/status").json()["motor"]["rodando"] is False


def test_fechar_par_sem_posicao(cliente):
    r = cliente.post("/api/motor/fechar", headers=auth(),
                     json={"symbol": "BTCUSDT", "fracao": 1.0})
    assert r.status_code == 400


def test_rearmar_kill_switch(cliente):
    assert cliente.post("/api/risco/rearmar", headers=auth()).status_code == 200


def test_ciclo_persiste_sinais_e_eventos(cliente):
    cliente.post("/api/motor/ciclo", headers=auth())
    assert cliente.get("/api/sinais?limite=10").status_code == 200
    assert "eventos" in cliente.get("/api/eventos").json()


def test_trades_vazio_traz_resumo_zerado(cliente):
    d = cliente.get("/api/trades").json()
    assert d["resumo"]["trades"] == 0
    assert d["resumo"]["win_rate"] == 0.0


# --------------------------------------------------------------- bitget
def test_bitget_status_nunca_devolve_segredo(cliente):
    d = cliente.get("/api/bitget/status").json()
    texto = str(d)
    assert "api_secret" not in texto
    assert "passphrase" not in texto.replace("passphrase da API", "")
    assert d["instrucao"]


def test_bitget_status_orienta_sem_permissao_de_saque(cliente):
    d = cliente.get("/api/bitget/status").json()
    assert "saque" in d["instrucao"].lower()
    assert "senha da sua conta nunca é usada" in d["instrucao"].lower()


def test_destravar_com_senha_errada(cliente):
    r = cliente.post("/api/bitget/destravar", headers=auth(),
                     json={"senha_mestra": "senha_qualquer_1"})
    assert r.status_code == 401


def test_conectar_valida_tamanho_minimo(cliente):
    r = cliente.post("/api/bitget/conectar", headers=auth(), json={
        "api_key": "curta", "api_secret": "s", "passphrase": "p",
        "senha_mestra": "curta"})
    assert r.status_code == 422


def test_conectar_salva_no_keystore(cliente, tmp_path):
    r = cliente.post("/api/bitget/conectar", headers=auth(), json={
        "api_key": "bg_chave_de_teste", "api_secret": "secret_de_teste_x",
        "passphrase": "passphrase_teste", "senha_mestra": "senha_mestra_ok",
        "salvar": True})
    assert r.status_code == 200
    d = r.json()
    assert d["salvo_em_keystore"] is True
    assert (tmp_path / "bitget_keystore.json").exists()
    # A resposta traz apenas a máscara, nunca o segredo.
    assert "secret_de_teste_x" not in str(d)


def test_destravar_apos_salvar(cliente):
    cliente.post("/api/bitget/conectar", headers=auth(), json={
        "api_key": "bg_chave_de_teste", "api_secret": "secret_de_teste_x",
        "passphrase": "passphrase_teste", "senha_mestra": "senha_mestra_ok"})
    r = cliente.post("/api/bitget/destravar", headers=auth(),
                     json={"senha_mestra": "senha_mestra_ok"})
    assert r.status_code == 200
    assert r.json()["api_key"].startswith("bg_c")


def test_apagar_credenciais(cliente, tmp_path):
    cliente.post("/api/bitget/conectar", headers=auth(), json={
        "api_key": "bg_chave_de_teste", "api_secret": "secret_de_teste_x",
        "passphrase": "passphrase_teste", "senha_mestra": "senha_mestra_ok"})
    r = cliente.delete("/api/bitget/credenciais", headers=auth())
    assert r.status_code == 200
    assert r.json()["keystore_apagado"] is True
    assert not (tmp_path / "bitget_keystore.json").exists()
