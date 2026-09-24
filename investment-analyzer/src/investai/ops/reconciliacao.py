"""Reconciliação: o que este sistema acha que tem contra o que a corretora tem.

O cenário
---------
O sistema mantém um estado local: posições abertas, tamanhos, stops, capital.
A corretora mantém o dela. Eles divergem por motivos banais e frequentes —
uma ordem executada parcialmente, um stop disparado enquanto o processo
estava caído, uma liquidação, uma ordem enviada de outro lugar (o aplicativo
da corretora, por exemplo).

A divergência em si não é o problema. O problema é **operar em cima do
estado errado**: dimensionar a próxima posição contra um capital que não
existe, ou achar que há proteção onde não há.

A regra
-------
**Divergência pausa a operação.** Não corrige sozinho, não sincroniza
sozinho: pausa e reporta. A correção automática seria pior que o erro —
fechar uma posição que "não deveria existir" pode ser fechar uma posição
legítima aberta à mão, e abrir uma que "está faltando" pode dobrar
exposição.

A única coisa que o sistema faz sozinho é parar.

O que conta como divergência
----------------------------
* posição na corretora que o sistema não conhece (mais grave: exposição sem
  gestão);
* posição no sistema que a corretora não tem (o stop pode ter disparado);
* mesmo par com tamanho ou lado diferente;
* stop na corretora diferente do stop local, além da tolerância do tick;
* capital divergente além da tolerância.

A tolerância existe porque preço e tamanho vêm arredondados ao passo do
contrato, e comparar float com igualdade exata geraria divergência a cada
ciclo — o que treinaria o operador a ignorar o alarme.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..models import Position

log = logging.getLogger("investai.reconciliacao")

# Tolerâncias relativas. Abaixo disso a diferença é arredondamento, não
# divergência.
TOL_TAMANHO = 0.01          # 1% do tamanho
TOL_PRECO = 0.002           # 0,2% do preço
TOL_CAPITAL = 0.01          # 1% do capital

GRAVIDADES = ("critica", "alta", "media")


@dataclass(slots=True)
class Divergencia:
    tipo: str
    symbol: str
    gravidade: str
    local: Any
    remoto: Any
    detalhe: str

    @property
    def pausa(self) -> bool:
        """Divergências críticas e altas param a operação."""
        return self.gravidade in ("critica", "alta")

    def to_dict(self) -> dict[str, Any]:
        return {"tipo": self.tipo, "symbol": self.symbol,
                "gravidade": self.gravidade, "local": self.local,
                "remoto": self.remoto, "detalhe": self.detalhe,
                "pausa": self.pausa}


@dataclass(slots=True)
class RelatorioReconciliacao:
    conferido_em_ms: int = 0
    posicoes_locais: int = 0
    posicoes_remotas: int = 0
    divergencias: list[Divergencia] = field(default_factory=list)
    conferidas: list[str] = field(default_factory=list)
    erro: str = ""

    @property
    def consultou(self) -> bool:
        return not self.erro

    @property
    def deve_pausar(self) -> bool:
        return any(d.pausa for d in self.divergencias)

    @property
    def veredicto(self) -> str:
        if self.erro:
            return "NAO_CONFERIDO"
        if self.deve_pausar:
            return "DIVERGENTE"
        if self.divergencias:
            return "DIVERGENCIA_MENOR"
        return "COERENTE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "veredicto": self.veredicto,
            "consultou": self.consultou,
            "deve_pausar": self.deve_pausar,
            "conferido_em_ms": self.conferido_em_ms,
            "posicoes_locais": self.posicoes_locais,
            "posicoes_remotas": self.posicoes_remotas,
            "conferidas": self.conferidas,
            "divergencias": [d.to_dict() for d in self.divergencias],
            "erro": self.erro,
            "observacao": (
                "Divergência PAUSA a operação; não é corrigida "
                "automaticamente. Fechar uma posição 'que não deveria "
                "existir' pode fechar uma posição legítima aberta à mão, e "
                "abrir uma 'que está faltando' pode dobrar exposição."),
        }

    def texto(self) -> str:
        linhas = [f"RECONCILIAÇÃO — {self.veredicto}",
                  f"local: {self.posicoes_locais} posição(ões) | "
                  f"corretora: {self.posicoes_remotas}"]
        if self.erro:
            linhas.append(f"  NÃO FOI POSSÍVEL CONFERIR: {self.erro}")
        for d in self.divergencias:
            marca = "PAUSA" if d.pausa else "aviso"
            linhas.append(f"  [{marca}] {d.symbol} {d.tipo}: {d.detalhe}")
        return "\n".join(linhas)


def _perto(a: float, b: float, tol: float) -> bool:
    escala = max(abs(a), abs(b), 1e-12)
    return abs(a - b) <= tol * escala


def comparar_posicoes(locais: Sequence[Position],
                      remotas: Sequence[Position]) -> list[Divergencia]:
    """Compara posição a posição, por par."""
    por_local = {p.symbol.upper(): p for p in locais}
    por_remoto = {p.symbol.upper(): p for p in remotas}
    divs: list[Divergencia] = []

    for symbol in sorted(set(por_local) | set(por_remoto)):
        loc = por_local.get(symbol)
        rem = por_remoto.get(symbol)

        if loc is None:
            divs.append(Divergencia(
                "posicao_desconhecida", symbol, "critica", None,
                {"side": rem.side.value, "size": rem.size, "entry": rem.entry},
                f"a corretora tem posição {rem.side.value} de {rem.size} em "
                f"{symbol} que este sistema NÃO conhece: é exposição real sem "
                f"gestão de stop nem de alvo"))
            continue

        if rem is None:
            divs.append(Divergencia(
                "posicao_ausente", symbol, "alta",
                {"side": loc.side.value, "size": loc.size,
                 "entry": loc.entry}, None,
                f"este sistema acha que tem {loc.side.value} de {loc.size} em "
                f"{symbol}, mas a corretora não tem: o stop provavelmente "
                f"disparou enquanto o sistema não estava olhando"))
            continue

        if loc.side is not rem.side:
            divs.append(Divergencia(
                "lado_diferente", symbol, "critica", loc.side.value,
                rem.side.value,
                f"lado oposto: local {loc.side.value}, corretora "
                f"{rem.side.value}. Qualquer gestão daqui vai na direção "
                f"errada"))
            continue

        if not _perto(loc.size, rem.size, TOL_TAMANHO):
            divs.append(Divergencia(
                "tamanho_diferente", symbol, "alta", loc.size, rem.size,
                f"tamanho local {loc.size} contra {rem.size} na corretora "
                f"({abs(loc.size - rem.size) / max(rem.size, 1e-12):.1%} de "
                f"diferença): execução parcial ou fechamento que o sistema "
                f"não registrou"))

        # Stop zero na corretora significa SEM PROTEÇÃO, e isso é crítico —
        # não é uma diferença de arredondamento.
        if loc.stop_loss > 0 and rem.stop_loss <= 0:
            divs.append(Divergencia(
                "stop_ausente", symbol, "critica", loc.stop_loss,
                rem.stop_loss,
                f"o sistema acha que há stop em {loc.stop_loss}, mas a "
                f"corretora não tem stop nenhum nesta posição: a perda é "
                f"ilimitada até alguém agir"))
        elif (loc.stop_loss > 0 and rem.stop_loss > 0
              and not _perto(loc.stop_loss, rem.stop_loss, TOL_PRECO)):
            divs.append(Divergencia(
                "stop_diferente", symbol, "alta", loc.stop_loss,
                rem.stop_loss,
                f"stop local {loc.stop_loss} contra {rem.stop_loss} na "
                f"corretora: o risco calculado aqui não é o risco real"))

    return divs


def reconciliar(locais: Sequence[Position], backend: Any, *,
                capital_local: float | None = None,
                agora_ms: int | None = None) -> RelatorioReconciliacao:
    """Confere o estado local contra o da corretora.

    Falha de consulta NÃO é "coerente": é `NAO_CONFERIDO`, e não libera
    operação. Tratar rede fora como confirmação seria o mesmo erro que a
    idempotência evita — confundir "não sei" com "está tudo bem".
    """
    agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
    rel = RelatorioReconciliacao(conferido_em_ms=agora,
                                 posicoes_locais=len(locais))

    if backend is None:
        rel.erro = ("nenhuma conexão com a corretora: o estado local não pôde "
                    "ser conferido contra nada")
        return rel

    try:
        remotas = list(backend.posicoes())
    except Exception as exc:                            # noqa: BLE001
        log.error("consulta de posições falhou: %s", exc)
        rel.erro = f"não foi possível ler as posições da corretora: {exc}"
        return rel

    rel.posicoes_remotas = len(remotas)
    rel.divergencias = comparar_posicoes(locais, remotas)
    rel.conferidas = sorted({p.symbol.upper() for p in list(locais) + remotas})

    if capital_local is not None:
        try:
            saldo = float(backend.saldo_usdt())
        except Exception as exc:                        # noqa: BLE001
            rel.divergencias.append(Divergencia(
                "capital_ilegivel", "", "media", capital_local, None,
                f"não foi possível ler o saldo: {exc}"))
        else:
            if not _perto(capital_local, saldo, TOL_CAPITAL):
                rel.divergencias.append(Divergencia(
                    "capital_diferente", "", "alta", capital_local, saldo,
                    f"capital local US$ {capital_local:.2f} contra "
                    f"US$ {saldo:.2f} na corretora: o dimensionamento da "
                    f"próxima posição sairia errado"))
    return rel


class Reconciliador:
    """Roda a reconciliação e aciona a pausa quando ela diverge."""

    def __init__(self, executor: Any, store: Any, trava: Any = None,
                 risk: Any = None):
        self.executor = executor
        self.store = store
        # `trava` é a mesma parada de emergência dos comandos operacionais.
        # Reusá-la é deliberado: existir um segundo mecanismo de pausa
        # significaria dois lugares para lembrar de destravar.
        self.trava = trava
        self.risk = risk
        self.ultimo: RelatorioReconciliacao | None = None

    def conferir(self, *, pausar: bool = True,
                 agora_ms: int | None = None) -> RelatorioReconciliacao:
        capital = None
        if self.risk is not None:
            try:
                capital = float(self.risk.estado.capital_atual)
            except Exception:                           # noqa: BLE001
                capital = None

        rel = reconciliar(self.executor.posicoes(), self.executor.backend,
                          capital_local=capital, agora_ms=agora_ms)
        self.ultimo = rel

        if rel.divergencias or rel.erro:
            self.store.registrar_evento(
                "ALERTA" if rel.deve_pausar else "INFO", "reconciliacao",
                f"reconciliação: {rel.veredicto}", rel.to_dict())

        if pausar and rel.deve_pausar and self.trava is not None:
            motivo = ("divergência com a corretora: "
                      + "; ".join(d.detalhe[:90] for d in rel.divergencias
                                  if d.pausa)[:400])
            self.trava.ativar(motivo, origem="reconciliacao",
                              agora_ms=agora_ms)
            self.store.registrar_evento(
                "ALERTA", "reconciliacao",
                "operação TRAVADA por divergência com a corretora",
                rel.to_dict())
        return rel

    def estado(self) -> dict[str, Any]:
        return {
            "ultimo": self.ultimo.to_dict() if self.ultimo else None,
            "tolerancias": {"tamanho": TOL_TAMANHO, "preco": TOL_PRECO,
                            "capital": TOL_CAPITAL},
        }


__all__ = [
    "Divergencia", "GRAVIDADES", "Reconciliador", "RelatorioReconciliacao",
    "TOL_CAPITAL", "TOL_PRECO", "TOL_TAMANHO", "comparar_posicoes",
    "reconciliar",
]
