"""Alertas técnicos.

Linguagem deste módulo
----------------------
Nenhum alerta usa imperativo comercial. Não existe "COMPRE AGORA" nem
"OPORTUNIDADE IMPERDÍVEL". Um alerta informa um fato mensurável e, quando
cabe, a ação que o sistema tomou — porque alerta que empurra decisão é
publicidade, não informação.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CategoriaAlerta(str, Enum):
    OPORTUNIDADE = "oportunidade"
    RISCO = "risco"
    STOP = "stop"
    REGIME = "mudanca_de_regime"
    NOTICIA = "noticia_critica"
    VOLATILIDADE = "volatilidade"
    LIQUIDACAO = "liquidacao"
    DRAWDOWN = "drawdown"
    EXPOSICAO = "exposicao"
    CIRCUIT_BREAKER = "circuit_breaker"
    QUALIDADE_DADOS = "qualidade_de_dados"
    SAUDE_SISTEMA = "saude_do_sistema"
    PROMOCAO = "promocao_de_estrategia"


class NivelAlerta(str, Enum):
    INFO = "info"
    ATENCAO = "atencao"
    URGENTE = "urgente"


# Termos que este módulo se recusa a emitir.
TERMOS_PROIBIDOS = (
    "compre agora", "não perca", "imperdível", "garantido", "lucro certo",
    "oportunidade única", "última chance", "risco zero", "infalível",
    "dinheiro fácil", "vai explodir", "100% de acerto",
)


class AlertaInvalido(ValueError):
    pass


@dataclass(slots=True)
class Alerta:
    categoria: CategoriaAlerta
    nivel: NivelAlerta
    titulo: str
    mensagem: str
    ts: int = 0
    symbol: str = ""
    # O que o sistema fez (ou deixou de fazer) por causa disto.
    acao_do_sistema: str = ""
    metricas: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        texto = f"{self.titulo} {self.mensagem}".lower()
        for termo in TERMOS_PROIBIDOS:
            if termo in texto:
                raise AlertaInvalido(
                    f"alerta contém linguagem promocional proibida "
                    f"({termo!r}): alertas informam fato mensurável, não "
                    f"empurram decisão")
        if not self.ts:
            self.ts = int(time.time() * 1000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "categoria": self.categoria.value, "nivel": self.nivel.value,
            "titulo": self.titulo, "mensagem": self.mensagem,
            "ts": self.ts, "symbol": self.symbol,
            "acao_do_sistema": self.acao_do_sistema,
            "metricas": self.metricas,
        }


class CentralDeAlertas:
    """Acumula alertas com deduplicação por janela."""

    def __init__(self, janela_dedup_ms: int = 900_000, limite: int = 500):
        self.janela_dedup_ms = janela_dedup_ms
        self.limite = limite
        self._alertas: list[Alerta] = []
        self._ultimo_por_chave: dict[str, int] = {}

    def emitir(self, alerta: Alerta) -> bool:
        """Registra o alerta. Devolve False se foi suprimido por duplicidade.

        Deduplicar importa: um feed congelado geraria um alerta por ciclo, e
        centenas de avisos idênticos escondem o que é novo.
        """
        chave = f"{alerta.categoria.value}:{alerta.symbol}:{alerta.titulo}"
        anterior = self._ultimo_por_chave.get(chave)
        if anterior is not None and alerta.ts - anterior < self.janela_dedup_ms:
            return False
        self._ultimo_por_chave[chave] = alerta.ts
        self._alertas.append(alerta)
        if len(self._alertas) > self.limite:
            self._alertas = self._alertas[-self.limite:]
        return True

    def listar(self, *, categoria: CategoriaAlerta | None = None,
               nivel: NivelAlerta | None = None,
               desde_ms: int | None = None,
               limite: int = 100) -> list[Alerta]:
        out = list(self._alertas)
        if categoria:
            out = [a for a in out if a.categoria is categoria]
        if nivel:
            out = [a for a in out if a.nivel is nivel]
        if desde_ms is not None:
            out = [a for a in out if a.ts >= desde_ms]
        return sorted(out, key=lambda a: a.ts, reverse=True)[:limite]

    def resumo(self) -> dict[str, Any]:
        por_categoria: dict[str, int] = {}
        por_nivel: dict[str, int] = {}
        for a in self._alertas:
            por_categoria[a.categoria.value] = por_categoria.get(
                a.categoria.value, 0) + 1
            por_nivel[a.nivel.value] = por_nivel.get(a.nivel.value, 0) + 1
        return {
            "total": len(self._alertas),
            "por_categoria": por_categoria,
            "por_nivel": por_nivel,
            "urgentes_abertos": [
                a.to_dict() for a in self.listar(nivel=NivelAlerta.URGENTE,
                                                 limite=10)],
        }

    def limpar(self) -> None:
        self._alertas.clear()
        self._ultimo_por_chave.clear()


# ------------------------------------------------------- construtores
def alerta_oportunidade(symbol: str, direcao: str, score: float,
                        decisao: str, ev_r: float, n_amostra: int, *,
                        ts: int = 0) -> Alerta:
    return Alerta(
        CategoriaAlerta.OPORTUNIDADE, NivelAlerta.INFO,
        titulo=f"{symbol}: candidato {direcao} classificado como {decisao}",
        mensagem=(f"score interno {score:.1f}; expectativa medida "
                  f"{ev_r:+.3f}R sobre amostra de {n_amostra} operações. "
                  f"Score é ferramenta quantitativa interna e não representa "
                  f"probabilidade de lucro."),
        symbol=symbol, ts=ts,
        acao_do_sistema="registrado para acompanhamento; execução depende do "
                        "Risk Engine e da fase da estratégia",
        metricas={"score": round(score, 2), "ev_r": round(ev_r, 4),
                  "n": n_amostra})


def alerta_risco(symbol: str, motivo: str, *, ts: int = 0) -> Alerta:
    return Alerta(
        CategoriaAlerta.RISCO, NivelAlerta.ATENCAO,
        titulo=f"{symbol}: entrada recusada pelo Risk Engine",
        mensagem=motivo, symbol=symbol, ts=ts,
        acao_do_sistema="nenhuma posição aberta")


def alerta_stop(symbol: str, preco: float, resultado_r: float, *,
                ts: int = 0) -> Alerta:
    return Alerta(
        CategoriaAlerta.STOP, NivelAlerta.INFO,
        titulo=f"{symbol}: stop executado em {preco:.6g}",
        mensagem=(f"resultado de {resultado_r:+.2f}R. Perda dentro do plano "
                  f"não indica falha de estratégia."),
        symbol=symbol, ts=ts, acao_do_sistema="posição encerrada",
        metricas={"preco": preco, "resultado_r": round(resultado_r, 4)})


def alerta_drawdown(atual_pct: float, limite_pct: float, *,
                    ts: int = 0) -> Alerta:
    urgente = atual_pct >= limite_pct
    return Alerta(
        CategoriaAlerta.DRAWDOWN,
        NivelAlerta.URGENTE if urgente else NivelAlerta.ATENCAO,
        titulo=f"drawdown em {atual_pct:.2f}% do capital",
        mensagem=(f"limite configurado: {limite_pct:.2f}%."
                  + (" Limite atingido." if urgente
                     else f" Restam {limite_pct - atual_pct:.2f} pontos "
                          f"percentuais até o limite.")),
        ts=ts,
        acao_do_sistema=("kill switch acionado: nenhuma nova operação até "
                         "rearme manual" if urgente
                         else "operação segue dentro dos limites"),
        metricas={"atual_pct": round(atual_pct, 3),
                  "limite_pct": limite_pct})


def alerta_circuit_breaker(qual: str, detalhe: str, *, ts: int = 0) -> Alerta:
    return Alerta(
        CategoriaAlerta.CIRCUIT_BREAKER, NivelAlerta.URGENTE,
        titulo=f"circuit breaker acionado: {qual}",
        mensagem=detalhe, ts=ts,
        acao_do_sistema="TRADING HALTED; reativação exige confirmação "
                        "explícita")


def alerta_regime(symbol: str, de: str, para: str,
                  desabilitadas: list[str], *, ts: int = 0) -> Alerta:
    return Alerta(
        CategoriaAlerta.REGIME, NivelAlerta.ATENCAO,
        titulo=f"{symbol}: regime mudou de {de} para {para}",
        mensagem=(f"estratégias desabilitadas neste regime: "
                  f"{', '.join(desabilitadas) or 'nenhuma'}"),
        symbol=symbol, ts=ts,
        acao_do_sistema="famílias de estratégia incompatíveis com o novo "
                        "regime foram desabilitadas")


def alerta_qualidade_dados(symbol: str, motivos: list[str], *,
                           ts: int = 0) -> Alerta:
    return Alerta(
        CategoriaAlerta.QUALIDADE_DADOS, NivelAlerta.URGENTE,
        titulo=f"{symbol}: qualidade de dados inadequada",
        mensagem="; ".join(motivos), symbol=symbol, ts=ts,
        acao_do_sistema="geração de sinais suspensa para este símbolo")


def alerta_saude(estado: str, motivos: list[str], *, ts: int = 0) -> Alerta:
    urgente = estado.upper() == "OFFLINE"
    return Alerta(
        CategoriaAlerta.SAUDE_SISTEMA,
        NivelAlerta.URGENTE if urgente else NivelAlerta.ATENCAO,
        titulo=f"saúde do sistema: {estado}",
        mensagem="; ".join(motivos) or "sem detalhes", ts=ts,
        acao_do_sistema=("abertura de posição bloqueada" if urgente
                         else "tamanho de posição reduzido"))


def alerta_liquidacao(symbol: str, volume_usd: float, desvios: float, *,
                      ts: int = 0) -> Alerta:
    return Alerta(
        CategoriaAlerta.LIQUIDACAO, NivelAlerta.URGENTE,
        titulo=f"{symbol}: onda de liquidações",
        mensagem=(f"US$ {volume_usd:,.0f} liquidados em 24h, {desvios:.1f} "
                  f"desvios acima do normal. Preço movido por fechamento "
                  f"forçado, não por fluxo com tese."),
        symbol=symbol, ts=ts,
        acao_do_sistema="abertura de posição bloqueada neste símbolo",
        metricas={"volume_usd": volume_usd, "desvios": round(desvios, 2)})


def alerta_exposicao(exposicao_usd: float, limite_usd: float,
                     apostas_efetivas: float, n_posicoes: int, *,
                     ts: int = 0) -> Alerta:
    return Alerta(
        CategoriaAlerta.EXPOSICAO, NivelAlerta.ATENCAO,
        titulo=f"exposição de US$ {exposicao_usd:,.2f}",
        mensagem=(f"limite US$ {limite_usd:,.2f}. As {n_posicoes} posições "
                  f"equivalem a {apostas_efetivas:.1f} apostas independentes "
                  f"pela correlação medida."),
        ts=ts,
        acao_do_sistema=("novas entradas bloqueadas por exposição"
                         if exposicao_usd >= limite_usd
                         else "dentro do limite"),
        metricas={"exposicao_usd": round(exposicao_usd, 2),
                  "limite_usd": round(limite_usd, 2),
                  "apostas_efetivas": round(apostas_efetivas, 2)})


def alerta_promocao(chave: str, de: str, para: str, aprovado: bool,
                    resumo: str, *, ts: int = 0) -> Alerta:
    return Alerta(
        CategoriaAlerta.PROMOCAO, NivelAlerta.INFO,
        titulo=(f"{chave}: {'promovida' if aprovado else 'mantida'} "
                f"{'para ' + para if aprovado else 'em ' + de}"),
        mensagem=resumo, ts=ts,
        acao_do_sistema=("estratégia avançou de fase" if aprovado
                         else "estratégia permanece na fase atual"))
