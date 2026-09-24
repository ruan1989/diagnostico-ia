"""Idempotência que atravessa reinício do processo.

O cenário que estes testes reproduzem é o que mais assusta em operação
automatizada: o processo morre entre enviar a ordem e receber a resposta. Na
volta, o sistema não sabe se a ordem chegou.

A regra sob teste é sempre a mesma: **incerteza nunca é resolvida enviando
outra ordem.**
"""
import pytest

from investai.config import ExecutionConfig
from investai.exchanges.base import ExchangeError, ExchangeUnreachable
from investai.models import Regime, Side, Signal, SignalGrade
from investai.risk.manager import DecisaoRisco
from investai.strategies import Fase, StrategyRegistry
from investai.trading import (
    ADOTADA, AUSENTE, CONFIRMADA, FALHOU, ControleIdempotencia, Executor,
    GuardaFase, gerar_client_oid, ordem_esta_viva,
)

AGORA = 1_700_000_000_000


def sinal(entry=64000.0, stop=62720.0, side=Side.LONG, symbol="BTCUSDT"):
    risco = abs(entry - stop)
    alvos = ([entry + risco * 1.8] if side is Side.LONG
             else [entry - risco * 1.8])
    return Signal(symbol=symbol, timeframe="1H", side=side,
                  grade=SignalGrade.A, score=80.0, entry=entry, stop_loss=stop,
                  take_profits=alvos, risk_reward=1.8, atr=entry * 0.01,
                  regime=Regime.TENDENCIA_ALTA)


def decisao(size=0.0039):
    return DecisaoRisco(True, "ok", size=size, notional_usd=size * 64000,
                        risco_usd=5.0, alavancagem=1.0)


class Corretora:
    """Corretora falsa com controle sobre o que acontece em cada envio.

    Os modos cobrem o que de fato acontece em produção:
      `ok`        aceita e registra
      `timeout`   a ordem CHEGA, mas a resposta não volta (o caso perigoso)
      `recusa`    nega explicitamente
      `mudo`      a consulta de ordem falha por rede
    """

    def __init__(self, saldo=1000.0):
        self._saldo = saldo
        self.enviadas: list[dict] = []
        self.livro: dict[str, dict] = {}
        self.modo = "ok"
        self.consultas = 0
        self.consulta_falha = False

    # ---------------------------------------------------------- consultas
    def ordem_por_client_oid(self, symbol, client_oid):
        self.consultas += 1
        if self.consulta_falha:
            raise ExchangeUnreachable("rede fora ao consultar ordem")
        return self.livro.get(client_oid)

    def saldo_usdt(self):
        return self._saldo

    def posicoes(self):
        return []

    def definir_alavancagem(self, symbol, leverage, hold_side=None):
        return {}

    def definir_margin_mode(self, symbol, modo):
        return {}

    def contrato(self, symbol):
        return {"volume_place": 4, "price_place": 1, "min_trade_num": 0.0001,
                "min_trade_usdt": 5.0}

    def fechar_posicao(self, symbol, side, size):
        return {}

    def ajustar_stop(self, symbol, side, novo_stop):
        return {}

    # ------------------------------------------------------------ envio
    def abrir_posicao(self, symbol, side, size, leverage, stop_loss,
                      take_profit, client_oid, margin_mode, preco_limite):
        if self.modo == "recusa":
            raise ExchangeError("40001 parâmetro inválido")
        # A ordem entra no livro ANTES de decidir se a resposta volta: é
        # exatamente o que acontece na corretora real.
        self.enviadas.append({"symbol": symbol, "client_oid": client_oid})
        self.livro[client_oid] = {
            "orderId": f"ord-{len(self.enviadas)}", "clientOid": client_oid,
            "state": "live", "baseVolume": "0",
        }
        if self.modo == "timeout":
            raise ExchangeUnreachable("resposta não chegou")
        return self.livro[client_oid]


class CorretoraCega:
    """Conexão que sabe enviar mas não sabe consultar ordem por clientOid.

    Existe para travar o comportamento nesse caso: sem consulta, não há como
    descartar que a ordem anterior esteja viva, então o envio é recusado.
    Deliberadamente NÃO herda de `Corretora` — herdar traria o método que o
    teste precisa que falte.
    """

    def __init__(self):
        self.enviadas: list[dict] = []

    def saldo_usdt(self):
        return 1000.0

    def posicoes(self):
        return []

    def definir_alavancagem(self, symbol, leverage, hold_side=None):
        return {}

    def definir_margin_mode(self, symbol, modo):
        return {}

    def contrato(self, symbol):
        return {"volume_place": 4, "price_place": 1, "min_trade_num": 0.0001,
                "min_trade_usdt": 5.0}

    def fechar_posicao(self, symbol, side, size):
        return {}

    def ajustar_stop(self, symbol, side, novo_stop):
        return {}

    def abrir_posicao(self, symbol, side, size, leverage, stop_loss,
                      take_profit, client_oid, margin_mode, preco_limite):
        self.enviadas.append({"symbol": symbol, "client_oid": client_oid})
        return {"orderId": "ord-cega", "clientOid": client_oid}


def montar(store, *, fase=Fase.REAL_LIMITADO, corretora=None):
    corretora = corretora or Corretora()
    reg = StrategyRegistry()
    v = reg.criar("idem", {"rsi": 14}, agora_ms=AGORA)
    while v.fase is not fase:
        v = reg.promover(v.chave, agora_ms=AGORA)
    guarda = GuardaFase(reg, capital_usd=1000.0)
    guarda.vincular(v.chave)
    controle = ControleIdempotencia(store, corretora)
    ex = Executor(ExecutionConfig(), backend=corretora, modo="live",
                  guarda=guarda, idempotencia=controle)
    return ex, corretora, controle


# ------------------------------------------------------- caminho normal
def test_primeiro_envio_passa(store):
    ex, corretora, controle = montar(store)
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert res.ok, res.mensagem
    assert len(corretora.enviadas) == 1
    assert res.envio.situacao == "nova"


def test_envio_confirmado_fica_registrado(store):
    ex, _, controle = montar(store)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    ex.abrir(s, decisao(), agora_ms=AGORA)
    registro = store.envio(oid)
    assert registro["estado"] == CONFIRMADA
    assert registro["order_id"] == "ord-1"


def test_intencao_e_gravada_antes_do_envio(store):
    """Se a gravação viesse depois, a janela entre as duas seria um buraco.

    Este teste prova a ordem observando o banco de dentro da chamada à
    corretora: naquele instante a intenção já tem de existir.
    """
    ex, corretora, _ = montar(store)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    visto = {}

    original = corretora.abrir_posicao

    def espiao(*a, **k):
        visto["registro"] = store.envio(oid)
        return original(*a, **k)

    corretora.abrir_posicao = espiao
    ex.abrir(s, decisao(), agora_ms=AGORA)
    assert visto["registro"] is not None, "ordem enviada sem intenção gravada"
    assert visto["registro"]["estado"] == "pendente"


# ------------------------------------------------ o cenário do processo morto
def test_ordem_que_chegou_sem_resposta_nao_e_reenviada(store):
    """O caso perigoso: a ordem chegou, a resposta não voltou.

    Na segunda tentativa, o sistema tem de perguntar à corretora e descobrir
    que a ordem está lá — em vez de mandar outra.
    """
    corretora = Corretora()
    corretora.modo = "timeout"
    ex, corretora, controle = montar(store, corretora=corretora)

    primeira = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not primeira.ok
    assert len(corretora.enviadas) == 1
    assert store.envio(primeira.autorizacao.client_oid)["estado"] == "pendente"

    # Segunda tentativa, mesmo instante → mesmo clientOid.
    corretora.modo = "ok"
    segunda = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not segunda.ok
    assert segunda.envio.situacao == "ja_existe"
    assert len(corretora.enviadas) == 1, "segunda ordem foi enviada"
    assert "CHEGOU" in segunda.mensagem


def test_reconciliacao_na_subida_adota_a_ordem_viva(store):
    """Simula o restart: processo novo, mesmo banco, mesma corretora."""
    corretora = Corretora()
    corretora.modo = "timeout"
    ex, corretora, _ = montar(store, corretora=corretora)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    ex.abrir(s, decisao(), agora_ms=AGORA)

    # Processo novo: controle recriado a partir do banco.
    novo = ControleIdempotencia(store, corretora)
    rel = novo.reconciliar_pendentes(agora_ms=AGORA + 60_000)
    assert rel.conferidas == 1
    assert len(rel.adotadas) == 1
    assert rel.adotadas[0]["client_oid"] == oid
    assert rel.exige_atencao
    assert store.envio(oid)["estado"] == ADOTADA
    assert any("NÃO foram reenviadas" in a for a in rel.avisos)


def test_reconciliacao_marca_ausente_o_que_nao_chegou(store):
    """Ordem que não chegou libera o caminho para nova tentativa."""
    corretora = Corretora()
    ex, corretora, _ = montar(store, corretora=corretora)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    store.registrar_intencao_envio(
        client_oid=oid, symbol=s.symbol, side=s.side.value, size=0.0039,
        entry=s.entry, stop_loss=s.stop_loss, criado_em=AGORA)

    controle = ControleIdempotencia(store, corretora)
    rel = controle.reconciliar_pendentes(agora_ms=AGORA + 1000)
    assert rel.ausentes == [oid]
    assert not rel.exige_atencao
    assert store.envio(oid)["estado"] == AUSENTE

    # E agora o envio passa.
    res = ex.abrir(s, decisao(), agora_ms=AGORA)
    assert res.ok, res.mensagem
    assert len(corretora.enviadas) == 1


def test_reconciliacao_sem_nada_pendente_e_silenciosa(store):
    controle = ControleIdempotencia(store, Corretora())
    rel = controle.reconciliar_pendentes(agora_ms=AGORA)
    assert rel.conferidas == 0
    assert not rel.exige_atencao
    assert rel.avisos == []


# ------------------------------------------- incerteza nunca vira novo envio
def test_consulta_que_falha_bloqueia_o_envio(store):
    """Não saber é diferente de saber que não existe."""
    corretora = Corretora()
    corretora.modo = "timeout"
    ex, corretora, _ = montar(store, corretora=corretora)
    ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert len(corretora.enviadas) == 1

    corretora.consulta_falha = True
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not res.ok
    assert res.envio.situacao == "incerto"
    assert len(corretora.enviadas) == 1


def test_reconciliacao_com_consulta_falhando_nao_conclui_nada(store):
    corretora = Corretora()
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    store.registrar_intencao_envio(
        client_oid=oid, symbol=s.symbol, side=s.side.value, size=0.0039,
        entry=s.entry, stop_loss=s.stop_loss, criado_em=AGORA)
    corretora.consulta_falha = True
    rel = ControleIdempotencia(store, corretora).reconciliar_pendentes(
        agora_ms=AGORA)
    assert len(rel.indeterminadas) == 1
    assert rel.ausentes == []
    assert store.envio(oid)["estado"] == "pendente", \
        "intenção não resolvida não pode ser fechada"


def test_backend_que_nao_sabe_consultar_bloqueia(store):
    """Conexão sem consulta de ordem não pode reenviar às cegas."""
    cega = CorretoraCega()
    assert not hasattr(cega, "ordem_por_client_oid")
    ex, cega, _ = montar(store, corretora=cega)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    store.registrar_intencao_envio(
        client_oid=oid, symbol=s.symbol, side=s.side.value, size=0.0039,
        entry=s.entry, stop_loss=s.stop_loss, criado_em=AGORA)
    res = ex.abrir(s, decisao(), agora_ms=AGORA)
    assert not res.ok
    assert res.envio.situacao == "incerto"
    assert cega.enviadas == []


def test_reconciliacao_sem_consulta_avisa_para_conferir_na_mao(store):
    store.registrar_intencao_envio(
        client_oid="iai-x", symbol="BTCUSDT", side="long", size=0.001,
        entry=64000.0, stop_loss=62000.0, criado_em=AGORA)
    rel = ControleIdempotencia(store, CorretoraCega()).reconciliar_pendentes(
        agora_ms=AGORA)
    assert rel.exige_atencao
    assert any("Confira na" in a for a in rel.avisos)


# ------------------------------------------------- recusa explícita libera
def test_recusa_explicita_permite_nova_tentativa(store):
    """Corretora que nega não deixou ordem viva; tentar de novo é seguro."""
    corretora = Corretora()
    corretora.modo = "recusa"
    ex, corretora, _ = montar(store, corretora=corretora)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    primeira = ex.abrir(s, decisao(), agora_ms=AGORA)
    assert not primeira.ok
    assert store.envio(oid)["estado"] == FALHOU

    corretora.modo = "ok"
    segunda = ex.abrir(s, decisao(), agora_ms=AGORA)
    assert segunda.ok, segunda.mensagem
    assert segunda.envio.situacao == "reenvio_seguro"
    assert len(corretora.enviadas) == 1


def test_ordem_confirmada_nao_e_reenviada(store):
    """Nem com o mesmo clientOid, nem depois de reiniciar."""
    ex, corretora, _ = montar(store)
    s = sinal()
    assert ex.abrir(s, decisao(), agora_ms=AGORA).ok
    segunda = ex.abrir(s, decisao(), agora_ms=AGORA)
    assert not segunda.ok
    assert segunda.envio.situacao == "ja_existe"
    assert "posição dobrada" in segunda.mensagem
    assert len(corretora.enviadas) == 1


def test_janela_de_tempo_diferente_e_ordem_diferente(store):
    """Sinal novo, cinco minutos depois, é outra ordem — e deve passar."""
    ex, corretora, _ = montar(store)
    assert ex.abrir(sinal(), decisao(), agora_ms=AGORA).ok
    assert ex.abrir(sinal(), decisao(), agora_ms=AGORA + 300_000).ok
    assert len(corretora.enviadas) == 2


# -------------------------------------------------- classificação da ordem
@pytest.mark.parametrize("estado", ["live", "new", "partially_filled",
                                    "filled", "init"])
def test_estados_vivos_impedem_reenvio(estado):
    assert ordem_esta_viva({"state": estado})


@pytest.mark.parametrize("estado", ["cancelled", "canceled", "rejected",
                                    "expired"])
def test_estados_mortos_sem_execucao_liberam(estado):
    assert not ordem_esta_viva({"state": estado, "baseVolume": "0"})


def test_cancelada_com_execucao_parcial_conta_como_viva():
    """Cancelar depois de executar parte ainda deixou posição aberta."""
    assert ordem_esta_viva({"state": "cancelled", "baseVolume": "0.002"})


def test_estado_desconhecido_conta_como_viva():
    """Na dúvida sobre o estado, não manda outra ordem."""
    assert ordem_esta_viva({"state": "algo_que_nao_conhecemos"})
    assert ordem_esta_viva({})


def test_volume_ilegivel_nao_derruba_a_classificacao():
    assert not ordem_esta_viva({"state": "cancelled", "baseVolume": "n/d"})


# ----------------------------------------------------------------- inspeção
def test_estado_do_controle(store):
    corretora = Corretora()
    controle = ControleIdempotencia(store, corretora)
    assert controle.estado()["pode_consultar"] is True
    assert controle.estado()["pendentes"] == 0
    store.registrar_intencao_envio(
        client_oid="iai-y", symbol="ETHUSDT", side="short", size=1.0,
        entry=3000.0, stop_loss=3100.0, criado_em=AGORA)
    assert controle.estado()["pendentes"] == 1


def test_sem_controle_o_executor_mantem_comportamento_antigo(store):
    """A camada é opcional; sua ausência não pode mudar o resto."""
    corretora = Corretora()
    reg = StrategyRegistry()
    v = reg.criar("idem", {"rsi": 14}, agora_ms=AGORA)
    while v.fase is not Fase.REAL_LIMITADO:
        v = reg.promover(v.chave, agora_ms=AGORA)
    guarda = GuardaFase(reg, capital_usd=1000.0)
    guarda.vincular(v.chave)
    ex = Executor(ExecutionConfig(), backend=corretora, modo="live",
                  guarda=guarda)
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert res.ok
    assert res.envio is None


# =====================================================================
# Cliente Bitget: "não existe" e "não deu para saber" são respostas
# diferentes, e confundi-las é o que gera ordem duplicada
# =====================================================================
def _cliente_bitget(resposta):
    """Cliente com `_request` trocado por uma função controlada pelo teste."""
    from investai.exchanges.bitget import BitgetClient
    c = BitgetClient(credenciais=None)
    c._request = resposta                               # type: ignore[assignment]
    return c


def test_ordem_inexistente_vira_none():
    def nega(*a, **k):
        raise ExchangeError("43001 the order does not exist")
    c = _cliente_bitget(nega)
    assert c.ordem_por_client_oid("BTCUSDT", "iai-x") is None
    c.close()


def test_rede_fora_na_consulta_propaga():
    """Propagar é obrigatório: quem chamou precisa tratar como incerteza."""
    def cai(*a, **k):
        raise ExchangeUnreachable("não foi possível alcançar a Bitget")
    c = _cliente_bitget(cai)
    with pytest.raises(ExchangeUnreachable):
        c.ordem_por_client_oid("BTCUSDT", "iai-x")
    c.close()


def test_erro_de_permissao_na_consulta_propaga():
    """Chave sem permissão de leitura não é 'ordem não existe'."""
    def nega(*a, **k):
        raise ExchangeError("40014 permissão insuficiente")
    c = _cliente_bitget(nega)
    with pytest.raises(ExchangeError):
        c.ordem_por_client_oid("BTCUSDT", "iai-x")
    c.close()


def test_ordem_encontrada_vira_dicionario():
    c = _cliente_bitget(lambda *a, **k: {"orderId": "9", "state": "live"})
    ordem = c.ordem_por_client_oid("BTCUSDT", "iai-x")
    assert ordem["orderId"] == "9"
    c.close()


def test_resposta_em_lista_e_aceita():
    """A Bitget já respondeu em lista e em objeto neste endpoint."""
    c = _cliente_bitget(lambda *a, **k: [{"orderId": "7", "state": "filled"}])
    assert c.ordem_por_client_oid("BTCUSDT", "iai-x")["orderId"] == "7"
    c.close()


def test_resposta_vazia_vira_none():
    for vazio in ({}, [], None):
        c = _cliente_bitget(lambda *a, **k: vazio)
        assert c.ordem_por_client_oid("BTCUSDT", "iai-x") is None
        c.close()


def test_fills_filtram_pelo_client_oid():
    c = _cliente_bitget(lambda *a, **k: {"fillList": [
        {"clientOid": "iai-x", "baseVolume": "0.001"},
        {"clientOid": "outro", "baseVolume": "0.5"},
    ]})
    fills = c.fills_por_client_oid("BTCUSDT", "iai-x")
    assert len(fills) == 1
    assert fills[0]["baseVolume"] == "0.001"
    c.close()


def test_fills_com_resposta_inesperada_devolve_vazio():
    c = _cliente_bitget(lambda *a, **k: "texto solto")
    assert c.fills_por_client_oid("BTCUSDT", "iai-x") == []
    c.close()
