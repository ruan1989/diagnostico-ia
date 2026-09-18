"""Journal de decisões e análise pós-operação.

A distinção que este módulo existe para manter
----------------------------------------------
**Lucro não valida o processo, e prejuízo não o invalida.**

Uma operação com expectativa positiva, tamanho correto e stop respeitado pode
perder — e continua sendo uma decisão boa. Uma operação alavancada demais, sem
tese e com stop movido pode dar lucro — e continua sendo uma decisão ruim, que
vai cobrar a conta na repetição.

Sistemas que avaliam decisão pelo resultado aprendem a coisa errada: reforçam
o comportamento imprudente que deu sorte e abandonam o processo correto que
teve azar. Por isso a análise pós-trade aqui pontua **processo** e
**resultado** em eixos separados, e nomeia explicitamente os quatro
quadrantes.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from ..models import Side, Trade


class QualidadeProcesso(str, Enum):
    CORRETO = "processo_correto"
    FALHO = "processo_falho"
    INDETERMINADO = "indeterminado"


class Quadrante(str, Enum):
    """Os quatro cruzamentos de processo x resultado."""

    ACERTO_MERECIDO = "processo_correto_com_lucro"
    AZAR = "processo_correto_com_prejuizo"
    SORTE = "processo_falho_com_lucro"
    ERRO_COBRADO = "processo_falho_com_prejuizo"
    INDETERMINADO = "indeterminado"


LICAO_POR_QUADRANTE: dict[Quadrante, str] = {
    Quadrante.ACERTO_MERECIDO:
        "Processo seguido e resultado positivo. Repetir o processo, não "
        "aumentar o tamanho por causa do acerto.",
    Quadrante.AZAR:
        "Processo seguido e resultado negativo. NÃO mudar a estratégia por "
        "causa desta operação: perdas fazem parte de uma expectativa "
        "positiva. Mudar regra depois de uma perda é o começo do overfitting "
        "ao vivo.",
    Quadrante.SORTE:
        "Processo violado e resultado positivo. É o caso mais perigoso do "
        "conjunto: o lucro reforça o comportamento errado. A violação precisa "
        "ser corrigida justamente porque desta vez saiu bem.",
    Quadrante.ERRO_COBRADO:
        "Processo violado e resultado negativo. A causa está na execução, não "
        "no mercado. Corrigir a violação antes de operar de novo.",
    Quadrante.INDETERMINADO:
        "Sem informação suficiente para avaliar o processo. Registrar o que "
        "faltou para que a próxima avaliação seja possível.",
}


@dataclass(slots=True)
class EntradaJournal:
    """Registro completo de uma decisão, com todos os campos pedidos."""

    id: str
    ts: int
    symbol: str
    mercado: str
    estrategia: str
    versao_estrategia: str
    side: Side
    tese: str
    entrada: float
    stop: float
    alvos: list[float]
    risco_usd: float
    risco_pct_capital: float
    score: float
    decisao: str
    # Procedência dos dados que sustentaram a decisão.
    dados_utilizados: list[str] = field(default_factory=list)
    fatores_favoraveis: list[str] = field(default_factory=list)
    fatores_contrarios: list[str] = field(default_factory=list)
    invalidacao: str = ""
    regime: str = ""
    probabilidade_estimada: float | None = None
    ev_estimado_r: float | None = None
    # Preenchido no fechamento.
    resultado_usd: float | None = None
    resultado_r: float | None = None
    motivo_saida: str = ""
    fechado_em: int | None = None
    # Preenchido pela análise pós-trade.
    erro_de_previsao: str = ""
    licao: str = ""
    quadrante: Quadrante | None = None
    violacoes: list[str] = field(default_factory=list)
    modo: str = "paper"

    @property
    def aberta(self) -> bool:
        return self.fechado_em is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "ts": self.ts, "symbol": self.symbol,
            "mercado": self.mercado, "estrategia": self.estrategia,
            "versao_estrategia": self.versao_estrategia,
            "side": self.side.value, "tese": self.tese,
            "entrada": self.entrada, "stop": self.stop, "alvos": self.alvos,
            "risco_usd": round(self.risco_usd, 2),
            "risco_pct_capital": round(self.risco_pct_capital, 4),
            "score": round(self.score, 2), "decisao": self.decisao,
            "dados_utilizados": self.dados_utilizados,
            "fatores_favoraveis": self.fatores_favoraveis,
            "fatores_contrarios": self.fatores_contrarios,
            "invalidacao": self.invalidacao, "regime": self.regime,
            "probabilidade_estimada": self.probabilidade_estimada,
            "ev_estimado_r": self.ev_estimado_r,
            "resultado_usd": (round(self.resultado_usd, 2)
                              if self.resultado_usd is not None else None),
            "resultado_r": (round(self.resultado_r, 4)
                            if self.resultado_r is not None else None),
            "motivo_saida": self.motivo_saida,
            "fechado_em": self.fechado_em, "aberta": self.aberta,
            "erro_de_previsao": self.erro_de_previsao, "licao": self.licao,
            "quadrante": self.quadrante.value if self.quadrante else None,
            "violacoes": self.violacoes, "modo": self.modo,
        }


@dataclass(slots=True)
class ChecklistProcesso:
    """O que precisa ter sido verdade para o processo estar correto."""

    tese_registrada: bool | None = None
    stop_definido_antes_da_entrada: bool | None = None
    stop_respeitado: bool | None = None
    tamanho_conforme_risco: bool | None = None
    risco_dentro_do_limite: bool | None = None
    dados_confiaveis: bool | None = None
    estatistica_suportava: bool | None = None
    regime_compativel: bool | None = None
    risco_aprovou: bool | None = None
    sem_aumento_apos_perda: bool | None = None

    def itens(self) -> dict[str, bool | None]:
        return {
            "tese_registrada": self.tese_registrada,
            "stop_definido_antes_da_entrada":
                self.stop_definido_antes_da_entrada,
            "stop_respeitado": self.stop_respeitado,
            "tamanho_conforme_risco": self.tamanho_conforme_risco,
            "risco_dentro_do_limite": self.risco_dentro_do_limite,
            "dados_confiaveis": self.dados_confiaveis,
            "estatistica_suportava": self.estatistica_suportava,
            "regime_compativel": self.regime_compativel,
            "risco_aprovou": self.risco_aprovou,
            "sem_aumento_apos_perda": self.sem_aumento_apos_perda,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.itens()


@dataclass(slots=True)
class AnalisePosTrade:
    entrada_id: str
    quadrante: Quadrante
    qualidade_processo: QualidadeProcesso
    violacoes: list[str] = field(default_factory=list)
    itens_nao_verificados: list[str] = field(default_factory=list)
    erro_de_previsao: str = ""
    licao: str = ""
    resultado_r: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entrada_id": self.entrada_id,
            "quadrante": self.quadrante.value,
            "qualidade_processo": self.qualidade_processo.value,
            "violacoes": self.violacoes,
            "itens_nao_verificados": self.itens_nao_verificados,
            "erro_de_previsao": self.erro_de_previsao,
            "licao": self.licao,
            "resultado_r": (round(self.resultado_r, 4)
                            if self.resultado_r is not None else None),
            "observacao": "processo e resultado são eixos independentes: "
                          "lucro não valida processo falho, e prejuízo não "
                          "invalida processo correto",
        }


# Itens cuja violação é grave o bastante para reprovar o processo sozinha.
VIOLACOES_GRAVES = {
    "stop_respeitado": "o stop não foi respeitado",
    "risco_dentro_do_limite": "o risco excedeu o limite configurado",
    "tamanho_conforme_risco": "o tamanho não seguiu o dimensionamento por risco",
    "risco_aprovou": "a operação foi aberta sem aprovação do Risk Engine",
    "sem_aumento_apos_perda": "a posição foi aumentada após uma perda "
                              "(martingale)",
}


def analisar_pos_trade(entrada: EntradaJournal,
                       checklist: ChecklistProcesso) -> AnalisePosTrade:
    """Avalia o PROCESSO, e só depois cruza com o resultado."""
    itens = checklist.itens()
    violacoes: list[str] = []
    nao_verificados: list[str] = []

    for chave, valor in itens.items():
        if valor is None:
            nao_verificados.append(chave)
        elif valor is False:
            violacoes.append(
                VIOLACOES_GRAVES.get(chave, f"{chave} não foi cumprido"))

    graves = [c for c, v in itens.items()
              if v is False and c in VIOLACOES_GRAVES]

    # Processo indeterminado quando falta verificar item grave.
    graves_nao_verificados = [c for c in nao_verificados
                              if c in VIOLACOES_GRAVES]
    if graves:
        qualidade = QualidadeProcesso.FALHO
    elif graves_nao_verificados:
        qualidade = QualidadeProcesso.INDETERMINADO
    elif violacoes:
        # Só violações leves: processo ainda é correto, com ressalvas.
        qualidade = QualidadeProcesso.CORRETO
    else:
        qualidade = QualidadeProcesso.CORRETO

    resultado = entrada.resultado_r
    if qualidade is QualidadeProcesso.INDETERMINADO or resultado is None:
        quadrante = Quadrante.INDETERMINADO
    elif qualidade is QualidadeProcesso.CORRETO:
        quadrante = (Quadrante.ACERTO_MERECIDO if resultado > 0
                     else Quadrante.AZAR)
    else:
        quadrante = (Quadrante.SORTE if resultado > 0
                     else Quadrante.ERRO_COBRADO)

    # ------------------------------------------------- erro de previsão
    erro = ""
    if (entrada.ev_estimado_r is not None and resultado is not None):
        diff = resultado - entrada.ev_estimado_r
        erro = (f"resultado de {resultado:+.2f}R contra expectativa de "
                f"{entrada.ev_estimado_r:+.2f}R (desvio de {diff:+.2f}R). "
                f"Um desvio isolado não diz nada sobre a estimativa: só a "
                f"média de muitas operações diz.")
    elif resultado is not None:
        erro = (f"resultado de {resultado:+.2f}R sem expectativa registrada "
                f"na entrada: sem ela não é possível medir erro de previsão")

    licao = LICAO_POR_QUADRANTE[quadrante]
    if violacoes and quadrante is Quadrante.SORTE:
        licao += (" Violações a corrigir: " + "; ".join(violacoes) + ".")
    elif violacoes and quadrante is Quadrante.ERRO_COBRADO:
        licao += (" Causa direta: " + "; ".join(violacoes) + ".")
    if nao_verificados:
        licao += (f" Itens não verificados nesta operação: "
                  f"{', '.join(nao_verificados)}.")

    return AnalisePosTrade(
        entrada_id=entrada.id, quadrante=quadrante,
        qualidade_processo=qualidade, violacoes=violacoes,
        itens_nao_verificados=nao_verificados, erro_de_previsao=erro,
        licao=licao, resultado_r=resultado)


class Journal:
    """Registro append-only de decisões, com análise pós-trade."""

    def __init__(self) -> None:
        self._entradas: dict[str, EntradaJournal] = {}
        self._seq = 0

    def registrar(self, *, symbol: str, side: Side, tese: str,
                  entrada: float, stop: float, alvos: Sequence[float],
                  risco_usd: float, risco_pct_capital: float,
                  score: float, decisao: str,
                  estrategia: str = "", versao_estrategia: str = "",
                  mercado: str = "cripto_futuros",
                  dados_utilizados: Sequence[str] = (),
                  fatores_favoraveis: Sequence[str] = (),
                  fatores_contrarios: Sequence[str] = (),
                  invalidacao: str = "", regime: str = "",
                  probabilidade_estimada: float | None = None,
                  ev_estimado_r: float | None = None,
                  modo: str = "paper",
                  agora_ms: int | None = None) -> EntradaJournal:
        self._seq += 1
        e = EntradaJournal(
            id=f"jrn-{self._seq:06d}",
            ts=agora_ms if agora_ms is not None else int(time.time() * 1000),
            symbol=symbol, mercado=mercado, estrategia=estrategia,
            versao_estrategia=versao_estrategia, side=side, tese=tese,
            entrada=entrada, stop=stop, alvos=list(alvos),
            risco_usd=risco_usd, risco_pct_capital=risco_pct_capital,
            score=score, decisao=decisao,
            dados_utilizados=list(dados_utilizados),
            fatores_favoraveis=list(fatores_favoraveis),
            fatores_contrarios=list(fatores_contrarios),
            invalidacao=invalidacao, regime=regime,
            probabilidade_estimada=probabilidade_estimada,
            ev_estimado_r=ev_estimado_r, modo=modo)
        self._entradas[e.id] = e
        return e

    def fechar(self, entrada_id: str, trade: Trade, *,
               checklist: ChecklistProcesso | None = None
               ) -> tuple[EntradaJournal, AnalisePosTrade]:
        e = self._entradas[entrada_id]
        e.resultado_usd = trade.pnl_usd
        e.resultado_r = trade.pnl_r
        e.motivo_saida = trade.motivo_saida
        e.fechado_em = trade.closed_at

        analise = analisar_pos_trade(e, checklist or ChecklistProcesso())
        e.quadrante = analise.quadrante
        e.erro_de_previsao = analise.erro_de_previsao
        e.licao = analise.licao
        e.violacoes = analise.violacoes
        return e, analise

    def obter(self, entrada_id: str) -> EntradaJournal:
        return self._entradas[entrada_id]

    def listar(self, *, apenas_abertas: bool = False,
               symbol: str | None = None,
               modo: str | None = None) -> list[EntradaJournal]:
        out = list(self._entradas.values())
        if apenas_abertas:
            out = [e for e in out if e.aberta]
        if symbol:
            out = [e for e in out if e.symbol == symbol.upper()]
        if modo:
            out = [e for e in out if e.modo == modo]
        return sorted(out, key=lambda e: e.ts, reverse=True)

    def resumo_por_quadrante(self) -> dict[str, Any]:
        """A leitura que importa: onde o processo está falhando."""
        contagem: dict[str, int] = {q.value: 0 for q in Quadrante}
        resultado_por_quadrante: dict[str, float] = {
            q.value: 0.0 for q in Quadrante}
        for e in self._entradas.values():
            if e.quadrante is None:
                continue
            contagem[e.quadrante.value] += 1
            resultado_por_quadrante[e.quadrante.value] += (e.resultado_r or 0.0)

        fechadas = sum(contagem.values())
        corretas = (contagem[Quadrante.ACERTO_MERECIDO.value]
                    + contagem[Quadrante.AZAR.value])
        falhas = (contagem[Quadrante.SORTE.value]
                  + contagem[Quadrante.ERRO_COBRADO.value])
        avaliaveis = corretas + falhas

        alertas: list[str] = []
        if contagem[Quadrante.SORTE.value] > 0:
            alertas.append(
                f"{contagem[Quadrante.SORTE.value]} operação(ões) com "
                f"processo violado e LUCRO: é o padrão mais perigoso, porque "
                f"o resultado positivo reforça o erro")
        if avaliaveis >= 10 and falhas / avaliaveis > 0.2:
            alertas.append(
                f"{falhas / avaliaveis:.0%} das operações avaliadas tiveram "
                f"processo violado: o problema está na execução, não na "
                f"estratégia")
        if contagem[Quadrante.INDETERMINADO.value] > avaliaveis:
            alertas.append(
                "a maioria das operações não pôde ser avaliada: o checklist "
                "de processo não está sendo preenchido")

        return {
            "total_registradas": len(self._entradas),
            "fechadas": fechadas,
            "por_quadrante": contagem,
            "resultado_r_por_quadrante": {
                k: round(v, 3) for k, v in resultado_por_quadrante.items()},
            "taxa_de_processo_correto": (round(corretas / avaliaveis, 4)
                                         if avaliaveis else None),
            "alertas": alertas,
            "licoes": {q.value: LICAO_POR_QUADRANTE[q] for q in Quadrante},
        }

    def exportar_jsonl(self) -> str:
        """Exporta o journal em JSON Lines, para auditoria externa."""
        return "\n".join(
            json.dumps(e.to_dict(), ensure_ascii=False)
            for e in sorted(self._entradas.values(), key=lambda x: x.ts))
