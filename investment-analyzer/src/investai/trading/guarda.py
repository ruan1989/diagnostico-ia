"""Guarda de fase: a última trava antes de uma ordem real.

Por que este módulo existe
--------------------------
O pipeline de promoção mede a estratégia e decide em que fase ela está. O
executor envia ordens. Até aqui, essas duas coisas não conversavam: com o
modo real armado e a chave conectada, a ordem ia para a corretora sem que
ninguém perguntasse se a estratégia havia passado no out-of-sample.

Era o único caminho pelo qual dinheiro real saía sem evidência. Este módulo
fecha esse caminho.

Como ele decide
---------------
A guarda é **fail-closed**: a resposta padrão é NÃO. Nenhuma ordem real sai
sem que exista uma versão de estratégia explicitamente vinculada ao motor e
em fase que autorize aquele tipo de envio.

    fase < ASSISTIDO   → bloqueia, dizendo qual fase falta
    ASSISTIDO          → não envia sozinho; exige confirmação humana por ordem
    REAL_LIMITADO      → envia, respeitando o teto de capital da fase
    REPROVADA/APOSENTADA → bloqueia

A confirmação do modo ASSISTIDO é por ordem e por `clientOid`: confirmar uma
ordem não confirma a próxima, e um token confirmado não pode ser reaproveitado
para um sinal diferente. Sem isso, "assistido" viraria "real" com um passo
extra na primeira vez.

O teto da fase REAL_LIMITADO
----------------------------
`REAL_LIMITADO` não é "liberado". A guarda impõe um teto de notional por
ordem, derivado do capital, independente do que a gestão de risco aprovou.
São duas travas em série de propósito: um erro de configuração no risco não
deve conseguir mandar a conta inteira para uma ordem só.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models import Signal
from ..strategies.registry import ORDEM, Fase, StrategyRegistry

log = logging.getLogger("investai.guarda")

# Fases a partir das quais existe qualquer contato com dinheiro real.
FASES_REAIS: tuple[Fase, ...] = (Fase.ASSISTIDO, Fase.REAL_LIMITADO)

# A fase DEMO envia ordens de verdade — no ambiente de teste da corretora.
# Ela NÃO entra em `FASES_REAIS`: autorizar a mesma ordem contra a conta real
# seria exatamente o acidente que o ambiente de demo existe para evitar.
FASES_DEMO: tuple[Fase, ...] = (Fase.DEMO,)

# Fração máxima do capital que uma única ordem pode movimentar em notional
# quando a estratégia está em REAL_LIMITADO.
TETO_NOTIONAL_FRAC_REAL_LIMITADO = 0.25


class GuardaError(RuntimeError):
    pass


@dataclass(slots=True)
class Autorizacao:
    """Veredicto da guarda sobre uma ordem real específica."""

    liberado: bool
    motivo: str
    fase: str = ""
    chave_estrategia: str = ""
    precisa_confirmacao: bool = False
    teto_notional_usd: float | None = None
    faltam_fases: list[str] = field(default_factory=list)
    # ID da ordem julgada. Vai no veredicto para que quem recebe um
    # "precisa de confirmação" saiba exatamente QUAL ordem confirmar, sem
    # ter de recalcular o id e correr o risco de confirmar outra.
    client_oid: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "liberado": self.liberado, "motivo": self.motivo,
            "fase": self.fase, "chave_estrategia": self.chave_estrategia,
            "precisa_confirmacao": self.precisa_confirmacao,
            "client_oid": self.client_oid,
            "teto_notional_usd": (round(self.teto_notional_usd, 2)
                                  if self.teto_notional_usd is not None else None),
            "faltam_fases": self.faltam_fases,
        }


def _fases_faltando(fase: Fase) -> list[str]:
    """Fases que ainda separam `fase` de REAL_LIMITADO."""
    if fase not in ORDEM:
        return []
    i = ORDEM.index(fase)
    return [f.value for f in ORDEM[i + 1:]]


@dataclass(slots=True)
class _Confirmacao:
    client_oid: str
    symbol: str
    side: str
    entry: float
    concedida_em_ms: int
    usada: bool = False


class GuardaFase:
    """Consulta a fase da estratégia antes de liberar uma ordem real.

    `vincular` declara qual versão do registro está produzindo os sinais que
    o executor recebe. Enquanto nada estiver vinculado, toda ordem real é
    negada — o que é o comportamento correto em um sistema que nasce com
    `LIVE_TRADING` desligado.
    """

    def __init__(self, registry: StrategyRegistry | None = None, *,
                 capital_usd: float = 0.0,
                 validade_confirmacao_s: float = 300.0,
                 trava: Callable[[], tuple[bool, str]] | None = None,
                 em_demo: Callable[[], bool] | None = None):
        self.registry = registry
        self.capital_usd = max(0.0, capital_usd)
        self.validade_confirmacao_s = validade_confirmacao_s
        # Consulta à parada de emergência. Recebida como função, e não como
        # objeto, para que este módulo não dependa da camada de operação —
        # a trava precisa poder ser lida de onde ela estiver guardada.
        self._trava = trava
        # Diz se a conexão ativa aponta para o ambiente de demo. A guarda
        # precisa disso porque a fase DEMO autoriza envio APENAS para lá: a
        # mesma ordem contra a conta real é o acidente que o ambiente de
        # teste existe para evitar.
        self._em_demo = em_demo
        self._chave: str | None = None
        self._confirmacoes: dict[str, _Confirmacao] = {}

    # --------------------------------------------------------------- vínculo
    @property
    def chave_vinculada(self) -> str | None:
        return self._chave

    def vincular(self, chave: str) -> None:
        """Declara a versão de estratégia que está no ar.

        Falha se a versão não existir no registro: vincular uma chave
        inventada daria a impressão de que existe evidência por trás dela.
        """
        if self.registry is None:
            raise GuardaError("não há registro de estratégias para vincular")
        self.registry.obter(chave)          # levanta se não existir
        self._chave = chave
        self._confirmacoes.clear()          # troca de versão invalida confirmações

    def desvincular(self) -> None:
        self._chave = None
        self._confirmacoes.clear()

    def sincronizar_capital(self, capital_usd: float) -> None:
        self.capital_usd = max(0.0, capital_usd)

    # ---------------------------------------------------------- confirmação
    def confirmar(self, client_oid: str, sinal: Signal, *,
                  agora_ms: int | None = None) -> None:
        """Registra a confirmação humana de UMA ordem do modo assistido."""
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        self._confirmacoes[client_oid] = _Confirmacao(
            client_oid=client_oid, symbol=sinal.symbol, side=sinal.side.value,
            entry=float(sinal.entry), concedida_em_ms=agora)

    def confirmacoes_pendentes(self, *, agora_ms: int | None = None) -> list[dict]:
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        return [
            {"client_oid": c.client_oid, "symbol": c.symbol, "side": c.side,
             "entry": c.entry, "concedida_em_ms": c.concedida_em_ms,
             "expira_em_ms": c.concedida_em_ms
             + int(self.validade_confirmacao_s * 1000)}
            for c in self._confirmacoes.values()
            if not c.usada
            and agora - c.concedida_em_ms <= self.validade_confirmacao_s * 1000
        ]

    def _consumir(self, client_oid: str, sinal: Signal,
                  agora_ms: int) -> tuple[bool, str]:
        c = self._confirmacoes.get(client_oid)
        if c is None:
            return False, ("fase assistido: esta ordem não foi confirmada por "
                           "um humano")
        if c.usada:
            return False, ("fase assistido: a confirmação desta ordem já foi "
                           "usada e não vale para um novo envio")
        if agora_ms - c.concedida_em_ms > self.validade_confirmacao_s * 1000:
            return False, (f"fase assistido: a confirmação expirou "
                           f"({self.validade_confirmacao_s:.0f}s); o preço já "
                           f"não é o que o humano viu")
        # O clientOid já embute símbolo, lado, preço e janela de tempo, mas
        # conferir os campos explicitamente evita que uma mudança futura no
        # gerador transforme um bug em ordem confirmada por engano.
        if (c.symbol != sinal.symbol or c.side != sinal.side.value
                or abs(c.entry - float(sinal.entry)) > 1e-9):
            return False, ("fase assistido: a confirmação foi dada para outra "
                           "ordem")
        c.usada = True
        return True, "confirmação humana válida"

    # ------------------------------------------------------------ veredicto
    def autorizar(self, sinal: Signal, notional_usd: float, *,
                  client_oid: str = "",
                  agora_ms: int | None = None) -> Autorizacao:
        """Decide se esta ordem real pode sair. Padrão: não."""
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)

        # A parada de emergência vem antes de tudo. Ela existe justamente
        # para os momentos em que o resto do sistema está errado, então não
        # pode depender de nenhuma outra checagem ter dado certo.
        if self._trava is not None:
            try:
                travada, motivo = self._trava()
            except Exception as exc:                    # noqa: BLE001
                # Não conseguir ler a trava é motivo para não operar. Na
                # dúvida sobre se alguém pediu parada, não se envia ordem.
                return Autorizacao(
                    False, f"não foi possível ler a trava de operação ({exc}); "
                           f"ordem real bloqueada por precaução",
                    client_oid=client_oid)
            if travada:
                return Autorizacao(
                    False, f"PARADA DE EMERGÊNCIA ativa: "
                           f"{motivo or 'sem motivo registrado'}. Libere a "
                           f"trava explicitamente antes de operar",
                    client_oid=client_oid)

        if self.registry is None:
            return Autorizacao(
                False, "nenhum registro de estratégias disponível: ordem real "
                       "bloqueada por precaução", client_oid=client_oid)
        if self._chave is None:
            return Autorizacao(
                False, "nenhuma versão de estratégia vinculada ao motor: não "
                       "há evidência de validação por trás deste sinal, então "
                       "a ordem real está bloqueada", client_oid=client_oid)

        try:
            versao = self.registry.obter(self._chave)
        except Exception as exc:                        # noqa: BLE001
            return Autorizacao(
                False, f"versão vinculada {self._chave} não pôde ser lida: {exc}",
                client_oid=client_oid)

        fase = versao.fase
        base = {"fase": fase.value, "chave_estrategia": versao.chave,
                "client_oid": client_oid}

        if fase is Fase.REPROVADA:
            return Autorizacao(
                False, f"estratégia reprovada no gate "
                       f"({versao.motivo_reprovacao or 'sem motivo registrado'}): "
                       f"ordem real bloqueada", **base)
        if fase is Fase.APOSENTADA:
            return Autorizacao(
                False, "estratégia aposentada: ordem real bloqueada", **base)

        if fase in FASES_DEMO:
            em_demo = bool(self._em_demo()) if self._em_demo else False
            if not em_demo:
                return Autorizacao(
                    False,
                    "estratégia em demo, mas a conexão ativa aponta para a "
                    "conta REAL. Ordem bloqueada: enviar para o real uma "
                    "ordem de fase demo é o acidente que o ambiente de teste "
                    "existe para evitar",
                    faltam_fases=_fases_faltando(fase), **base)
            return Autorizacao(
                True, "fase demo com conexão apontada para o ambiente de "
                      "teste: a ordem vai para lá e não movimenta dinheiro",
                **base)

        if fase not in FASES_REAIS:
            faltam = _fases_faltando(fase)
            return Autorizacao(
                False,
                f"estratégia em {fase.value}: nenhuma ordem real sai antes de "
                f"assistido. Faltam as fases {', '.join(faltam)}",
                faltam_fases=faltam, **base)

        # Fase real com a conexão apontada para o demo é o espelho do caso
        # acima: a ordem iria para a conta de teste achando que opera.
        if self._em_demo is not None and self._em_demo():
            return Autorizacao(
                False,
                f"estratégia em {fase.value} (fase real), mas a conexão "
                f"aponta para o ambiente de DEMO. A ordem não movimentaria "
                f"dinheiro e o resultado medido não seria real",
                **base)

        if fase is Fase.ASSISTIDO:
            if not client_oid:
                return Autorizacao(
                    False, "fase assistido exige clientOid para conferir a "
                           "confirmação humana",
                    precisa_confirmacao=True, **base)
            ok, motivo = self._consumir(client_oid, sinal, agora)
            if not ok:
                return Autorizacao(False, motivo, precisa_confirmacao=True,
                                   **base)
            return Autorizacao(True, f"fase assistido: {motivo}", **base)

        # REAL_LIMITADO: libera, mas com teto próprio.
        if self.capital_usd <= 0:
            return Autorizacao(
                False, "capital da conta não sincronizado: sem saber o capital, "
                       "não é possível aplicar o teto da fase real_limitado",
                **base)
        teto = self.capital_usd * TETO_NOTIONAL_FRAC_REAL_LIMITADO
        if notional_usd > teto:
            return Autorizacao(
                False,
                f"notional US$ {notional_usd:.2f} acima do teto da fase "
                f"real_limitado (US$ {teto:.2f} = "
                f"{TETO_NOTIONAL_FRAC_REAL_LIMITADO:.0%} do capital)",
                teto_notional_usd=teto, **base)
        return Autorizacao(
            True, f"fase real_limitado: notional US$ {notional_usd:.2f} dentro "
                  f"do teto de US$ {teto:.2f}",
            teto_notional_usd=teto, **base)

    # ------------------------------------------------------------- inspeção
    def estado(self) -> dict[str, Any]:
        versao = None
        if self.registry is not None and self._chave is not None:
            try:
                v = self.registry.obter(self._chave)
                versao = {
                    "chave": v.chave, "fase": v.fase.value,
                    "operavel_real": v.operavel_real,
                    "faltam_fases": _fases_faltando(v.fase),
                }
            except Exception:                           # noqa: BLE001
                versao = None
        travada, motivo_trava = False, ""
        if self._trava is not None:
            try:
                travada, motivo_trava = self._trava()
            except Exception as exc:                    # noqa: BLE001
                travada, motivo_trava = True, f"trava ilegível: {exc}"
        return {
            "trava_ativa": travada,
            "trava_motivo": motivo_trava,
            "chave_vinculada": self._chave,
            "versao": versao,
            "capital_usd": round(self.capital_usd, 2),
            "teto_notional_usd": (
                round(self.capital_usd * TETO_NOTIONAL_FRAC_REAL_LIMITADO, 2)
                if self.capital_usd > 0 else None),
            "validade_confirmacao_s": self.validade_confirmacao_s,
            "em_demo": (bool(self._em_demo()) if self._em_demo else None),
            "confirmacoes_pendentes": len(self.confirmacoes_pendentes()),
            "fases_que_permitem_real": [f.value for f in FASES_REAIS],
        }


__all__ = ["Autorizacao", "GuardaError", "GuardaFase", "FASES_DEMO",
           "FASES_REAIS",
           "TETO_NOTIONAL_FRAC_REAL_LIMITADO"]
