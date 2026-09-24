"""Reconciliação entre o estado local e o da corretora.

A divergência em si é banal e frequente: execução parcial, stop disparado
com o processo caído, ordem aberta pelo aplicativo da corretora. O que não
pode acontecer é o sistema **operar em cima do estado errado** — dimensionar
contra um capital que não existe, ou achar que há proteção onde não há.

A regra sob teste é sempre a mesma: divergência PAUSA, não corrige.
"""
import pytest

from investai.models import Position, Side
from investai.ops.comandos import TravaOperacao
from investai.ops.reconciliacao import (
    TOL_CAPITAL, TOL_PRECO, TOL_TAMANHO, Reconciliador, comparar_posicoes,
    reconciliar,
)

AGORA = 1_700_000_000_000


def pos(symbol="BTCUSDT", side=Side.LONG, size=0.01, entry=64000.0,
        stop=62000.0, modo="live"):
    return Position(symbol=symbol, side=side, size=size, entry=entry,
                    stop_loss=stop, take_profits=[entry * 1.04],
                    opened_at=AGORA, notional_usd=size * entry, modo=modo)


class Corretora:
    def __init__(self, posicoes=None, saldo=1000.0):
        self._pos = list(posicoes or [])
        self._saldo = saldo
        self.falha_posicoes = False
        self.falha_saldo = False

    def posicoes(self):
        if self.falha_posicoes:
            raise RuntimeError("rede fora")
        return list(self._pos)

    def saldo_usdt(self):
        if self.falha_saldo:
            raise RuntimeError("saldo indisponível")
        return self._saldo


# =====================================================================
# Comparação de posições
# =====================================================================
def test_estados_iguais_nao_divergem():
    assert comparar_posicoes([pos()], [pos()]) == []


def test_ambos_vazios_nao_divergem():
    assert comparar_posicoes([], []) == []


def test_posicao_na_corretora_que_o_sistema_nao_conhece_e_critica():
    """É exposição real sem gestão de stop nem de alvo."""
    divs = comparar_posicoes([], [pos()])
    assert len(divs) == 1
    assert divs[0].tipo == "posicao_desconhecida"
    assert divs[0].gravidade == "critica"
    assert divs[0].pausa
    assert "sem gestão" in divs[0].detalhe


def test_posicao_local_que_a_corretora_nao_tem_e_alta():
    """O stop provavelmente disparou enquanto o sistema não olhava."""
    divs = comparar_posicoes([pos()], [])
    assert divs[0].tipo == "posicao_ausente"
    assert divs[0].pausa
    assert "stop" in divs[0].detalhe


def test_lado_oposto_e_critico():
    """Qualquer gestão daqui iria na direção errada."""
    divs = comparar_posicoes([pos(side=Side.LONG)], [pos(side=Side.SHORT)])
    assert divs[0].tipo == "lado_diferente"
    assert divs[0].gravidade == "critica"


def test_tamanho_diferente_e_divergencia():
    divs = comparar_posicoes([pos(size=0.01)], [pos(size=0.005)])
    assert divs[0].tipo == "tamanho_diferente"
    assert divs[0].pausa
    assert "execução parcial" in divs[0].detalhe


def test_diferenca_de_arredondamento_no_tamanho_nao_alarma():
    """Comparar float por igualdade geraria alarme a cada ciclo.

    E um alarme que toca sempre treina o operador a ignorá-lo, que é pior
    do que não ter alarme.
    """
    dentro = 0.01 * (1 + TOL_TAMANHO * 0.5)
    assert comparar_posicoes([pos(size=0.01)], [pos(size=dentro)]) == []


def test_stop_ausente_na_corretora_e_critico():
    """Perda ilimitada até alguém agir. Não é arredondamento."""
    divs = comparar_posicoes([pos(stop=62000.0)], [pos(stop=0.0)])
    assert divs[0].tipo == "stop_ausente"
    assert divs[0].gravidade == "critica"
    assert "ilimitada" in divs[0].detalhe


def test_stop_diferente_e_divergencia():
    divs = comparar_posicoes([pos(stop=62000.0)], [pos(stop=60000.0)])
    assert divs[0].tipo == "stop_diferente"
    assert "o risco calculado aqui não é o risco real" in divs[0].detalhe


def test_stop_dentro_da_tolerancia_de_tick_nao_alarma():
    dentro = 62000.0 * (1 + TOL_PRECO * 0.5)
    assert comparar_posicoes([pos(stop=62000.0)], [pos(stop=dentro)]) == []


def test_varias_divergencias_no_mesmo_par():
    divs = comparar_posicoes([pos(size=0.01, stop=62000.0)],
                             [pos(size=0.02, stop=58000.0)])
    tipos = {d.tipo for d in divs}
    assert tipos == {"tamanho_diferente", "stop_diferente"}


def test_pares_diferentes_sao_comparados_separadamente():
    divs = comparar_posicoes(
        [pos("BTCUSDT"), pos("ETHUSDT", entry=3000.0, stop=2900.0)],
        [pos("BTCUSDT")])
    assert len(divs) == 1
    assert divs[0].symbol == "ETHUSDT"


def test_comparacao_ignora_caixa_do_par():
    divs = comparar_posicoes([pos("btcusdt")], [pos("BTCUSDT")])
    assert divs == []


# =====================================================================
# Reconciliação completa
# =====================================================================
def test_estados_coerentes():
    rel = reconciliar([pos()], Corretora([pos()]), agora_ms=AGORA)
    assert rel.veredicto == "COERENTE"
    assert not rel.deve_pausar
    assert rel.consultou


def test_falha_de_consulta_nao_e_coerente():
    """Tratar rede fora como confirmação é o mesmo erro da idempotência.

    "Não sei" é diferente de "está tudo bem", e confundir os dois libera
    operação em cima de estado não verificado.
    """
    c = Corretora([pos()])
    c.falha_posicoes = True
    rel = reconciliar([pos()], c, agora_ms=AGORA)
    assert rel.veredicto == "NAO_CONFERIDO"
    assert not rel.consultou
    assert "rede fora" in rel.erro


def test_sem_conexao_nao_e_coerente():
    rel = reconciliar([pos()], None, agora_ms=AGORA)
    assert rel.veredicto == "NAO_CONFERIDO"
    assert "nenhuma conexão" in rel.erro


def test_capital_divergente_e_apontado():
    rel = reconciliar([], Corretora([], saldo=500.0), capital_local=1000.0,
                      agora_ms=AGORA)
    assert rel.deve_pausar
    d = [x for x in rel.divergencias if x.tipo == "capital_diferente"][0]
    assert "dimensionamento da próxima posição" in d.detalhe


def test_capital_dentro_da_tolerancia_passa():
    saldo = 1000.0 * (1 + TOL_CAPITAL * 0.5)
    rel = reconciliar([], Corretora([], saldo=saldo), capital_local=1000.0,
                      agora_ms=AGORA)
    assert rel.veredicto == "COERENTE"


def test_saldo_ilegivel_vira_divergencia_media():
    """Não conseguir ler o saldo é aviso, não parada.

    A posição foi conferida; só o capital não. Parar por isso seria
    desproporcional, e parar demais é o caminho para desligar o alarme.
    """
    c = Corretora([])
    c.falha_saldo = True
    rel = reconciliar([], c, capital_local=1000.0, agora_ms=AGORA)
    assert rel.veredicto == "DIVERGENCIA_MENOR"
    assert not rel.deve_pausar


def test_conta_posicoes_dos_dois_lados():
    rel = reconciliar([pos("BTCUSDT")],
                      Corretora([pos("BTCUSDT"), pos("ETHUSDT")]),
                      agora_ms=AGORA)
    assert rel.posicoes_locais == 1
    assert rel.posicoes_remotas == 2
    assert rel.conferidas == ["BTCUSDT", "ETHUSDT"]


def test_relatorio_diz_que_nao_corrige_sozinho():
    d = reconciliar([], Corretora([]), agora_ms=AGORA).to_dict()
    assert "não é corrigida" in d["observacao"]
    assert "dobrar exposição" in d["observacao"]


def test_texto_marca_o_que_pausa():
    rel = reconciliar([], Corretora([pos()]), agora_ms=AGORA)
    texto = rel.texto()
    assert "PAUSA" in texto
    assert "DIVERGENTE" in texto


# =====================================================================
# Reconciliador: a pausa acontece de verdade
# =====================================================================
class ExecutorFalso:
    def __init__(self, posicoes, backend):
        self._pos = list(posicoes)
        self.backend = backend

    def posicoes(self):
        return list(self._pos)


class RiscoFalso:
    class Estado:
        capital_atual = 1000.0
    estado = Estado()


def test_divergencia_trava_a_operacao(store):
    """A parada é a mesma dos comandos operacionais, de propósito.

    Um segundo mecanismo de pausa significaria dois lugares para lembrar de
    destravar — e alguém esqueceria um deles.
    """
    trava = TravaOperacao(store)
    ex = ExecutorFalso([], Corretora([pos()]))
    rec = Reconciliador(ex, store, trava=trava)
    rel = rec.conferir(agora_ms=AGORA)
    assert rel.deve_pausar
    assert trava.ler().ativa
    assert "divergência com a corretora" in trava.ler().motivo


def test_estado_coerente_nao_trava(store):
    trava = TravaOperacao(store)
    ex = ExecutorFalso([pos()], Corretora([pos()]))
    Reconciliador(ex, store, trava=trava).conferir(agora_ms=AGORA)
    assert not trava.ler().ativa


def test_pode_conferir_sem_travar(store):
    """Útil para inspeção manual sem efeito colateral."""
    trava = TravaOperacao(store)
    ex = ExecutorFalso([], Corretora([pos()]))
    rel = Reconciliador(ex, store, trava=trava).conferir(pausar=False,
                                                         agora_ms=AGORA)
    assert rel.deve_pausar
    assert not trava.ler().ativa


def test_divergencia_menor_nao_trava(store):
    trava = TravaOperacao(store)
    c = Corretora([])
    c.falha_saldo = True
    ex = ExecutorFalso([], c)
    rec = Reconciliador(ex, store, trava=trava, risk=RiscoFalso())
    rel = rec.conferir(agora_ms=AGORA)
    assert rel.veredicto == "DIVERGENCIA_MENOR"
    assert not trava.ler().ativa


def test_divergencia_e_registrada_no_journal(store):
    ex = ExecutorFalso([], Corretora([pos()]))
    Reconciliador(ex, store, trava=TravaOperacao(store)).conferir(
        agora_ms=AGORA)
    eventos = store.eventos(limite=20)
    assert any("reconciliação" in e["mensagem"] for e in eventos)
    assert any("TRAVADA" in e["mensagem"] for e in eventos)


def test_reconciliador_usa_o_capital_do_risco(store):
    ex = ExecutorFalso([], Corretora([], saldo=200.0))
    rec = Reconciliador(ex, store, trava=TravaOperacao(store),
                        risk=RiscoFalso())
    rel = rec.conferir(agora_ms=AGORA)
    assert any(d.tipo == "capital_diferente" for d in rel.divergencias)


def test_estado_do_reconciliador(store):
    ex = ExecutorFalso([pos()], Corretora([pos()]))
    rec = Reconciliador(ex, store)
    assert rec.estado()["ultimo"] is None
    rec.conferir(agora_ms=AGORA)
    assert rec.estado()["ultimo"]["veredicto"] == "COERENTE"
    assert rec.estado()["tolerancias"]["tamanho"] == TOL_TAMANHO


def test_falha_de_consulta_nao_trava_mas_e_registrada(store):
    """Não conseguir conferir não é divergência; é não ter conferido.

    Travar aqui pararia o sistema a cada oscilação de rede. O que não pode
    é o relatório dizer "coerente".
    """
    trava = TravaOperacao(store)
    c = Corretora([])
    c.falha_posicoes = True
    rec = Reconciliador(ExecutorFalso([], c), store, trava=trava)
    rel = rec.conferir(agora_ms=AGORA)
    assert rel.veredicto == "NAO_CONFERIDO"
    assert not trava.ler().ativa
    assert any("NAO_CONFERIDO" in e["mensagem"] or "reconciliação" in
               e["mensagem"] for e in store.eventos(limite=10))
