"""Shadow mode operante: registro persistente e liquidação vela a vela.

O valor do shadow mode está em uma coisa só: a decisão é carimbada ANTES de o
preço andar. Estes testes garantem que o carimbo é real (a liquidação nunca
olha a vela que originou a decisão), que o empate resolve para o lado
pessimista, e que o resumo diz quando ainda não pode concluir nada.
"""
from __future__ import annotations

import pytest

from investai.models import Candle
from investai.ops.shadow_live import (
    MIN_DECISOES, MIN_DIAS, ShadowLive, liquidar,
)

H = 3_600_000


def vela(ts, o, h, l, c):
    return Candle(ts=ts, open=o, high=h, low=l, close=c, volume=100.0)


def decisao(ts=1_000_000, entry=100.0, stop=98.0, alvo=104.0, side="long"):
    return {"id": "d1", "decidido_em": ts, "symbol": "BTCUSDT", "side": side,
            "entry": entry, "stop_loss": stop, "alvo": alvo, "size": 1.0}


# ----------------------------------------------------------- liquidação
def test_alvo_atingido_paga_o_multiplo_do_risco():
    d = decisao()                       # risco 2, alvo a +4 => 2R
    velas = [vela(1_000_000 + H, 100, 101, 99.5, 100),
             vela(1_000_000 + 2*H, 100, 104.5, 99.8, 104)]
    r = liquidar(d, velas)
    assert r.estado == "alvo"
    assert r.resultado_r == pytest.approx(2.0)
    assert r.barras == 2


def test_stop_atingido_custa_exatamente_um_r():
    d = decisao()
    velas = [vela(1_000_000 + H, 100, 100.5, 97.5, 98)]
    r = liquidar(d, velas)
    assert r.estado == "stop"
    assert r.resultado_r == -1.0


def test_empate_na_mesma_vela_resolve_para_o_stop():
    """Sem dado intrabar não dá para saber qual veio primeiro.

    Assumir o alvo produziria exatamente o número bonito que este modo existe
    para não produzir.
    """
    d = decisao()
    velas = [vela(1_000_000 + H, 100, 105, 97, 101)]   # toca alvo E stop
    r = liquidar(d, velas)
    assert r.estado == "stop"
    assert r.resultado_r == -1.0


def test_a_vela_da_decisao_nunca_e_usada():
    """Usá-la seria olhar para dentro da barra que originou o sinal."""
    d = decisao(ts=1_000_000)
    # A vela do próprio instante bate o stop; as seguintes vão ao alvo.
    velas = [vela(1_000_000, 100, 100, 90, 95),
             vela(1_000_000 + H, 100, 104.2, 99.9, 104)]
    r = liquidar(d, velas)
    assert r.estado == "alvo", "a vela da decisão contaminou a liquidação"


def test_short_inverte_os_lados():
    d = decisao(entry=100.0, stop=102.0, alvo=96.0, side="short")
    velas = [vela(1_000_000 + H, 100, 100.5, 95.5, 96)]
    r = liquidar(d, velas)
    assert r.estado == "alvo"
    assert r.resultado_r == pytest.approx(2.0)


def test_sem_resolver_fica_pendente():
    d = decisao()
    velas = [vela(1_000_000 + H, 100, 101, 99, 100)]
    assert liquidar(d, velas).estado == "pendente"


def test_decisao_eterna_expira_a_mercado():
    """Sem prazo, só as que fecham rápido entrariam na conta — viés."""
    d = decisao()
    velas = [vela(1_000_000 + (i+1)*H, 100, 100.9, 99.1, 100.5)
             for i in range(60)]
    r = liquidar(d, velas, max_barras=50)
    assert r.estado == "expirada"
    assert r.barras == 50
    assert r.resultado_r is not None


# ------------------------------------------------ persistência e resumo
class HubFalso:
    def __init__(self, velas): self._v = velas
    def candles(self, symbol, tf, limit=300): return self._v


def test_decisao_sobrevive_a_reinicio(store):
    """O shadow roda semanas; memória volátil perderia a amostra."""
    d = decisao()
    assert store.salvar_decisao_shadow(d) is True
    assert store.decisoes_shadow()[0]["id"] == "d1"


def test_o_mesmo_ciclo_rodado_duas_vezes_nao_duplica(store):
    """Duplicar infla a amostra e melhora a estatística sem novo dado."""
    d = decisao()
    assert store.salvar_decisao_shadow(d) is True
    assert store.salvar_decisao_shadow(dict(d, id="outro")) is False


def test_liquidacao_persiste_e_entra_no_resumo(store):
    store.salvar_decisao_shadow(decisao())
    velas = [vela(1_000_000 + H, 100, 101, 99.5, 100),
             vela(1_000_000 + 2*H, 100, 104.5, 99.8, 104)]
    sh = ShadowLive(store, HubFalso(velas))
    assert sh.liquidar_pendentes() == 1
    r = sh.resumo(agora_ms=1_000_000 + 40*24*H)
    assert r.liquidadas == 1 and r.ganhos == 1
    assert r.expectativa_r == pytest.approx(2.0)
    assert store.decisoes_shadow()[0]["estado"] == "alvo"


def test_resumo_declara_que_ainda_nao_conclui(store):
    """Uma amostra pequena não pode se apresentar como evidência."""
    store.salvar_decisao_shadow(decisao())
    velas = [vela(1_000_000 + H, 100, 104.5, 99.5, 104)]
    sh = ShadowLive(store, HubFalso(velas))
    sh.liquidar_pendentes()
    r = sh.resumo(agora_ms=1_000_000 + H)
    assert r.conclusivo is False
    assert any(str(MIN_DECISOES) in a for a in r.avisos)
    assert any(str(MIN_DIAS) in a for a in r.avisos)


def test_expectativa_negativa_com_amostra_suficiente_e_dita_sem_rodeio(store):
    velas = []
    for i in range(MIN_DECISOES):
        ts = 1_000_000 + i * 24 * H
        store.salvar_decisao_shadow(dict(decisao(ts=ts), id=f"d{i}"))
        velas.append(vela(ts + H, 100, 100.5, 97.5, 98))   # todas no stop
    sh = ShadowLive(store, HubFalso(velas))
    sh.liquidar_pendentes()
    r = sh.resumo(agora_ms=1_000_000 + (MIN_DIAS + 5) * 24 * H)
    assert r.liquidadas == MIN_DECISOES
    assert r.conclusivo is True
    assert r.expectativa_r is not None and r.expectativa_r < 0
    assert any("NÃO deram dinheiro" in a for a in r.avisos)


# ------------------------------------------- ponta a ponta, pela API
def _cliente(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    from fastapi.testclient import TestClient
    from investai.api import AppState, criar_app
    from investai.config import Settings
    from investai.exchanges import SyntheticProvider
    shutil.copy(Path(__file__).resolve().parent.parent / "data" / "fiis_snapshot.json",
                tmp_path / "fiis_snapshot.json")
    monkeypatch.setenv("INVESTAI_API_TOKEN", "tok")
    monkeypatch.setenv("INVESTAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INVESTAI_SYNTHETIC", "1")
    s = Settings.from_env()
    s.universo = ("BTCUSDT", "ETHUSDT")
    estado = AppState(settings=s, usar_sintetico=True)
    estado.provider = SyntheticProvider(seed=99, agora_ms=1_726_000_000_000)
    estado.hub.provider = estado.provider
    return TestClient(criar_app(estado))


def test_a_validacao_destrava_a_cobertura_analitica(tmp_path, monkeypatch):
    """Sem estatística medida o shadow mode nunca encheria.

    O agente quantitativo se abstém enquanto não há amostra, e sem ele a
    cobertura fica abaixo do mínimo: NADA é aprovado, logo nada é registrado.
    Rodar a validação é o que destrava — e é assim que o fluxo foi desenhado.
    """
    c = _cliente(tmp_path, monkeypatch)
    H_ = {"X-API-Token": "tok"}

    antes = c.get("/api/ciclo?symbols=BTCUSDT").json()["analises"][0]
    cob_antes = (antes["consenso"] or {}).get("cobertura")
    assert antes["decisao"] == "dados_insuficientes"

    c.post("/api/validacao/estrategia", headers=H_,
           json={"symbol": "BTCUSDT", "barras": 12000, "n_ciclos": 5})

    depois = c.get("/api/ciclo?symbols=BTCUSDT").json()["analises"][0]
    cob_depois = (depois["consenso"] or {}).get("cobertura")
    assert cob_depois > cob_antes, "a validação não alimentou o quantitativo"
    assert depois["decisao"] != "dados_insuficientes"
    # E o plano existe, mesmo que o consenso ainda não aprove: é ele que
    # torna a decisão conferível depois.
    assert depois["plano"]["stop_loss"] > 0


def test_o_ciclo_registra_o_que_aprova_e_so_isso(tmp_path, monkeypatch):
    """Registrar rejeitadas mediria outra coisa: o que teria virado ordem."""
    from investai.ops.shadow_live import ShadowLive

    class AnaliseFalsa:
        def __init__(self, symbol, operavel):
            self.symbol = symbol
            self.operavel = operavel
            self.preco = 100.0
            self.sinal = type("S", (), {"entry": 100.0, "stop_loss": 98.0,
                                        "take_profits": [104.0, 108.0]})()
            self.consenso = type("C", (), {"direcao": "long", "score": 80.0})()
            self.risco = type("R", (), {"size": 1.5})()

    c = _cliente(tmp_path, monkeypatch)
    estado = c.app.state.investai
    sh = ShadowLive(estado.store, estado.hub)

    novos = sh.registrar_ciclo(
        [AnaliseFalsa("BTCUSDT", True), AnaliseFalsa("ETHUSDT", False)],
        agora_ms=1_726_000_000_000)

    assert len(novos) == 1, "aprovou uma, deveria registrar uma"
    guardadas = estado.store.decisoes_shadow()
    assert [d["symbol"] for d in guardadas] == ["BTCUSDT"]
    d = guardadas[0]
    assert d["entry"] == 100.0 and d["stop_loss"] == 98.0 and d["alvo"] == 104.0
    assert d["size"] == 1.5 and d["estado"] == "pendente"


def test_shadow_aparece_no_payload_do_ciclo(tmp_path, monkeypatch):
    c = _cliente(tmp_path, monkeypatch)
    d = c.get("/api/ciclo?symbols=BTCUSDT").json()
    assert "shadow" in d
    assert set(d["shadow"]) == {"registradas", "liquidadas"}


def test_analise_expoe_o_plano_de_trade(tmp_path, monkeypatch):
    c = _cliente(tmp_path, monkeypatch)
    a = c.get("/api/analise-completa/BTCUSDT").json()["analise"]
    if a["plano"] is None:
        pytest.skip("análise parou antes de montar o plano")
    p = a["plano"]
    assert p["entry"] > 0 and p["stop_loss"] > 0 and p["take_profits"]
    assert p["risk_reward"] > 0


def test_shadow_pode_ser_desligado(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTAI_SHADOW", "0")
    c = _cliente(tmp_path, monkeypatch)
    assert c.get("/api/shadow").json()["ligado"] is False
    assert "shadow" not in c.get("/api/ciclo?symbols=BTCUSDT").json()
