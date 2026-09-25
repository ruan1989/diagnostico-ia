"""Envio de alertas para fora do processo.

O que muda em relação à central de alertas
------------------------------------------
`CentralDeAlertas` guarda alertas em memória: ótimo para o painel, inútil
para quem está dormindo. Este módulo leva os que importam para um lugar que
vibra no bolso.

O que ele NÃO faz é mandar tudo. Um canal que recebe cinquenta mensagens por
dia deixa de ser lido em uma semana, e aí o alerta que importava chega no
mesmo lugar que os outros quarenta e nove.

Três filtros, nesta ordem
-------------------------
1. **nível mínimo** — informativo fica no painel;
2. **deduplicação por janela** — um feed congelado geraria um alerta por
   ciclo;
3. **teto por hora** — se algo dispara em cascata, o canal entrega os
   primeiros e um resumo do resto, em vez de virar ruído.

O terceiro é o que salva o canal no dia ruim, que é justamente o dia em que
ele precisa funcionar.

A falha silenciosa
------------------
**Um canal de alerta que falha em silêncio é pior que não ter canal**, porque
quem confia nele para de olhar o painel.

Por isso toda falha de entrega é contada, a última fica registrada, e o
diagnóstico consulta esse estado. Um canal configurado e quebrado aparece
como problema, não como ausência.

Segredo
-------
O token nunca aparece em log, em `__repr__`, em mensagem de erro ou no
retorno de `estado()`. A URL da API do Telegram carrega o token no caminho,
então ela também nunca é registrada inteira.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .alertas import Alerta, NivelAlerta

log = logging.getLogger("investai.notificacao")

# Ordem de gravidade, para comparação.
ORDEM_NIVEL: dict[NivelAlerta, int] = {
    NivelAlerta.INFO: 0,
    NivelAlerta.ATENCAO: 1,
    NivelAlerta.URGENTE: 2,
}

TETO_POR_HORA_PADRAO = 12
JANELA_DEDUP_PADRAO_MS = 1_800_000        # 30 minutos


class Transporte(Protocol):
    def enviar(self, texto: str) -> None: ...
    def descricao(self) -> str: ...


@dataclass(slots=True)
class Entrega:
    ts: int
    titulo: str
    ok: bool
    erro: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "titulo": self.titulo, "ok": self.ok,
                "erro": self.erro}


class TransporteMemoria:
    """Transporte que só guarda. Usado em teste e quando nada está configurado."""

    def __init__(self) -> None:
        self.enviadas: list[str] = []
        self.falhar = False

    def enviar(self, texto: str) -> None:
        if self.falhar:
            raise RuntimeError("transporte configurado para falhar")
        self.enviadas.append(texto)

    def descricao(self) -> str:
        return "memória (nada sai do processo)"


class TransporteTelegram:
    """Envia por bot do Telegram.

    O token vem de variável de ambiente e não é guardado em lugar nenhum
    além deste objeto. `descricao()` e `__repr__` mostram só o id do chat,
    porque a URL da API carrega o token no caminho.
    """

    def __init__(self, token: str, chat_id: str, *,
                 timeout: float = 10.0, client: Any = None):
        if not token or not chat_id:
            raise ValueError(
                "token e chat_id são obrigatórios; um canal pela metade "
                "falharia em silêncio, que é o pior resultado possível")
        self._token = token
        self.chat_id = str(chat_id)
        self.timeout = timeout
        self._client = client

    def __repr__(self) -> str:
        return f"<TransporteTelegram chat={self.chat_id}>"

    def descricao(self) -> str:
        return f"Telegram, chat {self.chat_id}"

    def enviar(self, texto: str) -> None:
        import httpx
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        cliente = self._client or httpx.Client(timeout=self.timeout)
        fechar = self._client is None
        try:
            resp = cliente.post(url, json={
                "chat_id": self.chat_id, "text": texto,
                "disable_web_page_preview": True,
            })
            if resp.status_code >= 400:
                # A resposta de erro do Telegram não contém o token, mas a
                # URL contém — então a mensagem de erro cita só o status.
                raise RuntimeError(
                    f"Telegram recusou com HTTP {resp.status_code}")
        finally:
            if fechar:
                cliente.close()


def formatar(alerta: Alerta) -> str:
    """Texto curto, legível no celular, sem imperativo comercial."""
    marca = {"urgente": "[URGENTE]", "atencao": "[atenção]",
             "info": "[info]"}.get(alerta.nivel.value, "")
    linhas = [f"{marca} {alerta.titulo}".strip()]
    if alerta.symbol:
        linhas.append(f"par: {alerta.symbol}")
    linhas.append(alerta.mensagem)
    if alerta.acao_do_sistema:
        linhas.append(f"ação do sistema: {alerta.acao_do_sistema}")
    return "\n".join(linhas)


class Notificador:
    """Leva os alertas que importam para fora do processo."""

    def __init__(self, transporte: Transporte | None = None, *,
                 nivel_minimo: NivelAlerta = NivelAlerta.URGENTE,
                 teto_por_hora: int = TETO_POR_HORA_PADRAO,
                 janela_dedup_ms: int = JANELA_DEDUP_PADRAO_MS,
                 relogio: Callable[[], int] | None = None):
        self.transporte = transporte
        self.nivel_minimo = nivel_minimo
        self.teto_por_hora = teto_por_hora
        self.janela_dedup_ms = janela_dedup_ms
        self._relogio = relogio or (lambda: int(time.time() * 1000))
        self.entregues = 0
        self.falhas = 0
        self.suprimidos_por_nivel = 0
        self.suprimidos_por_duplicidade = 0
        self.suprimidos_por_teto = 0
        self.ultima_falha: Entrega | None = None
        self.historico: list[Entrega] = []
        self._ultimo_por_chave: dict[str, int] = {}
        self._enviados_ms: list[int] = []
        self._pendentes_resumo: list[Alerta] = []

    @property
    def configurado(self) -> bool:
        return self.transporte is not None

    # ------------------------------------------------------------ filtros
    def _passa_nivel(self, alerta: Alerta) -> bool:
        return (ORDEM_NIVEL.get(alerta.nivel, 0)
                >= ORDEM_NIVEL.get(self.nivel_minimo, 0))

    def _duplicado(self, alerta: Alerta, agora: int) -> bool:
        chave = f"{alerta.categoria.value}:{alerta.symbol}:{alerta.titulo}"
        anterior = self._ultimo_por_chave.get(chave)
        if anterior is not None and agora - anterior < self.janela_dedup_ms:
            return True
        self._ultimo_por_chave[chave] = agora
        return False

    def _dentro_do_teto(self, agora: int) -> bool:
        limite = agora - 3_600_000
        self._enviados_ms = [t for t in self._enviados_ms if t >= limite]
        return len(self._enviados_ms) < self.teto_por_hora

    # ------------------------------------------------------------- envio
    def notificar(self, alerta: Alerta) -> bool:
        """Envia se passar pelos filtros. Nunca levanta exceção.

        Uma falha de rede no canal de alerta não pode derrubar o ciclo de
        operação: o sistema precisa continuar gerenciando posição aberta
        mesmo sem conseguir avisar ninguém.
        """
        if not self.configurado:
            return False
        agora = self._relogio()

        if not self._passa_nivel(alerta):
            self.suprimidos_por_nivel += 1
            return False
        if self._duplicado(alerta, agora):
            self.suprimidos_por_duplicidade += 1
            return False
        if not self._dentro_do_teto(agora):
            self.suprimidos_por_teto += 1
            self._pendentes_resumo.append(alerta)
            return False

        return self._entregar(formatar(alerta), alerta.titulo, agora)

    def _entregar(self, texto: str, titulo: str, agora: int) -> bool:
        assert self.transporte is not None
        try:
            self.transporte.enviar(texto)
        except Exception as exc:                        # noqa: BLE001
            self.falhas += 1
            entrega = Entrega(agora, titulo, False, f"{type(exc).__name__}: {exc}")
            self.ultima_falha = entrega
            self.historico.append(entrega)
            del self.historico[:-50]
            log.warning("alerta não entregue (%s): %s", titulo, exc)
            return False
        self.entregues += 1
        self._enviados_ms.append(agora)
        entrega = Entrega(agora, titulo, True)
        self.historico.append(entrega)
        del self.historico[:-50]
        return True

    def despejar_resumo(self) -> bool:
        """Envia um resumo do que foi suprimido pelo teto.

        Sem isto, uma cascata de alertas sumiria sem deixar rastro no canal
        — e o operador acharia que estava tudo quieto justamente no dia em
        que não estava.
        """
        if not self.configurado or not self._pendentes_resumo:
            return False
        agora = self._relogio()
        por_titulo: dict[str, int] = {}
        for a in self._pendentes_resumo:
            por_titulo[a.titulo] = por_titulo.get(a.titulo, 0) + 1
        linhas = [f"[resumo] {len(self._pendentes_resumo)} alerta(s) não "
                  f"enviados por limite de frequência:"]
        linhas += [f"  {n}x {titulo}" for titulo, n in
                   sorted(por_titulo.items(), key=lambda p: -p[1])[:10]]
        self._pendentes_resumo.clear()
        return self._entregar("\n".join(linhas), "resumo de supressões", agora)

    def testar(self) -> dict[str, Any]:
        """Prova que o canal funciona. O único jeito honesto de saber."""
        if not self.configurado:
            return {"ok": False,
                    "motivo": "nenhum canal externo configurado"}
        agora = self._relogio()
        ok = self._entregar(
            "[teste] canal de alertas do InvestAI funcionando. Esta mensagem "
            "não indica nenhum evento de mercado.", "teste de canal", agora)
        return {"ok": ok, "transporte": self.transporte.descricao(),
                "erro": (self.ultima_falha.erro
                         if not ok and self.ultima_falha else "")}

    # ---------------------------------------------------------- inspeção
    def estado(self) -> dict[str, Any]:
        return {
            "configurado": self.configurado,
            "transporte": (self.transporte.descricao()
                           if self.transporte else None),
            "nivel_minimo": self.nivel_minimo.value,
            "teto_por_hora": self.teto_por_hora,
            "entregues": self.entregues,
            "falhas": self.falhas,
            "suprimidos_por_nivel": self.suprimidos_por_nivel,
            "suprimidos_por_duplicidade": self.suprimidos_por_duplicidade,
            "suprimidos_por_teto": self.suprimidos_por_teto,
            "aguardando_resumo": len(self._pendentes_resumo),
            "ultima_falha": (self.ultima_falha.to_dict()
                             if self.ultima_falha else None),
            "saudavel": self.configurado and self.falhas == 0,
            "observacao": (
                "Um canal que falha em silêncio é pior que não ter canal, "
                "porque quem confia nele para de olhar o painel. Por isso as "
                "falhas são contadas e o diagnóstico as consulta."),
        }


def telegram_do_ambiente(env: dict[str, str] | None = None
                         ) -> TransporteTelegram | None:
    """Monta o transporte a partir do ambiente, ou devolve None.

    Segredo em variável de ambiente, nunca em código nem em arquivo do
    repositório — a mesma regra das chaves da corretora.
    """
    import os
    fonte = env if env is not None else os.environ
    token = (fonte.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (fonte.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return None
    return TransporteTelegram(token, chat)


__all__ = [
    "Entrega", "JANELA_DEDUP_PADRAO_MS", "Notificador", "ORDEM_NIVEL",
    "TETO_POR_HORA_PADRAO", "Transporte", "TransporteMemoria",
    "TransporteTelegram", "formatar", "telegram_do_ambiente",
]
