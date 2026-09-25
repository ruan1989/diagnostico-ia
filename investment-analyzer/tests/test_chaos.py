"""Testes de chaos: quebrar de propósito e conferir o que sobra consistente.

O que estes testes medem não é "funciona", é "quando falha, falha de que
jeito". Um sistema que mexe em dinheiro é julgado pelo pior dia, não pelo
normal, e o pior dia sempre envolve alguma combinação de: processo morto no
meio de um envio, rede caindo entre a ordem e a resposta, relógio deslocado,
resposta truncada, banco travado.

As invariantes que nenhum cenário pode violar
---------------------------------------------
1. **nunca duas ordens para o mesmo sinal.** Nem por reenvio, nem por
   reinício, nem por consulta que falhou;
2. **incerteza nunca vira envio.** Não conseguir saber se a ordem chegou
   tem de bloquear, não liberar;
3. **a parada de emergência sobrevive a tudo.** Reinício, banco corrompido,
   registro ilegível;
4. **estado não conferido nunca é reportado como conferido.**

Cada teste abaixo escolhe uma forma de quebrar e confere uma dessas.
"""
import sqlite3
import threading

import pytest

from investai.config import ExecutionConfig
from investai.exchanges.base import ExchangeError, ExchangeUnreachable
from investai.models import Position, Regime, Side, Signal, SignalGrade
from investai.ops.comandos import TravaOperacao, parada_emergencia
from investai.ops.reconciliacao import Reconciliador, reconciliar
from investai.risk.manager import DecisaoRisco
from investai.store import Store
from investai.strategies import Fase, StrategyRegistry
from investai.trading import (
    ControleIdempotencia, Executor, GuardaFase, gerar_client_oid,
)

AGORA = 1_700_000_000_000


def sinal(entry=64000.0, symbol="BTCUSDT"):
    return Signal(symbol=symbol, timeframe="1H", side=Side.LONG,
                  grade=SignalGrade.A, score=80.0, entry=entry,
                  stop_loss=entry * 0.98, take_profits=[entry * 1.04],
                  risk_reward=1.8, atr=entry * 0.01,
                  regime=Regime.TENDENCIA_ALTA)


def decisao(size=0.0039):
    return DecisaoRisco(True, "ok", size=size, notional_usd=size * 64000,
                        risco_usd=5.0, alavancagem=1.0)


class CorretoraCaotica:
    """Corretora que falha do jeito que se pede.

    `modo` controla o que acontece no envio:
      ok        aceita
      timeout   a ordem CHEGA e a resposta não volta
      truncada  responde algo que não é o esperado
      recusa    nega explicitamente
      lenta     aceita mas a consulta posterior falha
    """

    def __init__(self, saldo=1000.0):
        self._saldo = saldo
        self.enviadas: list[str] = []
        self.livro: dict[str, dict] = {}
        self.modo = "ok"
        self.consulta_falha = False
        self.posicoes_falha = False
        self._posicoes: list[Position] = []

    def saldo_usdt(self):
        return self._saldo

    def posicoes(self):
        if self.posicoes_falha:
            raise ExchangeUnreachable("rede fora ao ler posições")
        return list(self._posicoes)

    def ordem_por_client_oid(self, symbol, client_oid):
        if self.consulta_falha:
            raise ExchangeUnreachable("rede fora ao consultar ordem")
        return self.livro.get(client_oid)

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
        if self.modo == "recusa":
            raise ExchangeError("40001 parâmetro inválido")
        # A ordem entra no livro ANTES de decidir se a resposta volta, que é
        # exatamente o que acontece na corretora real.
        self.enviadas.append(client_oid)
        self.livro[client_oid] = {
            "orderId": f"ord-{len(self.enviadas)}", "clientOid": client_oid,
            "state": "live", "baseVolume": "0"}
        self._posicoes.append(Position(
            symbol=symbol, side=side, size=size, entry=64000.0,
            stop_loss=stop_loss, take_profits=[], opened_at=AGORA,
            modo="live"))
        if self.modo == "timeout":
            raise ExchangeUnreachable("resposta não chegou")
        if self.modo == "truncada":
            return "isto não é um dicionário"
        return self.livro[client_oid]


def montar(store, corretora, fase=Fase.REAL_LIMITADO):
    reg = StrategyRegistry()
    v = reg.criar("chaos", {"a": 1}, agora_ms=AGORA)
    while v.fase is not fase:
        v = reg.promover(v.chave, agora_ms=AGORA)
    trava = TravaOperacao(store)
    guarda = GuardaFase(reg, capital_usd=1000.0,
                        trava=lambda: (lambda t: (t.ativa, t.motivo))(
                            trava.ler()))
    guarda.vincular(v.chave)
    controle = ControleIdempotencia(store, corretora)
    ex = Executor(ExecutionConfig(), backend=corretora, modo="live",
                  guarda=guarda, idempotencia=controle)
    return ex, controle, trava


# =====================================================================
# 1. Processo morto no meio de um envio
# =====================================================================
def test_processo_morre_entre_enviar_e_responder(store):
    """O cenário que mais assusta, reproduzido inteiro.

    Processo 1 envia e morre sem resposta. Processo 2 sobe com o mesmo
    banco e a mesma corretora. Nenhuma segunda ordem pode sair.
    """
    corretora = CorretoraCaotica()
    corretora.modo = "timeout"
    ex1, _, _ = montar(store, corretora)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)

    res1 = ex1.abrir(s, decisao(), agora_ms=AGORA)
    assert not res1.ok
    assert corretora.enviadas == [oid]

    # --- morte do processo 1; processo 2 sobe do mesmo banco ---
    corretora.modo = "ok"
    ex2, controle2, _ = montar(store, corretora)
    rel = controle2.reconciliar_pendentes(agora_ms=AGORA + 60_000)
    assert len(rel.adotadas) == 1
    assert rel.exige_atencao

    res2 = ex2.abrir(s, decisao(), agora_ms=AGORA)
    assert not res2.ok
    assert corretora.enviadas == [oid], "segunda ordem foi enviada"


def test_morte_repetida_nunca_acumula_ordens(store):
    """Dez reinícios seguidos, uma ordem só.

    Não é exagero: um processo que reinicia em laço por causa de um erro de
    configuração é comum, e cada volta é uma chance de duplicar posição.
    """
    corretora = CorretoraCaotica()
    corretora.modo = "timeout"
    s = sinal()
    for _ in range(10):
        ex, controle, _ = montar(store, corretora)
        controle.reconciliar_pendentes(agora_ms=AGORA)
        ex.abrir(s, decisao(), agora_ms=AGORA)
    assert len(corretora.enviadas) == 1


def test_morte_com_consulta_tambem_fora_nao_envia(store):
    """Pior combinação: a ordem pode estar viva e a consulta não responde."""
    corretora = CorretoraCaotica()
    corretora.modo = "timeout"
    ex1, _, _ = montar(store, corretora)
    ex1.abrir(sinal(), decisao(), agora_ms=AGORA)

    corretora.consulta_falha = True
    ex2, controle2, _ = montar(store, corretora)
    rel = controle2.reconciliar_pendentes(agora_ms=AGORA)
    assert len(rel.indeterminadas) == 1
    assert rel.ausentes == []

    res = ex2.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not res.ok
    assert res.envio.situacao == "incerto"
    assert len(corretora.enviadas) == 1


# =====================================================================
# 2. Respostas estranhas da corretora
# =====================================================================
def test_resposta_truncada_nao_quebra_o_registro(store):
    """A corretora respondeu algo inesperado. A ordem saiu mesmo assim."""
    corretora = CorretoraCaotica()
    corretora.modo = "truncada"
    ex, _, _ = montar(store, corretora)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    res = ex.abrir(s, decisao(), agora_ms=AGORA)
    assert res.ok
    # Registro marcado como confirmado, sem order_id — porque não veio.
    registro = store.envio(oid)
    assert registro["estado"] == "confirmada"
    assert registro["order_id"] == ""
    # E um reenvio continua bloqueado.
    assert not ex.abrir(s, decisao(), agora_ms=AGORA).ok
    assert len(corretora.enviadas) == 1


def test_ordem_com_estado_desconhecido_impede_reenvio(store):
    """Na dúvida sobre o estado da ordem, não se manda outra."""
    corretora = CorretoraCaotica()
    corretora.modo = "timeout"
    ex, _, _ = montar(store, corretora)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    ex.abrir(s, decisao(), agora_ms=AGORA)
    corretora.livro[oid]["state"] = "estado_que_nao_conhecemos"
    corretora.modo = "ok"
    assert not ex.abrir(s, decisao(), agora_ms=AGORA).ok
    assert len(corretora.enviadas) == 1


def test_recusa_explicita_permite_tentar_de_novo(store):
    """Recusa não deixou ordem viva; travar aqui perderia oportunidade à toa."""
    corretora = CorretoraCaotica()
    corretora.modo = "recusa"
    ex, _, _ = montar(store, corretora)
    s = sinal()
    assert not ex.abrir(s, decisao(), agora_ms=AGORA).ok
    corretora.modo = "ok"
    assert ex.abrir(s, decisao(), agora_ms=AGORA).ok
    assert len(corretora.enviadas) == 1


# =====================================================================
# 3. Relógio deslocado
# =====================================================================
def test_idempotencia_nao_cobre_relogio_andando_para_tras(store):
    """Onde a trava de idempotência NÃO protege, e por quê.

    O `clientOid` é derivado de uma janela de tempo. Se o NTP corrige o
    relógio cinco minutos para trás, o mesmo sinal cai em outra janela e
    gera outro id — que é, para a camada de idempotência, uma ordem
    diferente. Ela não tem como saber que não é.

    Registrar isso como teste é o ponto: a proteção contra este caso existe,
    mas mora em outro lugar (ver o teste seguinte). Fingir que a
    idempotência cobre tudo esconderia a única camada que de fato cobre.
    """
    corretora = CorretoraCaotica()
    ex, _, _ = montar(store, corretora)
    s = sinal()
    assert ex.abrir(s, decisao(), agora_ms=AGORA).ok
    # Mesma janela: bloqueado.
    assert not ex.abrir(s, decisao(), agora_ms=AGORA).ok
    # Janela anterior: passa, porque é outro id.
    assert ex.abrir(s, decisao(), agora_ms=AGORA - 300_000).ok
    assert len(corretora.enviadas) == 2


def test_gestao_de_risco_barra_segunda_posicao_no_mesmo_par(store):
    """A camada que cobre o relógio deslocado — e qualquer sinal repetido.

    O risco recebe as posições abertas e recusa entrada em par que já tem
    posição. É upstream do executor, então vale independentemente do id da
    ordem e do relógio.
    """
    from investai.config import RiskConfig
    from investai.risk import RiskManager

    risco = RiskManager(RiskConfig(), 1000.0)
    s = sinal()
    aberta = Position(symbol="BTCUSDT", side=Side.LONG, size=0.01,
                      entry=64000.0, stop_loss=62000.0, take_profits=[],
                      opened_at=AGORA, modo="live")
    d = risco.avaliar_entrada(s, [aberta], agora_ms=AGORA)
    assert not d.aprovado
    assert any("já existe posição aberta em BTCUSDT" in b
               for b in d.bloqueios)

    # E o executor respeita a recusa do risco antes de qualquer outra coisa.
    corretora = CorretoraCaotica()
    ex, _, _ = montar(store, corretora)
    assert not ex.abrir(s, d, agora_ms=AGORA).ok
    assert corretora.enviadas == []


def test_confirmacao_do_assistido_nao_sobrevive_a_relogio_adiantado(store):
    """Relógio pulando para frente expira a confirmação, não a valida."""
    corretora = CorretoraCaotica()
    ex, _, _ = montar(store, corretora, fase=Fase.ASSISTIDO)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    ex.guarda.confirmar(oid, s, agora_ms=AGORA)
    aut = ex.guarda.autorizar(s, 250.0, client_oid=oid,
                              agora_ms=AGORA + 86_400_000)
    assert not aut.liberado
    assert "expirou" in aut.motivo


# =====================================================================
# 4. A parada de emergência sobrevive a tudo
# =====================================================================
def test_trava_sobrevive_a_reinicio_e_bloqueia_ordem(store, tmp_path):
    """Uma parada que se perde no restart é uma pausa, não uma parada."""
    corretora = CorretoraCaotica()
    ex1, _, trava1 = montar(store, corretora)
    trava1.ativar("parada durante incidente", agora_ms=AGORA)

    # Processo novo, mesmo arquivo de banco.
    caminho = store.caminho
    store.close()
    outro = Store(caminho)
    try:
        ex2, _, trava2 = montar(outro, corretora)
        assert trava2.ler().ativa
        res = ex2.abrir(sinal(), decisao(), agora_ms=AGORA)
        assert not res.ok
        assert "PARADA DE EMERGÊNCIA" in res.mensagem
        assert corretora.enviadas == []
    finally:
        outro.close()


def test_registro_de_trava_corrompido_conta_como_travado(store):
    """Na dúvida sobre se alguém pediu parada, não se opera."""
    corretora = CorretoraCaotica()
    ex, _, _ = montar(store, corretora)
    store.set_estado("trava_operacao", "{lixo binario \x00 aqui")
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not res.ok
    assert corretora.enviadas == []


def test_parada_trava_mesmo_com_fechamento_falhando(store):
    """Se fechar falhar, o sistema fica travado, não meio-aberto."""
    class EstadoFalso:
        def __init__(self, store, corretora):
            self.store = store
            self.trava = TravaOperacao(store)
            self.corretora = corretora

            class Motor:
                def desarmar_live(_):
                    raise RuntimeError("desarme falhou")

                def parar(_):
                    raise RuntimeError("parada falhou")

                def fechar_tudo(_, motivo):
                    raise ExchangeUnreachable("rede fora ao fechar")
            self.engine = Motor()

            class Exec:
                def posicoes(_):
                    return []
            self.executor = Exec()

    st = EstadoFalso(store, CorretoraCaotica())
    res = parada_emergencia(st, "incidente", agora_ms=AGORA)
    assert st.trava.ler().ativa, "trava não sobreviveu à falha da limpeza"
    assert not res["ok"]
    assert len(res["erros"]) == 3


# =====================================================================
# 5. Estado não conferido nunca é reportado como conferido
# =====================================================================
def test_reconciliacao_com_rede_fora_nao_diz_coerente(store):
    corretora = CorretoraCaotica()
    corretora.posicoes_falha = True
    rel = reconciliar([], corretora, agora_ms=AGORA)
    assert rel.veredicto == "NAO_CONFERIDO"
    assert not rel.consultou


def test_divergencia_durante_o_caos_trava(store):
    """A corretora tem posição que o sistema não conhece — depois da queda."""
    corretora = CorretoraCaotica()
    corretora.modo = "timeout"
    ex, _, trava = montar(store, corretora)
    ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    # O envio chegou: a corretora tem posição, o executor local não.
    assert corretora.posicoes()

    class ExecutorLocal:
        backend = corretora

        def posicoes(self):
            return []

    rec = Reconciliador(ExecutorLocal(), store, trava=trava)
    rel = rec.conferir(agora_ms=AGORA + 1000)
    assert rel.deve_pausar
    assert trava.ler().ativa
    assert any(d.tipo == "posicao_desconhecida" for d in rel.divergencias)


# =====================================================================
# 6. Banco sob estresse
# =====================================================================
def test_duas_threads_gravando_intencao_nao_duplicam(store):
    """Índice único no banco, não trava em memória.

    Se a exclusão dependesse de um lock do processo, duas threads — ou dois
    processos — passariam juntas.
    """
    resultados = []

    def tentar():
        nova, _ = store.registrar_intencao_envio(
            client_oid="iai-corrida", symbol="BTCUSDT", side="long",
            size=0.01, entry=64000.0, stop_loss=62000.0, criado_em=AGORA)
        resultados.append(nova)

    threads = [threading.Thread(target=tentar) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in resultados if r) == 1, (
        f"mais de uma thread achou que era a primeira: {resultados}")


def test_shadow_nao_duplica_sob_concorrencia(store):
    """Mesmo ciclo rodando duas vezes não pode inflar a amostra."""
    d = {"id": "s1", "decidido_em": AGORA, "symbol": "BTCUSDT",
         "side": "long", "entry": 64000.0, "stop_loss": 62000.0,
         "alvo": 66000.0, "size": 0.01}
    resultados = []

    def tentar():
        resultados.append(store.salvar_decisao_shadow(dict(d)))

    threads = [threading.Thread(target=tentar) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in resultados if r) == 1


def test_banco_somente_leitura_falha_alto_e_nao_em_silencio(store, tmp_path):
    """Disco cheio ou permissão errada tem de dar erro, não perda silenciosa.

    Uma gravação que falha sem barulho é a forma mais cara de perder
    estado: o sistema segue achando que registrou.
    """
    conn = store._conn
    conn.execute("PRAGMA query_only = ON")
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.registrar_intencao_envio(
                client_oid="iai-x", symbol="BTCUSDT", side="long", size=0.01,
                entry=64000.0, stop_loss=62000.0, criado_em=AGORA)
    finally:
        conn.execute("PRAGMA query_only = OFF")


# =====================================================================
# 7. Feed congelado
# =====================================================================
def test_feed_congelado_devolve_none_em_vez_de_preco_velho():
    """O perigo não é o feed cair; é ele ficar aberto e mudo."""
    from investai.exchanges.feed_estado import EstadoFeed
    f = EstadoFeed({"ticker": 5_000})
    f.conectou(AGORA)
    f.mensagem("ticker", {"preco": 64000}, AGORA)
    assert f.ultimo("ticker", AGORA + 1_000) is not None
    assert f.ultimo("ticker", AGORA + 3_600_000) is None
    assert f.canais_velhos(AGORA + 3_600_000) == ["ticker"]
    assert not f.confiavel(AGORA + 3_600_000)


def test_reconexao_em_cascata_acumula_lacunas():
    """Rede instável: cai e volta várias vezes. Nenhuma lacuna pode sumir."""
    from investai.exchanges.feed_estado import EstadoFeed
    f = EstadoFeed({"ticker": 60_000})
    t = AGORA
    for _ in range(5):
        f.conectou(t)
        f.desconectou(t + 1_000, "queda")
        t += 10_000
    assert f.reconexoes == 4
    assert len(f.lacunas) == 4
    assert not f.confiavel(t)


# =====================================================================
# 8. Invariante final
# =====================================================================
def test_nenhum_cenario_produz_ordem_dobrada(store):
    """Passa por todos os modos de falha com o mesmo sinal.

    É o resumo do arquivo: qualquer combinação, uma ordem só.
    """
    corretora = CorretoraCaotica()
    s = sinal()
    for modo in ("timeout", "ok", "truncada", "timeout", "ok"):
        corretora.modo = modo
        ex, controle, _ = montar(store, corretora)
        controle.reconciliar_pendentes(agora_ms=AGORA)
        ex.abrir(s, decisao(), agora_ms=AGORA)
    assert len(corretora.enviadas) == 1, (
        f"o mesmo sinal virou {len(corretora.enviadas)} ordens")
