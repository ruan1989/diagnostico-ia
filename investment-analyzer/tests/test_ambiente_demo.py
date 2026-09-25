"""Ambiente de demo trading.

Shadow mode prova que as DECISÕES valem alguma coisa. O que ele não toca é o
encanamento: assinatura aceita, tamanho arredondado ao passo do contrato,
stop anexado à ordem, `clientOid` impedindo duplicata, reconciliação sem
divergência. Nada disso aparece em simulação e tudo isso quebra na primeira
ordem real. Demo é onde quebra sem custar dinheiro.

O risco específico que estes testes cobrem é o acidente de destino: uma
ordem de fase demo chegando à conta real, ou uma ordem de fase real chegando
ao ambiente de teste.
"""
import pytest

from investai.config import ExecutionConfig
from investai.exchanges.ambiente import (
    Ambiente, cabecalhos_de, do_demo, para_demo, product_type_de, resumo,
    traduzir,
)
from investai.exchanges.bitget import BitgetClient
from investai.models import Regime, Side, Signal, SignalGrade
from investai.risk.manager import DecisaoRisco
from investai.strategies import Fase, StrategyRegistry
from investai.trading import Executor, GuardaFase

AGORA = 1_700_000_000_000


# =====================================================================
# Tradução de símbolo e productType
# =====================================================================
@pytest.mark.parametrize("real,demo", [
    ("BTCUSDT", "SBTCSUSDT"),
    ("ETHUSDT", "SETHSUSDT"),
    ("SOLUSDT", "SSOLSUSDT"),
])
def test_traducao_de_simbolo_ida_e_volta(real, demo):
    """A volta importa tanto quanto a ida.

    Um símbolo que vai traduzido e volta diferente produziria posição
    fantasma na reconciliação, todo ciclo.
    """
    assert para_demo(real) == demo
    assert do_demo(demo) == real


def test_traducao_e_idempotente():
    """Traduzir duas vezes não pode dobrar o prefixo."""
    assert para_demo(para_demo("BTCUSDT")) == "SBTCSUSDT"
    assert do_demo(do_demo("SBTCSUSDT")) == "BTCUSDT"


def test_simbolo_real_passa_intacto_pelo_caminho_de_volta():
    assert do_demo("BTCUSDT") == "BTCUSDT"


def test_traduzir_respeita_o_ambiente():
    assert traduzir("BTCUSDT", Ambiente.DEMO) == "SBTCSUSDT"
    assert traduzir("SBTCSUSDT", Ambiente.REAL) == "BTCUSDT"
    assert traduzir("BTCUSDT", Ambiente.REAL) == "BTCUSDT"


def test_product_type_nos_dois_sentidos():
    """O sentido de volta já esteve errado: um cliente saindo do demo
    carregando SUSDT-FUTURES consultaria contas que não existem no real e
    concluiria saldo zero."""
    assert product_type_de("USDT-FUTURES", Ambiente.DEMO) == "SUSDT-FUTURES"
    assert product_type_de("SUSDT-FUTURES", Ambiente.REAL) == "USDT-FUTURES"
    assert product_type_de("USDT-FUTURES", Ambiente.REAL) == "USDT-FUTURES"
    assert product_type_de("SUSDT-FUTURES", Ambiente.DEMO) == "SUSDT-FUTURES"


def test_product_type_de_outra_moeda():
    assert product_type_de("COIN-FUTURES", Ambiente.DEMO) == "SCOIN-FUTURES"
    assert product_type_de("SCOIN-FUTURES", Ambiente.REAL) == "COIN-FUTURES"


def test_cabecalho_so_existe_no_demo():
    """É o cabeçalho que de fato liga o modo demo na Bitget.

    Esquecê-lo não dá erro claro: dá uma ordem real enviada achando que era
    demo.
    """
    assert cabecalhos_de(Ambiente.DEMO) == {"paptrading": "1"}
    assert cabecalhos_de(Ambiente.REAL) == {}


def test_resumo_avisa_o_que_cada_ambiente_significa():
    r = resumo(Ambiente.REAL, "USDT-FUTURES")
    assert "movimenta dinheiro" in r["aviso"]
    d = resumo(Ambiente.DEMO, "USDT-FUTURES")
    assert "NÃO se somam a resultados reais" in d["aviso"]
    assert d["exemplo_simbolo"] == "SBTCSUSDT"


# =====================================================================
# Cliente: as três diferenças precisam andar juntas
# =====================================================================
def test_cliente_real_nao_carrega_nada_de_demo():
    c = BitgetClient()
    try:
        assert not c.e_demo
        assert c.product_type == "USDT-FUTURES"
        assert c._sym("BTCUSDT") == "BTCUSDT"
        assert "paptrading" not in dict(c._client.headers)
    finally:
        c.close()


def test_cliente_demo_traduz_tudo():
    c = BitgetClient(ambiente=Ambiente.DEMO)
    try:
        assert c.e_demo
        assert c.product_type == "SUSDT-FUTURES"
        assert c._sym("BTCUSDT") == "SBTCSUSDT"
        assert dict(c._client.headers)["paptrading"] == "1"
    finally:
        c.close()


def test_cliente_demo_devolve_nome_interno():
    """O resto do sistema não pode ver `SBTCSUSDT`.

    Painel, risco e reconciliação falam do universo interno; um nome de
    corretora vazando para eles quebra as três coisas de formas diferentes.
    """
    c = BitgetClient(ambiente=Ambiente.DEMO)
    try:
        assert c._sym_local("SBTCSUSDT") == "BTCUSDT"
    finally:
        c.close()


def test_posicao_do_demo_volta_com_nome_interno():
    """Sem isso a reconciliação acusaria posição fantasma e ausente juntas."""
    c = BitgetClient(ambiente=Ambiente.DEMO)
    c._request = lambda *a, **k: [{                      # type: ignore
        "symbol": "SBTCSUSDT", "holdSide": "long", "total": "0.01",
        "openPriceAvg": "64000", "presetStopLossPrice": "62000",
        "cTime": str(AGORA), "leverage": "3",
    }]
    try:
        posicoes = c.posicoes()
        assert posicoes[0].symbol == "BTCUSDT"
    finally:
        c.close()


def test_contrato_do_demo_volta_com_nome_interno():
    c = BitgetClient(ambiente=Ambiente.DEMO)
    c._request = lambda *a, **k: [{                      # type: ignore
        "symbol": "SBTCSUSDT", "pricePlace": "1", "volumePlace": "4",
        "minTradeNum": "0.0001", "minTradeUSDT": "5",
    }]
    try:
        assert c.contrato("BTCUSDT")["symbol"] == "BTCUSDT"
    finally:
        c.close()


def test_ticker_do_demo_volta_com_nome_interno():
    c = BitgetClient(ambiente=Ambiente.DEMO)
    c._request = lambda *a, **k: [{"lastPr": "64000"}]   # type: ignore
    try:
        assert c.ticker("BTCUSDT").symbol == "BTCUSDT"
    finally:
        c.close()


def test_lista_de_simbolos_do_demo_volta_traduzida():
    c = BitgetClient(ambiente=Ambiente.DEMO)
    c._request = lambda *a, **k: [                       # type: ignore
        {"symbol": "SBTCSUSDT", "symbolStatus": "normal"},
        {"symbol": "SETHSUSDT", "symbolStatus": "normal"},
    ]
    try:
        assert c.symbols() == ["BTCUSDT", "ETHUSDT"]
    finally:
        c.close()


def test_pedido_ao_demo_leva_o_simbolo_traduzido():
    """O que sai tem de ir traduzido; o que volta tem de voltar destraduzido."""
    visto = {}
    c = BitgetClient(ambiente=Ambiente.DEMO)

    def espiao(metodo, endpoint, **kw):
        visto.update(kw.get("params") or {})
        return [{"lastPr": "64000"}]

    c._request = espiao                                  # type: ignore
    try:
        c.ticker("BTCUSDT")
        assert visto["symbol"] == "SBTCSUSDT"
        assert visto["productType"] == "SUSDT-FUTURES"
    finally:
        c.close()


# =====================================================================
# A fase DEMO e o acidente de destino
# =====================================================================
def sinal():
    return Signal(symbol="BTCUSDT", timeframe="1H", side=Side.LONG,
                  grade=SignalGrade.A, score=80.0, entry=64000.0,
                  stop_loss=62720.0, take_profits=[66304.0], risk_reward=1.8,
                  atr=640.0, regime=Regime.TENDENCIA_ALTA)


def decisao():
    return DecisaoRisco(True, "ok", size=0.0039, notional_usd=249.6,
                        risco_usd=5.0, alavancagem=1.0)


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
        return {"volume_place": 4, "price_place": 1, "min_trade_num": 0.0001,
                "min_trade_usdt": 5.0}

    def fechar_posicao(self, *a, **k):
        return {}

    def ajustar_stop(self, *a, **k):
        return {}

    def abrir_posicao(self, symbol, side, size, leverage, stop_loss,
                      take_profit, client_oid, margin_mode, preco_limite):
        self.ordens.append(client_oid)
        return {"orderId": "1", "clientOid": client_oid}


def guarda_em(fase, *, em_demo):
    reg = StrategyRegistry()
    v = reg.criar("amb", {"a": 1}, agora_ms=AGORA)
    while v.fase is not fase:
        v = reg.promover(v.chave, agora_ms=AGORA)
    g = GuardaFase(reg, capital_usd=1000.0, em_demo=lambda: em_demo)
    g.vincular(v.chave)
    return g


def test_demo_e_uma_fase_do_ciclo_entre_shadow_e_assistido():
    from investai.strategies import ORDEM
    nomes = [f.value for f in ORDEM]
    assert nomes.index("shadow") < nomes.index("demo") < nomes.index("assistido")


def test_fase_demo_com_conexao_real_e_bloqueada():
    """O acidente que o ambiente de teste existe para evitar."""
    corretora = Corretora()
    ex = Executor(ExecutionConfig(), backend=corretora, modo="live",
                  guarda=guarda_em(Fase.DEMO, em_demo=False))
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not res.ok
    assert "aponta para a conta REAL" in res.mensagem
    assert corretora.ordens == []


def test_fase_demo_com_conexao_demo_envia():
    corretora = Corretora()
    ex = Executor(ExecutionConfig(), backend=corretora, modo="live",
                  guarda=guarda_em(Fase.DEMO, em_demo=True))
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert res.ok, res.mensagem
    assert len(corretora.ordens) == 1
    assert "não movimenta dinheiro" in res.autorizacao.motivo


def test_fase_real_com_conexao_demo_e_bloqueada():
    """O espelho: a ordem iria para a conta de teste achando que opera.

    O resultado medido não seria real, e a estratégia seria promovida com
    base em números que não descrevem dinheiro nenhum.
    """
    corretora = Corretora()
    ex = Executor(ExecutionConfig(), backend=corretora, modo="live",
                  guarda=guarda_em(Fase.REAL_LIMITADO, em_demo=True))
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not res.ok
    assert "ambiente de DEMO" in res.mensagem
    assert corretora.ordens == []


def test_fase_real_com_conexao_real_envia():
    corretora = Corretora()
    ex = Executor(ExecutionConfig(), backend=corretora, modo="live",
                  guarda=guarda_em(Fase.REAL_LIMITADO, em_demo=False))
    assert ex.abrir(sinal(), decisao(), agora_ms=AGORA).ok
    assert len(corretora.ordens) == 1


def test_guarda_sem_informacao_de_ambiente_bloqueia_a_fase_demo():
    """Sem saber para onde a conexão aponta, não se envia ordem de demo."""
    reg = StrategyRegistry()
    v = reg.criar("amb", {"a": 1}, agora_ms=AGORA)
    while v.fase is not Fase.DEMO:
        v = reg.promover(v.chave, agora_ms=AGORA)
    g = GuardaFase(reg, capital_usd=1000.0)      # sem `em_demo`
    g.vincular(v.chave)
    aut = g.autorizar(sinal(), 250.0, client_oid="x", agora_ms=AGORA)
    assert not aut.liberado


def test_estado_da_guarda_mostra_o_ambiente():
    g = guarda_em(Fase.DEMO, em_demo=True)
    assert g.estado()["em_demo"] is True
    assert guarda_em(Fase.DEMO, em_demo=False).estado()["em_demo"] is False


# =====================================================================
# Gate de promoção da fase demo
# =====================================================================
def test_gate_demo_mede_encanamento_e_nao_lucro():
    """Exigir expectativa positiva aqui confundiria "o sistema sabe enviar
    ordem" com "a estratégia dá dinheiro" — que as fases anteriores já
    mediram."""
    from investai.strategies import EvidenciaFase, avaliar_gate
    ev = EvidenciaFase(ordens_demo=30, dias_demo=7, taxa_aceite_demo=1.0,
                       divergencias_demo=0,
                       desvio_preenchimento_demo_pct=0.05)
    gate = avaliar_gate(Fase.DEMO, ev)
    assert gate.aprovado, gate.reprovacoes
    assert gate.fase_alvo is Fase.ASSISTIDO
    nomes = {c.nome for c in gate.criterios}
    assert "demo_taxa_aceite" in nomes
    assert not any("expectativa" in n for n in nomes)


def test_gate_demo_sem_medicao_nao_aprova():
    from investai.strategies import EvidenciaFase, avaliar_gate
    gate = avaliar_gate(Fase.DEMO, EvidenciaFase())
    assert not gate.aprovado
    assert "ordens_demo" in gate.dados_faltando


def test_uma_divergencia_de_reconciliacao_reprova():
    """Qualquer divergência aqui vira posição sem gestão no real."""
    from investai.strategies import EvidenciaFase, avaliar_gate
    ev = EvidenciaFase(ordens_demo=30, dias_demo=7, taxa_aceite_demo=1.0,
                       divergencias_demo=1)
    gate = avaliar_gate(Fase.DEMO, ev)
    assert not gate.aprovado
    assert any("divergencias" in r for r in gate.reprovacoes)


def test_ordem_rejeitada_pela_corretora_reprova():
    """Rejeição é defeito de montagem da ordem, e ele se repete no real."""
    from investai.strategies import EvidenciaFase, avaliar_gate
    ev = EvidenciaFase(ordens_demo=30, dias_demo=7, taxa_aceite_demo=0.80,
                       divergencias_demo=0)
    gate = avaliar_gate(Fase.DEMO, ev)
    assert not gate.aprovado
    assert any("taxa_aceite" in r for r in gate.reprovacoes)


def test_desvio_de_preenchimento_alto_reprova():
    """Revela problema de execução mesmo quando dá lucro por sorte."""
    from investai.strategies import EvidenciaFase, avaliar_gate
    ev = EvidenciaFase(ordens_demo=30, dias_demo=7, taxa_aceite_demo=1.0,
                       divergencias_demo=0,
                       desvio_preenchimento_demo_pct=1.5)
    gate = avaliar_gate(Fase.DEMO, ev)
    assert not gate.aprovado


def test_shadow_agora_aponta_para_demo():
    from investai.strategies import CriteriosPromocao, EvidenciaFase, avaliar_gate
    crit = CriteriosPromocao()
    ev = EvidenciaFase(decisoes_shadow=crit.shadow_min_decisoes,
                       dias_shadow=crit.shadow_min_dias,
                       fidelidade_shadow=0.95)
    gate = avaliar_gate(Fase.SHADOW, ev)
    assert gate.fase_alvo is Fase.DEMO
