"""Guarda de fase: a trava que impede ordem real sem estratégia validada.

Este arquivo existe por causa de um defeito concreto que estava no sistema:
`Executor.abrir` consultava apenas a gestão de risco. Com o modo real armado
e a chave conectada, a ordem ia para a corretora mesmo que a estratégia
tivesse sido reprovada no out-of-sample. Cada teste aqui é uma forma de esse
defeito voltar.
"""
import pytest

from investai.config import ExecutionConfig
from investai.models import Regime, Side, Signal, SignalGrade
from investai.risk.manager import DecisaoRisco
from investai.strategies import Fase, StrategyRegistry
from investai.trading import (
    TETO_NOTIONAL_FRAC_REAL_LIMITADO, Executor, GuardaError, GuardaFase,
    gerar_client_oid,
)

AGORA = 1_700_000_000_000


def sinal(entry=64000.0, stop=62720.0, side=Side.LONG, symbol="BTCUSDT"):
    risco = abs(entry - stop)
    alvos = ([entry + risco * r for r in (1.8, 3.0)] if side is Side.LONG
             else [entry - risco * r for r in (1.8, 3.0)])
    return Signal(symbol=symbol, timeframe="1H", side=side,
                  grade=SignalGrade.A, score=80.0, entry=entry, stop_loss=stop,
                  take_profits=alvos, risk_reward=1.8, atr=entry * 0.01,
                  regime=Regime.TENDENCIA_ALTA)


def decisao(size=0.0039):
    return DecisaoRisco(True, "ok", size=size, notional_usd=size * 64000,
                        risco_usd=5.0, alavancagem=1.0)


class BackendFalso:
    """Corretora de mentira que registra o que recebeu.

    O ponto dela é este: se `ordens` ficar vazio, nenhuma ordem saiu. É a
    única forma de provar que a trava funcionou, em vez de acreditar na
    mensagem de erro.
    """

    def __init__(self, saldo=1000.0):
        self._saldo = saldo
        self.ordens: list[dict] = []
        self.alavancagens: list[tuple] = []
        self.margin_modes: list[tuple] = []

    def saldo_usdt(self):
        return self._saldo

    def posicoes(self):
        return []

    def definir_alavancagem(self, symbol, leverage, hold_side=None):
        self.alavancagens.append((symbol, leverage, hold_side))
        return {}

    def definir_margin_mode(self, symbol, modo):
        self.margin_modes.append((symbol, modo))
        return {}

    def abrir_posicao(self, symbol, side, size, leverage, stop_loss,
                      take_profit, client_oid, margin_mode, preco_limite):
        self.ordens.append({"symbol": symbol, "side": side.value, "size": size,
                            "client_oid": client_oid})
        return {"orderId": f"ord-{len(self.ordens)}", "clientOid": client_oid}

    def fechar_posicao(self, symbol, side, size):
        return {}

    def ajustar_stop(self, symbol, side, novo_stop):
        return {}

    def contrato(self, symbol):
        return {"volume_place": 4, "price_place": 1, "min_trade_num": 0.0001,
                "min_trade_usdt": 5.0}


def registro_em(fase: Fase) -> tuple[StrategyRegistry, str]:
    """Registro com uma versão levada até `fase` pelo caminho normal."""
    reg = StrategyRegistry()
    v = reg.criar("teste", {"rsi": 14}, agora_ms=AGORA)
    while v.fase is not fase:
        v = reg.promover(v.chave, agora_ms=AGORA)
    return reg, v.chave


def executor_live(fase: Fase | None, *, saldo=1000.0,
                  capital=1000.0) -> tuple[Executor, BackendFalso, GuardaFase]:
    backend = BackendFalso(saldo)
    if fase is None:
        guarda = GuardaFase(StrategyRegistry(), capital_usd=capital)
    else:
        reg, chave = registro_em(fase)
        guarda = GuardaFase(reg, capital_usd=capital)
        guarda.vincular(chave)
    ex = Executor(ExecutionConfig(), backend=backend, modo="live",
                  guarda=guarda)
    return ex, backend, guarda


# ------------------------------------------------------- padrão é não operar
def test_executor_sem_guarda_explicita_nega_ordem_real():
    """Executor recém-construído não consegue mandar ordem real.

    É o `LIVE_TRADING = false` da especificação, imposto por construção em
    vez de por configuração: não existe estado inicial em que a ordem passa.
    """
    backend = BackendFalso()
    ex = Executor(ExecutionConfig(), backend=backend, modo="live")
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not res.ok
    assert backend.ordens == []


def test_guarda_sem_registro_nega():
    g = GuardaFase(None, capital_usd=1000.0)
    aut = g.autorizar(sinal(), 250.0, client_oid="x", agora_ms=AGORA)
    assert not aut.liberado
    assert "registro" in aut.motivo


def test_guarda_com_registro_mas_sem_vinculo_nega():
    """Existir estratégia validada no registro não basta: tem de estar no ar."""
    reg, chave = registro_em(Fase.REAL_LIMITADO)
    g = GuardaFase(reg, capital_usd=1000.0)
    aut = g.autorizar(sinal(), 250.0, client_oid="x", agora_ms=AGORA)
    assert not aut.liberado
    assert "vinculada" in aut.motivo


def test_vincular_chave_inexistente_falha():
    """Vincular chave inventada daria falsa impressão de evidência."""
    g = GuardaFase(StrategyRegistry())
    with pytest.raises(Exception):
        g.vincular("nao-existe@v1")


def test_vincular_sem_registro_falha():
    g = GuardaFase(None)
    with pytest.raises(GuardaError):
        g.vincular("qualquer@v1")


# ------------------------------------------------- fases que não operam real
@pytest.mark.parametrize("fase", [
    Fase.RASCUNHO, Fase.BACKTEST, Fase.OUT_OF_SAMPLE, Fase.PAPER_TRADING,
    Fase.SHADOW, Fase.DEMO,
])
def test_fase_anterior_a_assistido_nao_manda_ordem(fase):
    ex, backend, _ = executor_live(fase)
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not res.ok, f"fase {fase.value} não deveria enviar ordem real"
    assert backend.ordens == []
    assert res.autorizacao is not None
    assert res.autorizacao.fase == fase.value


def test_bloqueio_diz_quais_fases_faltam():
    """Mensagem tem de ser acionável: qual é o próximo passo, não só 'não'."""
    ex, _, _ = executor_live(Fase.OUT_OF_SAMPLE)
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    faltam = res.autorizacao.faltam_fases
    assert faltam == ["paper_trading", "shadow", "demo", "assistido",
                      "real_limitado"]
    assert "paper_trading" in res.mensagem


def test_guarda_nega_antes_de_tocar_na_conta():
    """Nem alavancagem nem margem podem ser escritas em ordem bloqueada.

    Definir alavancagem já é escrita na conta da corretora. Uma ordem que a
    fase não autoriza não deve deixar rastro nenhum lá.
    """
    ex, backend, _ = executor_live(Fase.SHADOW)
    ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert backend.ordens == []
    assert backend.alavancagens == []
    assert backend.margin_modes == []


def test_estrategia_reprovada_nao_opera():
    reg, chave = registro_em(Fase.REAL_LIMITADO)
    reg.reprovar(chave, "expectativa OOS com IC cruzando zero", agora_ms=AGORA)
    g = GuardaFase(reg, capital_usd=1000.0)
    g._chave = chave                   # simula reprovação depois do vínculo
    backend = BackendFalso()
    ex = Executor(ExecutionConfig(), backend=backend, modo="live", guarda=g)
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not res.ok
    assert "reprovada" in res.mensagem
    assert "IC cruzando zero" in res.mensagem
    assert backend.ordens == []


def test_estrategia_aposentada_nao_opera():
    reg, chave = registro_em(Fase.REAL_LIMITADO)
    g = GuardaFase(reg, capital_usd=1000.0)
    g.vincular(chave)
    reg.aposentar(chave, "substituída pela v2", agora_ms=AGORA)
    aut = g.autorizar(sinal(), 100.0, client_oid="x", agora_ms=AGORA)
    assert not aut.liberado
    assert "aposentada" in aut.motivo


def test_reprovacao_depois_do_armar_para_as_ordens():
    """A fase é conferida em cada ordem, não só ao armar.

    Cenário real: o motor foi armado com a estratégia em real_limitado e,
    duas horas depois, um gate a reprovou. As ordens têm de parar naquele
    instante, sem depender de alguém desarmar na mão.
    """
    ex, backend, g = executor_live(Fase.REAL_LIMITADO)
    assert ex.abrir(sinal(), decisao(), agora_ms=AGORA).ok
    assert len(backend.ordens) == 1

    g.registry.reprovar(g.chave_vinculada, "drawdown acima do limite",
                        agora_ms=AGORA)
    res = ex.abrir(sinal(entry=64100.0), decisao(), agora_ms=AGORA + 120_000)
    assert not res.ok
    assert len(backend.ordens) == 1, "ordem saiu depois da reprovação"


# ------------------------------------------------------------ modo assistido
def test_assistido_nao_manda_sozinho():
    ex, backend, _ = executor_live(Fase.ASSISTIDO)
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not res.ok
    assert res.autorizacao.precisa_confirmacao
    assert backend.ordens == []


def test_assistido_manda_com_confirmacao():
    ex, backend, g = executor_live(Fase.ASSISTIDO)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    g.confirmar(oid, s, agora_ms=AGORA)
    res = ex.abrir(s, decisao(), agora_ms=AGORA)
    assert res.ok, res.mensagem
    assert len(backend.ordens) == 1
    assert backend.ordens[0]["client_oid"] == oid


def test_confirmacao_nao_serve_para_uma_segunda_ordem():
    """Confirmar uma ordem não pode virar autorização permanente."""
    ex, backend, g = executor_live(Fase.ASSISTIDO)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    g.confirmar(oid, s, agora_ms=AGORA)
    assert ex.abrir(s, decisao(), agora_ms=AGORA).ok
    res2 = ex.abrir(s, decisao(), agora_ms=AGORA)
    assert not res2.ok
    assert "já foi usada" in res2.mensagem
    assert len(backend.ordens) == 1


def test_confirmacao_nao_vale_para_outro_sinal():
    """Confirmação dada para BTC não pode liberar ETH.

    O clientOid já embute o par, mas o teste trava o comportamento contra
    uma mudança futura no gerador de id.
    """
    ex, backend, g = executor_live(Fase.ASSISTIDO)
    s_btc = sinal()
    oid = gerar_client_oid(s_btc.symbol, s_btc.side, s_btc.entry, AGORA)
    g.confirmar(oid, s_btc, agora_ms=AGORA)
    s_eth = sinal(symbol="ETHUSDT")
    res = ex.abrir(s_eth, decisao(), agora_ms=AGORA)
    assert not res.ok
    assert backend.ordens == []


def test_confirmacao_expira():
    """Preço velho não é o preço que o humano viu."""
    ex, backend, g = executor_live(Fase.ASSISTIDO)
    g.validade_confirmacao_s = 60.0
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    g.confirmar(oid, s, agora_ms=AGORA)
    # Mesmo clientOid exige mesma janela de 60s; o vencimento é medido
    # contra o instante da confirmação, então avançamos o relógio do
    # julgamento sem mudar o instante da ordem.
    aut = g.autorizar(s, 250.0, client_oid=oid, agora_ms=AGORA + 61_000)
    assert not aut.liberado
    assert "expirou" in aut.motivo
    assert backend.ordens == []


def test_trocar_versao_invalida_confirmacoes():
    reg, chave = registro_em(Fase.ASSISTIDO)
    outra = reg.criar("teste", {"rsi": 21}, agora_ms=AGORA)
    g = GuardaFase(reg, capital_usd=1000.0)
    g.vincular(chave)
    s = sinal()
    oid = gerar_client_oid(s.symbol, s.side, s.entry, AGORA)
    g.confirmar(oid, s, agora_ms=AGORA)
    g.vincular(outra.chave)
    assert g.confirmacoes_pendentes(agora_ms=AGORA) == []


def test_assistido_sem_client_oid_pede_confirmacao():
    reg, chave = registro_em(Fase.ASSISTIDO)
    g = GuardaFase(reg, capital_usd=1000.0)
    g.vincular(chave)
    aut = g.autorizar(sinal(), 100.0, client_oid="", agora_ms=AGORA)
    assert not aut.liberado
    assert aut.precisa_confirmacao


# ---------------------------------------------------- teto do real_limitado
def test_real_limitado_manda_ordem():
    ex, backend, _ = executor_live(Fase.REAL_LIMITADO)
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert res.ok, res.mensagem
    assert len(backend.ordens) == 1


def test_teto_de_notional_barra_ordem_grande():
    """Teto da fase é independente do risco: duas travas em série.

    Um erro de configuração no dimensionamento não deve conseguir mandar a
    conta inteira para uma ordem só.
    """
    ex, backend, _ = executor_live(Fase.REAL_LIMITADO, capital=1000.0)
    grande = DecisaoRisco(True, "ok", size=1.0, notional_usd=64000.0,
                          risco_usd=5.0, alavancagem=1.0)
    res = ex.abrir(sinal(), grande, agora_ms=AGORA)
    assert not res.ok
    assert "teto" in res.mensagem
    assert backend.ordens == []


def test_teto_usa_notional_arredondado_ao_contrato():
    """O julgamento é feito sobre o tamanho que vai de fato para a corretora.

    Caso escolhido para separar os dois comportamentos: 0,00399 BTC a 64000
    são US$ 255,36 — acima do teto de US$ 250. O passo do contrato (4 casas)
    corta para 0,0039, US$ 249,60, abaixo do teto. Se a guarda julgasse o
    tamanho pedido em vez do arredondado, esta ordem seria barrada sem
    motivo; se julgasse o arredondado para cima, passaria movimentando mais
    do que aprovou.
    """
    ex, backend, _ = executor_live(Fase.REAL_LIMITADO, capital=1000.0)
    teto = 1000.0 * TETO_NOTIONAL_FRAC_REAL_LIMITADO
    res = ex.abrir(sinal(), decisao(size=0.00399), agora_ms=AGORA)
    assert res.ok, res.mensagem
    assert res.autorizacao.teto_notional_usd == pytest.approx(teto)
    assert backend.ordens[0]["size"] == pytest.approx(0.0039)


def test_capital_nao_sincronizado_nega():
    """Sem saber o capital, não há teto — e sem teto, não há envio."""
    ex, backend, _ = executor_live(Fase.REAL_LIMITADO, capital=0.0)
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert not res.ok
    assert "capital" in res.mensagem
    assert backend.ordens == []


def test_sincronizar_capital_move_o_teto():
    _, _, g = executor_live(Fase.REAL_LIMITADO, capital=1000.0)
    g.sincronizar_capital(4000.0)
    aut = g.autorizar(sinal(), 900.0, client_oid="x", agora_ms=AGORA)
    assert aut.liberado
    assert aut.teto_notional_usd == pytest.approx(1000.0)


# --------------------------------------------------- paper não é afetado
def test_modo_papel_nao_consulta_a_guarda():
    """A trava é para dinheiro real. Simulação continua livre para medir.

    Se a guarda barrasse o paper trading, a estratégia nunca acumularia a
    evidência necessária para sair da fase — o sistema travaria a si mesmo.
    """
    ex = Executor(ExecutionConfig(), modo="paper")
    res = ex.abrir(sinal(), decisao(), agora_ms=AGORA)
    assert res.ok
    assert res.autorizacao is None


# -------------------------------------------------------------- inspeção
def test_estado_da_guarda_expõe_o_que_falta():
    _, _, g = executor_live(Fase.SHADOW, capital=1000.0)
    est = g.estado()
    assert est["versao"]["fase"] == "shadow"
    assert est["versao"]["faltam_fases"] == ["demo", "assistido",
                                             "real_limitado"]
    assert est["teto_notional_usd"] == pytest.approx(250.0)
    assert est["fases_que_permitem_real"] == ["assistido", "real_limitado"]


def test_risco_reprovado_continua_barrando_antes_da_fase():
    """As duas travas são independentes; a do risco vem primeiro."""
    ex, backend, _ = executor_live(Fase.REAL_LIMITADO)
    negado = DecisaoRisco(False, "drawdown diário no limite")
    res = ex.abrir(sinal(), negado, agora_ms=AGORA)
    assert not res.ok
    assert "risco não aprovou" in res.mensagem
    assert backend.ordens == []


# =====================================================================
# Motor: armar o modo real e o fluxo de confirmação do modo assistido
# =====================================================================
def motor_com(fase, settings, hub, store, *, saldo=1000.0):
    from investai.analysis.screener import Screener
    from investai.risk import RiskManager
    from investai.trading import TradingEngine
    backend = BackendFalso(saldo)
    if fase is None:
        guarda = GuardaFase(StrategyRegistry(),
                            capital_usd=settings.exec.capital_inicial_usd)
    else:
        reg, chave = registro_em(fase)
        guarda = GuardaFase(reg, capital_usd=settings.exec.capital_inicial_usd)
        guarda.vincular(chave)
    ex = Executor(settings.exec, backend=backend, modo="paper", guarda=guarda)
    motor = TradingEngine(settings, Screener(hub, settings), ex,
                          RiskManager(settings.risk,
                                      settings.exec.capital_inicial_usd),
                          store)
    return motor, backend, guarda


def test_armar_sem_estrategia_vinculada_e_recusado(settings, hub, store):
    """Armar o modo real sem estratégia validada não faz sentido.

    Se deixássemos armar, o motor ficaria ligado e todo sinal seria barrado
    pela guarda depois — dando ao operador a impressão de que está operando
    quando não está.
    """
    motor, backend, _ = motor_com(None, settings, hub, store)
    ok, msg = motor.armar_live("OPERAR COM DINHEIRO REAL")
    assert not ok
    assert "vinculada" in msg
    assert motor.executor.modo == "paper"


@pytest.mark.parametrize("fase", [Fase.BACKTEST, Fase.OUT_OF_SAMPLE,
                                  Fase.PAPER_TRADING, Fase.SHADOW,
                                  Fase.DEMO])
def test_armar_com_fase_insuficiente_e_recusado(fase, settings, hub, store):
    motor, _, _ = motor_com(fase, settings, hub, store)
    ok, msg = motor.armar_live("OPERAR COM DINHEIRO REAL")
    assert not ok
    assert fase.value in msg
    assert not motor.estado.armado_live


def test_armar_confere_fase_antes_de_ler_a_conta(settings, hub, store):
    """Estratégia sem fase não justifica nem uma chamada à corretora."""
    class BackendQueConta(BackendFalso):
        def __init__(self):
            super().__init__()
            self.leituras = 0

        def saldo_usdt(self):
            self.leituras += 1
            return 1000.0

    from investai.analysis.screener import Screener
    from investai.risk import RiskManager
    from investai.trading import TradingEngine
    backend = BackendQueConta()
    reg, chave = registro_em(Fase.SHADOW)
    guarda = GuardaFase(reg, capital_usd=1000.0)
    guarda.vincular(chave)
    ex = Executor(settings.exec, backend=backend, modo="paper", guarda=guarda)
    motor = TradingEngine(settings, Screener(hub, settings), ex,
                          RiskManager(settings.risk, 1000.0), store)
    ok, _ = motor.armar_live("OPERAR COM DINHEIRO REAL")
    assert not ok
    assert backend.leituras == 0


def test_armar_com_real_limitado_funciona(settings, hub, store):
    motor, _, _ = motor_com(Fase.REAL_LIMITADO, settings, hub, store)
    ok, msg = motor.armar_live("OPERAR COM DINHEIRO REAL")
    assert ok, msg
    assert motor.estado.armado_live
    assert "real_limitado" in msg
    # O teto da fase precisa acompanhar o saldo lido da conta, não o capital
    # de configuração.
    assert motor.guarda.capital_usd == pytest.approx(1000.0)


def test_armar_com_assistido_funciona(settings, hub, store):
    motor, _, _ = motor_com(Fase.ASSISTIDO, settings, hub, store)
    ok, msg = motor.armar_live("OPERAR COM DINHEIRO REAL")
    assert ok, msg


def test_proposta_assistida_fica_pendente(settings, hub, store):
    motor, backend, _ = motor_com(Fase.ASSISTIDO, settings, hub, store)
    assert motor.armar_live("OPERAR COM DINHEIRO REAL")[0]
    saida = motor._tentar_entrada(sinal(), AGORA)
    assert saida["ok"] is False
    assert saida["precisa_confirmacao"]
    pendentes = motor.propostas_pendentes()
    assert len(pendentes) == 1
    assert pendentes[0]["symbol"] == "BTCUSDT"
    assert pendentes[0]["client_oid"] == saida["client_oid"]
    assert backend.ordens == []


def test_confirmar_proposta_envia_a_ordem(settings, hub, store):
    motor, backend, _ = motor_com(Fase.ASSISTIDO, settings, hub, store)
    motor.armar_live("OPERAR COM DINHEIRO REAL")
    saida = motor._tentar_entrada(sinal(), AGORA)
    res = motor.confirmar_proposta(saida["client_oid"])
    assert res["ok"], res["motivo"]
    assert len(backend.ordens) == 1
    assert motor.propostas_pendentes() == []


def test_confirmar_reenvia_com_o_mesmo_client_oid(settings, hub, store):
    """O reenvio tem de carregar o id original.

    Se a confirmação gerasse um id novo, uma ordem que já tivesse chegado à
    corretora antes da resposta se perder viraria posição dobrada.
    """
    motor, backend, _ = motor_com(Fase.ASSISTIDO, settings, hub, store)
    motor.armar_live("OPERAR COM DINHEIRO REAL")
    saida = motor._tentar_entrada(sinal(), AGORA)
    motor.confirmar_proposta(saida["client_oid"])
    assert backend.ordens[0]["client_oid"] == saida["client_oid"]


def test_confirmar_id_desconhecido_nao_envia(settings, hub, store):
    motor, backend, _ = motor_com(Fase.ASSISTIDO, settings, hub, store)
    motor.armar_live("OPERAR COM DINHEIRO REAL")
    res = motor.confirmar_proposta("iai-inventado")
    assert not res["ok"]
    assert backend.ordens == []


def test_recusar_proposta_descarta(settings, hub, store):
    motor, backend, _ = motor_com(Fase.ASSISTIDO, settings, hub, store)
    motor.armar_live("OPERAR COM DINHEIRO REAL")
    saida = motor._tentar_entrada(sinal(), AGORA)
    assert motor.recusar_proposta(saida["client_oid"])["ok"]
    assert motor.propostas_pendentes() == []
    assert motor.confirmar_proposta(saida["client_oid"])["ok"] is False
    assert backend.ordens == []


def test_desarmar_apaga_propostas(settings, hub, store):
    """Confirmação pendente não pode sobreviver a um desarme."""
    motor, backend, _ = motor_com(Fase.ASSISTIDO, settings, hub, store)
    motor.armar_live("OPERAR COM DINHEIRO REAL")
    saida = motor._tentar_entrada(sinal(), AGORA)
    motor.desarmar_live()
    assert motor.propostas_pendentes() == []
    assert not motor.confirmar_proposta(saida["client_oid"])["ok"]
    assert backend.ordens == []


def test_confirmar_em_modo_papel_nao_envia(settings, hub, store):
    motor, backend, guarda = motor_com(Fase.ASSISTIDO, settings, hub, store)
    motor.armar_live("OPERAR COM DINHEIRO REAL")
    saida = motor._tentar_entrada(sinal(), AGORA)
    motor.executor.modo = "paper"       # desarme parcial, feito à mão
    res = motor.confirmar_proposta(saida["client_oid"])
    assert not res["ok"]
    assert backend.ordens == []


def test_bloqueio_por_fase_e_contado_separado_do_risco(settings, hub, store):
    """Causas diferentes, contadores diferentes: a correção é outra."""
    motor, _, _ = motor_com(Fase.SHADOW, settings, hub, store)
    motor.executor.modo = "live"
    motor.estado.armado_live = True
    motor._tentar_entrada(sinal(), AGORA)
    assert motor.estado.bloqueadas_por_fase == 1
    assert motor.estado.ordens_enviadas == 0


def test_status_do_motor_mostra_a_guarda(settings, hub, store):
    motor, _, _ = motor_com(Fase.SHADOW, settings, hub, store)
    s = motor.status()
    assert s["guarda"]["versao"]["fase"] == "shadow"
    assert s["propostas_pendentes"] == []
