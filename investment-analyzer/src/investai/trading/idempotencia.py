"""Idempotência de ordens que atravessa reinício do processo.

O problema
----------
`clientOid` determinístico resolve o reenvio dentro da mesma sessão: mandar
duas vezes o mesmo id faz a corretora rejeitar a segunda. Mas ele não resolve
o cenário que de fato acontece em operação automatizada:

    o processo morre entre o envio e a resposta.

Na volta, o sistema não sabe se a ordem chegou. Sem informação, as duas
saídas são ruins: reenviar pode abrir posição dobrada; ignorar pode deixar
uma posição real viva e sem gestão — sem stop sendo acompanhado, sem alvo,
sem contabilizar risco.

A solução
---------
Gravar a intenção **antes** de enviar, e transformar a volta em uma pergunta
respondível: existe ordem com este `clientOid` na corretora?

    1. grava a intenção como `pendente`
    2. envia
    3. marca `confirmada` com a resposta

Se o passo 2 ou 3 não acontecer, a intenção fica `pendente` no banco, e a
subida seguinte pergunta à corretora o que houve com ela.

A regra que não se negocia
--------------------------
**Incerteza nunca é resolvida enviando outra ordem.**

Se a consulta falhar — rede fora, chave sem permissão, resposta ilegível — o
envio é recusado. É frustrante e é correto: uma ordem a menos custa uma
oportunidade; uma ordem a mais custa dinheiro real e ainda deixa uma posição
que o sistema acha que não tem.

Por isso `ExchangeUnreachable` e "ordem não existe" são tratados de formas
opostas aqui, em vez de caírem no mesmo `except`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("investai.idempotencia")

# Estados que uma intenção pode assumir.
PENDENTE = "pendente"
CONFIRMADA = "confirmada"
AUSENTE = "ausente"
FALHOU = "falhou"
ADOTADA = "adotada"

# Status de ordem na Bitget que significam "essa ordem existe e moveu ou pode
# mover dinheiro". Uma ordem cancelada sem execução não abre posição, então
# não impede um novo envio.
STATUS_VIVOS = frozenset({
    "live", "new", "partially_filled", "partial_fill", "filled",
    "full_fill", "init", "pending",
})
STATUS_MORTOS = frozenset({"cancelled", "canceled", "rejected", "expired"})


class _Consultavel(Protocol):
    def ordem_por_client_oid(self, symbol: str,
                             client_oid: str) -> dict[str, Any] | None: ...
    def posicoes(self) -> list[Any]: ...


@dataclass(slots=True)
class Veredicto:
    """O que fazer com uma intenção de envio."""

    pode_enviar: bool
    motivo: str
    # "nova": primeira tentativa; "ja_existe": ordem achada na corretora;
    # "reenvio_seguro": consulta provou ausência; "incerto": não deu para saber.
    situacao: str = "nova"
    ordem: dict[str, Any] | None = None
    order_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pode_enviar": self.pode_enviar, "motivo": self.motivo,
            "situacao": self.situacao, "order_id": self.order_id,
            "ordem": self.ordem,
        }


@dataclass(slots=True)
class ResultadoReconciliacao:
    """O que a subida do sistema descobriu sobre as intenções pendentes."""

    conferidas: int = 0
    adotadas: list[dict[str, Any]] = field(default_factory=list)
    ausentes: list[str] = field(default_factory=list)
    indeterminadas: list[dict[str, Any]] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def exige_atencao(self) -> bool:
        """Há ordem real possivelmente viva que o sistema não sabe gerenciar."""
        return bool(self.adotadas or self.indeterminadas)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conferidas": self.conferidas,
            "adotadas": self.adotadas,
            "ausentes": self.ausentes,
            "indeterminadas": self.indeterminadas,
            "exige_atencao": self.exige_atencao,
            "avisos": self.avisos,
        }


def _status(ordem: dict[str, Any]) -> str:
    for chave in ("state", "status", "orderStatus"):
        v = ordem.get(chave)
        if isinstance(v, str) and v:
            return v.strip().lower()
    return ""


def ordem_esta_viva(ordem: dict[str, Any]) -> bool:
    """Diz se a ordem encontrada impede um novo envio.

    Uma ordem cancelada ou rejeitada sem execução não abre posição, então não
    impede. Uma ordem em estado desconhecido impede: na dúvida, não manda
    outra.
    """
    st = _status(ordem)
    if st in STATUS_MORTOS:
        # Cancelada depois de execução parcial ainda deixou posição.
        for chave in ("baseVolume", "filledQty", "fillSize", "accBaseVolume"):
            try:
                if float(ordem.get(chave) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False
    if st in STATUS_VIVOS:
        return True
    return True                     # estado desconhecido: trata como viva


class ControleIdempotencia:
    """Decide se uma ordem pode ser enviada, e resolve o que ficou pendente."""

    def __init__(self, store: Any, backend: _Consultavel | None = None):
        self.store = store
        self.backend = backend

    # ------------------------------------------------------- antes de enviar
    def antes_de_enviar(self, *, client_oid: str, symbol: str, side: str,
                        size: float, entry: float, stop_loss: float,
                        agora_ms: int | None = None,
                        payload: dict[str, Any] | None = None) -> Veredicto:
        """Grava a intenção e decide se o envio pode acontecer."""
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        nova, existente = self.store.registrar_intencao_envio(
            client_oid=client_oid, symbol=symbol, side=side, size=size,
            entry=entry, stop_loss=stop_loss, criado_em=agora,
            payload=payload)

        if nova:
            return Veredicto(True, "primeira tentativa desta ordem",
                             situacao="nova")

        estado = (existente or {}).get("estado", PENDENTE)

        if estado in (CONFIRMADA, ADOTADA):
            return Veredicto(
                False,
                f"esta ordem já foi enviada antes (estado {estado}, order_id "
                f"{(existente or {}).get('order_id') or 'não registrado'}); "
                f"reenviar abriria posição dobrada",
                situacao="ja_existe",
                order_id=str((existente or {}).get("order_id") or ""))

        if estado == FALHOU:
            # A corretora recusou explicitamente. Não há ordem viva, então
            # tentar de novo é seguro — e é o comportamento desejado quando a
            # recusa foi transitória.
            return Veredicto(True, "tentativa anterior foi recusada pela "
                                   "corretora; reenvio é seguro",
                             situacao="reenvio_seguro")

        if estado == AUSENTE:
            return Veredicto(True, "consulta anterior provou que a ordem não "
                                   "existe na corretora; reenvio é seguro",
                             situacao="reenvio_seguro")

        # estado == PENDENTE: destino desconhecido. É aqui que a trava vale.
        return self._resolver_pendente(client_oid, symbol, agora)

    def _resolver_pendente(self, client_oid: str, symbol: str,
                           agora_ms: int) -> Veredicto:
        if self.backend is None or not hasattr(self.backend,
                                               "ordem_por_client_oid"):
            return Veredicto(
                False,
                "existe uma tentativa anterior desta ordem com destino "
                "desconhecido, e esta conexão não sabe consultar ordem por "
                "clientOid. Envio recusado: não dá para descartar que a ordem "
                "já esteja viva na corretora",
                situacao="incerto")
        try:
            ordem = self.backend.ordem_por_client_oid(symbol, client_oid)
        except Exception as exc:                        # noqa: BLE001
            log.error("consulta de ordem %s falhou: %s", client_oid, exc)
            return Veredicto(
                False,
                f"não foi possível consultar a ordem anterior na corretora "
                f"({exc}). Envio recusado: incerteza não se resolve mandando "
                f"outra ordem",
                situacao="incerto")

        if ordem is None:
            self.store.resolver_envio(
                client_oid, estado=AUSENTE, resolvido_em=agora_ms,
                detalhe="corretora não encontrou ordem com este clientOid")
            return Veredicto(
                True, "a corretora não tem ordem com este clientOid; a "
                      "tentativa anterior não chegou, então o envio é seguro",
                situacao="reenvio_seguro")

        if ordem_esta_viva(ordem):
            oid = str(ordem.get("orderId") or ordem.get("order_id") or "")
            self.store.resolver_envio(
                client_oid, estado=ADOTADA, resolvido_em=agora_ms,
                order_id=oid,
                detalhe=f"ordem encontrada na corretora em estado "
                        f"{_status(ordem) or 'desconhecido'}")
            return Veredicto(
                False,
                f"a tentativa anterior CHEGOU à corretora (order_id {oid or '?'}, "
                f"estado {_status(ordem) or 'desconhecido'}). Nenhuma nova "
                f"ordem foi enviada",
                situacao="ja_existe", ordem=ordem, order_id=oid)

        self.store.resolver_envio(
            client_oid, estado=AUSENTE, resolvido_em=agora_ms,
            detalhe=f"ordem anterior terminou em {_status(ordem)} sem execução")
        return Veredicto(
            True, f"a ordem anterior terminou em {_status(ordem)} sem mover "
                  f"posição; o envio é seguro",
            situacao="reenvio_seguro", ordem=ordem)

    # ------------------------------------------------------- depois de enviar
    def confirmar(self, client_oid: str, resposta: Any, *,
                  agora_ms: int | None = None) -> None:
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        oid = ""
        if isinstance(resposta, dict):
            oid = str(resposta.get("orderId") or resposta.get("order_id") or "")
        self.store.resolver_envio(client_oid, estado=CONFIRMADA,
                                  resolvido_em=agora, order_id=oid,
                                  detalhe="corretora aceitou a ordem")

    def registrar_falha(self, client_oid: str, erro: str, *,
                        agora_ms: int | None = None) -> None:
        """Marca recusa EXPLÍCITA da corretora.

        Só deve ser chamado quando a corretora respondeu negando. Um timeout
        não é recusa: ali a ordem pode ter chegado, e a intenção precisa
        continuar `pendente` para que a próxima subida a investigue.
        """
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        self.store.resolver_envio(client_oid, estado=FALHOU,
                                  resolvido_em=agora, detalhe=erro)

    # ------------------------------------------------------- na subida
    def reconciliar_pendentes(self, *, agora_ms: int | None = None
                              ) -> ResultadoReconciliacao:
        """Resolve, contra a corretora, tudo que ficou com destino incerto.

        Deve rodar na subida do sistema, antes de o motor começar a operar.
        Cada intenção pendente aqui é uma ordem que pode estar viva.
        """
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        res = ResultadoReconciliacao()
        pendentes = self.store.envios_pendentes()
        res.conferidas = len(pendentes)
        if not pendentes:
            return res

        if self.backend is None or not hasattr(self.backend,
                                               "ordem_por_client_oid"):
            res.indeterminadas = [
                {"client_oid": p["client_oid"], "symbol": p["symbol"],
                 "motivo": "sem conexão capaz de consultar ordem"}
                for p in pendentes]
            res.avisos.append(
                f"{len(pendentes)} intenção(ões) de ordem com destino "
                f"desconhecido e nenhuma conexão para conferir. Confira na "
                f"Bitget antes de operar: pode haver posição aberta que este "
                f"sistema não está gerenciando.")
            return res

        for p in pendentes:
            oid_cliente = p["client_oid"]
            try:
                ordem = self.backend.ordem_por_client_oid(p["symbol"],
                                                          oid_cliente)
            except Exception as exc:                    # noqa: BLE001
                res.indeterminadas.append(
                    {"client_oid": oid_cliente, "symbol": p["symbol"],
                     "motivo": str(exc)})
                continue

            if ordem is None:
                self.store.resolver_envio(
                    oid_cliente, estado=AUSENTE, resolvido_em=agora,
                    detalhe="não encontrada na subida")
                res.ausentes.append(oid_cliente)
                continue

            if ordem_esta_viva(ordem):
                order_id = str(ordem.get("orderId")
                               or ordem.get("order_id") or "")
                self.store.resolver_envio(
                    oid_cliente, estado=ADOTADA, resolvido_em=agora,
                    order_id=order_id,
                    detalhe=f"encontrada na subida em {_status(ordem)}")
                res.adotadas.append(
                    {"client_oid": oid_cliente, "symbol": p["symbol"],
                     "order_id": order_id, "estado": _status(ordem),
                     "side": p["side"], "size": p["size"],
                     "entry": p["entry"], "stop_loss": p["stop_loss"]})
            else:
                self.store.resolver_envio(
                    oid_cliente, estado=AUSENTE, resolvido_em=agora,
                    detalhe=f"terminou em {_status(ordem)} sem execução")
                res.ausentes.append(oid_cliente)

        if res.adotadas:
            res.avisos.append(
                f"{len(res.adotadas)} ordem(ns) enviada(s) antes da queda "
                f"chegou(aram) à corretora. Elas NÃO foram reenviadas. "
                f"Confira as posições correspondentes na Bitget: o stop foi "
                f"anexado à ordem de abertura, mas a gestão de alvos deste "
                f"sistema só passa a acompanhar posição que ele conhece.")
        if res.indeterminadas:
            res.avisos.append(
                f"{len(res.indeterminadas)} intenção(ões) continuam sem "
                f"resposta. Nada foi reenviado.")
        return res

    def estado(self) -> dict[str, Any]:
        return {
            "pendentes": len(self.store.envios_pendentes()),
            "ultimos": self.store.envios(limite=20),
            "pode_consultar": bool(
                self.backend is not None
                and hasattr(self.backend, "ordem_por_client_oid")),
        }


__all__ = [
    "ADOTADA", "AUSENTE", "CONFIRMADA", "ControleIdempotencia", "FALHOU",
    "PENDENTE", "ResultadoReconciliacao", "Veredicto", "ordem_esta_viva",
]
