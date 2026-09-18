"""Integração ponta a ponta.

Os testes por módulo verificam peças. Estes verificam o caminho completo e,
principalmente, que as travas continuam de pé quando as camadas são montadas
juntas — é aí que sistemas de trading costumam afrouxar sem ninguém notar:
cada parte está correta, e a composição libera o que nenhuma delas liberaria.

Cenários cobertos:
    1. ciclo completo de análise, do candle à decisão, com motivo sempre;
    2. pipeline de validação do zero até onde ele para, e por quê;
    3. o caminho para capital real permanece fechado;
    4. halt global derruba tudo, e só a frase exata reabre;
    5. nenhuma superfície da API promete lucro.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from investai.api import AppState, criar_app
from investai.config import Settings
from investai.data.quality import FONTE_NAO_CONFIGURADA
from investai.exchanges import SyntheticProvider
from investai.strategies import Fase
from investai.trading import CONFIRMACAO_LIVE

TOKEN = "token-integracao"
AGORA = 1_726_000_000_000

# Expressões que este sistema não pode produzir em nenhuma tela, log ou
# relatório. A lista existe porque foi exatamente o que se pediu no início:
# "quase 100% de acerto". A resposta honesta é que isso não existe.
PROIBIDAS = (
    "lucro garantido", "lucro quase garantido", "risco zero", "sem risco",
    "operação infalível", "infalível", "certeza de valorização",
    "100% de acerto", "acerto garantido", "ganho garantido",
    "retorno garantido", "não tem como perder",
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    origem = Path(__file__).resolve().parent.parent / "data" / "fiis_snapshot.json"
    shutil.copy(origem, tmp_path / "fiis_snapshot.json")

    monkeypatch.setenv("INVESTAI_API_TOKEN", TOKEN)
    monkeypatch.setenv("INVESTAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INVESTAI_SYNTHETIC", "1")
    s = Settings.from_env()
    s.universo = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    estado = AppState(settings=s, usar_sintetico=True)
    estado.provider = SyntheticProvider(seed=4242, agora_ms=AGORA)
    estado.hub.provider = estado.provider
    with TestClient(criar_app(estado)) as c:
        yield c, estado


def auth():
    return {"X-API-Token": TOKEN}


# =========================================================== 1) ciclo
def test_ciclo_completo_do_candle_a_decisao(app):
    """Cada par percorre qualidade → regime → anomalias → agentes → risco."""
    cliente, _ = app
    d = cliente.get("/api/ciclo").json()

    assert d["total_analisado"] == 3
    assert d["duracao_s"] >= 0
    # Contagem fechada: ninguém desaparece do relatório sem classificação.
    assert (d["total_operavel"] + d["total_em_observacao"]
            + d["total_rejeitado"]) == d["total_analisado"]

    etapas_vistas = set()
    for a in d["analises"]:
        assert a["motivo"], f"{a['symbol']} sem motivo"
        assert a["categoria_motivo"] != "outro", a["symbol"]
        etapas_vistas.add(a["etapa_final"])
        # Preço só existe depois da coleta; antes dela, zero declarado.
        if a["etapa_final"] not in ("normalizacao", "coleta"):
            assert a["preco"] > 0

    # Nenhum par pode parar na normalização: o universo é válido.
    assert "normalizacao" not in etapas_vistas


def test_operavel_exige_a_cadeia_inteira_aprovada(app):
    """Operável só com consenso VALIDADA e Risk Engine aprovado."""
    cliente, _ = app
    d = cliente.get("/api/ciclo").json()
    for a in d["analises"]:
        if not a["operavel"]:
            continue
        assert a["consenso"]["decisao"] == "validada"
        assert a["risco"] is not None and a["risco"]["aprovado"] is True
        assert a["risco"]["size"] > 0
        assert a["qualidade"]["pode_gerar_sinal"] is True
        assert a["anomalias"]["bloqueia_entrada"] is False
        # Alavancagem aprovada nunca coloca a liquidação antes do stop.
        liq = a["risco"]["analise_liquidacao"]
        if liq is not None:
            assert liq["aprovado"] is True
            assert liq["dist_liquidacao_pct"] > liq["dist_stop_pct"]


def test_rejeicao_e_o_resultado_mais_comum_e_isso_e_declarado(app):
    """Um sistema seletivo rejeita a maior parte. O relatório assume isso."""
    cliente, _ = app
    d = cliente.get("/api/ciclo").json()
    rel = cliente.get("/api/relatorio-diario").json()["texto"]
    if d["total_operavel"] == 0:
        assert "CAPITAL PRESERVADO" in d["resumo"]
        assert "NENHUMA OPORTUNIDADE" in rel


def test_ciclo_e_reprodutivel_com_a_mesma_semente(app):
    """Duas chamadas seguidas não podem divergir: decisão instável não é
    auditável, e sem reprodutibilidade não há como investigar um erro."""
    cliente, _ = app
    a = cliente.get("/api/ciclo?symbols=BTCUSDT").json()["analises"][0]
    b = cliente.get("/api/ciclo?symbols=BTCUSDT").json()["analises"][0]
    assert a["decisao"] == b["decisao"]
    assert a["motivo"] == b["motivo"]
    assert a["preco"] == b["preco"]


# ==================================================== 2) validação
def test_pipeline_de_validacao_para_onde_a_evidencia_acaba(app):
    """O resultado importante não é "passou": é onde parou, e por quê."""
    cliente, _ = app
    r = cliente.post("/api/validacao/estrategia", headers=auth(), json={
        "symbol": "BTCUSDT", "timeframe": "1H", "barras": 8000,
        "n_ciclos": 4, "score_minimo": 66.0})
    assert r.status_code == 200
    d = r.json()

    # Qualquer fase alcançada tem que ser uma fase declarada do pipeline.
    assert d["fase_final"] in [f.value for f in Fase]
    # E não pode ser uma das que exigem execução em tempo real.
    assert d["fase_final"] not in ("shadow", "assistido", "real_limitado")

    if d["gate"] is not None:
        gate = d["gate"]
        assert gate["criterios"], "gate sem critérios não decide nada"
        reprovados = [c for c in gate["criterios"] if not c["passou"]]
        if not gate["aprovado"]:
            assert reprovados, "reprovado sem critério reprovado"
            assert gate["resumo"]
            assert gate["reprovacoes"]
            for c in reprovados:
                # Cada reprovação traz o exigido e o medido, lado a lado.
                assert c["exigido"] is not None
                assert c["medido"] is not None


def test_walk_forward_nao_mistura_treino_com_teste(app):
    """Se um trade aparecesse nas duas amostras, o OOS seria fantasia."""
    cliente, _ = app
    d = cliente.post("/api/validacao/estrategia", headers=auth(),
                     json={"barras": 8000, "n_ciclos": 4}).json()
    wf = d.get("walk_forward")
    if not wf:
        pytest.skip("pipeline parou antes do walk-forward")
    assert wf["n_ciclos"] >= 2
    is_trades = wf["stats_in_sample"]["trades"]
    oos_trades = wf["stats_oos"]["trades"]
    assert is_trades > 0
    assert wf["n_trades_oos"] == oos_trades
    # Só o out-of-sample decide; o in-sample existe para medir degradação.
    assert "apenas stats_oos vale para decisão" in wf["observacao"]
    assert "degradacao" in wf
    # Os ciclos registrados são as janelas de TESTE, em ordem cronológica
    # e sem retroceder: um "out-of-sample" que voltasse no tempo mediria o
    # passado com parâmetros ajustados no futuro.
    inicios = [c["janela"]["inicio"] for c in wf["ciclos"]]
    assert inicios == sorted(inicios)
    assert all(c["janela"]["nome"].startswith("oos_") for c in wf["ciclos"])
    # A soma dos trades dos ciclos é o OOS total: a deduplicação garante que
    # nenhuma operação seja contada em dois ciclos.
    assert sum(c["n_trades"] for c in wf["ciclos"]) == wf["n_trades_oos"]


def test_estatistica_do_oos_alimenta_o_agente_quantitativo(app):
    """Antes da validação o quantitativo se abstém; depois, opina."""
    cliente, estado = app
    antes = cliente.get("/api/analise-completa/BTCUSDT").json()["analise"]
    cliente.post("/api/validacao/estrategia", headers=auth(),
                 json={"symbol": "BTCUSDT", "barras": 8000, "n_ciclos": 4})
    depois = cliente.get("/api/analise-completa/BTCUSDT").json()["analise"]

    def quant(a):
        if not a["consenso"]:
            return None
        return next((p for p in a["consenso"]["pareceres"]
                     if p["agente"] == "quantitativo"), None)

    q_antes, q_depois = quant(antes), quant(depois)
    if q_antes is None or q_depois is None:
        pytest.skip("análise não chegou ao consenso")
    assert q_antes["entra_no_calculo"] is False
    assert q_antes["postura"] == "sem_dados"
    # Com amostra registrada ele passa a participar — ou explica por que não.
    if not q_depois["entra_no_calculo"]:
        assert q_depois["dados_faltando"] or q_depois["contraindicacoes"]


def test_amostra_pequena_nunca_promove(app):
    """Doze trades bonitos não podem virar autorização para arriscar."""
    cliente, _ = app
    mc = cliente.post("/api/validacao/monte-carlo", json={
        "retornos_r": [3.0, 3.0, 3.0, -1.0, 3.0, 3.0] * 2,
        "n_simulacoes": 1000, "risco_por_trade_frac": 0.02}).json()
    assert mc["avisos"], "amostra de 12 sem aviso é convite a erro"
    ev = cliente.post("/api/estatistica", headers=auth(), json={
        "symbol": "BTCUSDT", "side": "long",
        "retornos_r": [3.0, 3.0, 3.0, -1.0, 3.0, 3.0] * 2}).json()["ev"]
    assert ev["amostra_suficiente"] is False
    assert ev["ic_win_rate"]["informativo"] is False


# ============================================ 3) caminho para o real
def test_nenhuma_estrategia_chega_a_capital_real_por_backtest(app):
    """A trava central do desenho: histórico não libera dinheiro."""
    cliente, _ = app
    for barras in (8000, 12000):
        cliente.post("/api/validacao/estrategia", headers=auth(),
                     json={"barras": barras, "n_ciclos": 4})
    reg = cliente.get("/api/estrategias").json()
    assert reg["operaveis_em_real"] == []
    for v in reg["versoes"]:
        # Entrar em paper_trading é o prêmio por passar no out-of-sample:
        # a estratégia é ADMITIDA à simulação, não liberada para capital.
        # O que backtest nunca pode alcançar são as fases seguintes, que
        # exigem execução e dias de calendário.
        assert v["operavel_real"] is False, v["chave"]
        assert v["fase"] not in ("shadow", "assistido", "real_limitado"), \
            v["chave"]


def test_modo_real_exige_frase_exata_e_chave(app):
    """Três camadas: token, frase exata, credencial verificada."""
    cliente, _ = app
    assert cliente.post("/api/motor/armar-live",
                        json={"confirmacao": CONFIRMACAO_LIVE}
                        ).status_code == 401
    r = cliente.post("/api/motor/armar-live", headers=auth(),
                     json={"confirmacao": "vai"})
    assert r.status_code == 400 and "confirmação incorreta" in r.json()["detail"]
    r = cliente.post("/api/motor/armar-live", headers=auth(),
                     json={"confirmacao": CONFIRMACAO_LIVE})
    assert r.status_code == 400 and "chave de API" in r.json()["detail"]
    assert cliente.get("/api/status").json()["motor"]["modo"] == "paper"


def test_segredo_nunca_volta_pela_api(app):
    """Varredura ampla: nenhum endpoint devolve secret ou passphrase."""
    cliente, _ = app
    cliente.post("/api/bitget/conectar", headers=auth(), json={
        "api_key": "bg_chave_integracao", "api_secret": "SEGREDO_UNICO_XYZ",
        "passphrase": "PASSPHRASE_UNICA_ABC", "senha_mestra": "senha_mestra_ok",
        "salvar": True})
    caminhos = ["/api/config", "/api/status", "/api/bitget/status",
                "/api/saude", "/api/risco", "/api/dados/cobertura",
                "/api/eventos", "/api/estrategias", "/api/relatorio-diario"]
    for caminho in caminhos:
        corpo = cliente.get(caminho).text
        assert "SEGREDO_UNICO_XYZ" not in corpo, caminho
        assert "PASSPHRASE_UNICA_ABC" not in corpo, caminho
        assert "senha_mestra_ok" not in corpo, caminho


def test_keystore_em_disco_esta_cifrado(app):
    """Quem abrir o arquivo não pode ler a chave."""
    cliente, estado = app
    cliente.post("/api/bitget/conectar", headers=auth(), json={
        "api_key": "bg_chave_integracao", "api_secret": "SEGREDO_UNICO_XYZ",
        "passphrase": "PASSPHRASE_UNICA_ABC", "senha_mestra": "senha_mestra_ok",
        "salvar": True})
    from pathlib import Path
    bruto = (Path(estado.settings.data_dir) / "bitget_keystore.json").read_text()
    assert "SEGREDO_UNICO_XYZ" not in bruto
    assert "PASSPHRASE_UNICA_ABC" not in bruto
    assert "senha_mestra_ok" not in bruto


# ============================================== 4) halt global
def test_halt_atravessa_todas_as_camadas(app):
    """Halt não é um aviso na tela: é veto em toda superfície de entrada."""
    cliente, _ = app
    cliente.post("/api/risco/halt", headers=auth())

    assert cliente.get("/api/risco").json()["halted"] is True
    # ciclo de análise
    d = cliente.get("/api/ciclo").json()
    assert d["total_operavel"] == 0
    # motor de execução
    cliente.post("/api/motor/ciclo", headers=auth())
    status = cliente.get("/api/status").json()
    assert status["posicoes_abertas"] == 0
    assert status["posicoes"] == []
    # saúde reflete o halt no componente de risco
    comps = {c["nome"]: c for c in cliente.get("/api/saude").json()["componentes"]}
    assert comps["motor_de_risco"]["estado"] != "HEALTHY"


def test_retomada_exige_a_frase_e_registra_evento(app):
    cliente, _ = app
    cliente.post("/api/risco/halt", headers=auth())
    # A frase é a fricção; caixa e espaços em volta não são. O que não passa
    # é qualquer outra frase — inclusive uma que só se pareça com ela.
    for frase in ("", "retomar", "RETOMAR", "RETOMAR OPERAÇÃO",
                  "retomar a operacao", "RETOMAR OPERACAO agora"):
        r = cliente.post("/api/risco/retomar", headers=auth(),
                         json={"confirmacao": frase})
        assert r.status_code == 400, frase
        assert cliente.get("/api/risco").json()["halted"] is True

    # Caixa baixa e espaço sobrando são aceitos de propósito: travar a
    # retomada por causa do Caps Lock deixaria o operador sem saída num
    # momento em que ele precisa reagir.
    assert cliente.post("/api/risco/retomar", headers=auth(),
                        json={"confirmacao": "  retomar operacao  "}
                        ).status_code == 200
    assert cliente.get("/api/risco").json()["halted"] is False
    eventos = cliente.get("/api/eventos?limite=100").json()["eventos"]
    assert any("halt" in e["mensagem"].lower()
               or "retomad" in e["mensagem"].lower() for e in eventos)


# ================================= 5) nenhuma promessa em nenhuma tela
@pytest.mark.parametrize("caminho", [
    "/", "/static/app.js", "/static/index.html", "/api/config", "/api/ciclo",
    "/api/scan?symbols=BTCUSDT", "/api/fiis", "/api/relatorio-diario",
    "/api/estrategias/criterios", "/api/dados/cobertura", "/api/risco",
    "/api/saude", "/api/analise-completa/BTCUSDT",
])
def test_nenhuma_superficie_promete_lucro(app, caminho):
    cliente, _ = app
    r = cliente.get(caminho)
    assert r.status_code == 200, caminho
    texto = r.text.lower()
    for proibida in PROIBIDAS:
        # "não existe lucro garantido" é o contrário de uma promessa: o
        # sistema tem que poder NEGAR a expressão.
        pos = texto.find(proibida)
        while pos != -1:
            antes = texto[max(0, pos - 60):pos]
            negado = any(n in antes for n in
                         ("não existe", "nao existe", "não há", "nao ha",
                          "nunca", "não é", "nao e", "sem ", "não prometem",
                          "não promessas", "nenhuma", "não use", "proibid"))
            assert negado, f"{caminho}: '{proibida}' sem negação (…{antes})"
            pos = texto.find(proibida, pos + 1)


def test_todo_numero_de_probabilidade_fica_estritamente_entre_0_e_1(app):
    """Nenhum caminho pode produzir 0% ou 100% de probabilidade."""
    cliente, _ = app
    d = cliente.get("/api/scan?symbols=BTCUSDT,ETHUSDT,SOLUSDT").json()
    for a in d["analises"]:
        for lado in ("sinal_long", "sinal_short"):
            p = a[lado]["prob_acerto_estimada"]
            assert 0.0 < p < 1.0, (a["symbol"], lado, p)


def test_classe_sem_fonte_nunca_recebe_score(app):
    """Ação e ETF não têm conector: a resposta é ausência, não estimativa."""
    cliente, _ = app
    acao = cliente.post("/api/ativos/acao",
                        json={"ticker": "PETR4"}).json()["analise"]
    etf = cliente.post("/api/ativos/etf",
                       json={"ticker": "IVVB11"}).json()["analise"]
    for a in (acao, etf):
        assert a["disponivel"] is False
        assert a["score"] == 0
        assert FONTE_NAO_CONFIGURADA in a["mensagem"]


def test_cobertura_declarada_bate_com_o_que_a_api_recusa(app):
    """Se a matriz diz que há fonte, o analisador tem que responder; se diz
    que não há, tem que recusar. Divergência aqui é dado inventado."""
    cliente, _ = app
    matriz = cliente.get("/api/dados/cobertura").json()["matriz"]
    assert matriz["acao"]["fundamentos"]["disponivel"] is False
    assert matriz["cripto"]["ohlcv"]["disponivel"] is True
    # E o caminho que depende de OHLCV de cripto de fato funciona.
    assert cliente.get("/api/regime/BTCUSDT").status_code == 200


def test_promovida_nao_pode_ser_lida_como_aprovacao_final(app):
    """Subir de fase e ser barrado no gate seguinte são compatíveis.

    O pipeline avança rascunho → backtest → out_of_sample e então esbarra no
    gate para paper_trading. Nesse caso `promovida` é True (avançou) e
    `gate_aprovado` é False (foi barrada). Se a interface lesse `promovida`
    como veredicto, mostraria verde para uma estratégia bloqueada — por isso
    o payload declara os dois campos separadamente.
    """
    cliente, _ = app
    d = cliente.post("/api/validacao/estrategia", headers=auth(),
                     json={"symbol": "BTCUSDT", "barras": 12000,
                           "n_ciclos": 5}).json()
    assert "promovida" in d and "gate_aprovado" in d
    assert isinstance(d["fases_avancadas"], list)
    if d["promovida"]:
        assert d["fases_avancadas"], "promovida sem fase registrada"
        # A fase final tem que ser a última fase alcançada.
        assert d["fase_final"] == d["fases_avancadas"][-1]
    if not d["gate_aprovado"]:
        assert d["gate"] is not None
        assert any(not c["passou"] for c in d["gate"]["criterios"])
        assert any("parou" in a or "permanece" in a for a in d["avisos"])
