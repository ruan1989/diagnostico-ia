"""Ranking do universo de ativos, com entrada e saída automáticas.

O problema
----------
Um universo fixo envelhece. Um par que tinha liquidez boa há seis meses pode
estar com spread de 0,4% hoje; um recém-listado pode ter passado a ser o
melhor candidato. Manter a lista na mão significa que ela vai ficar errada, e
o sistema vai continuar operando um ativo cujo custo de transação já come a
vantagem inteira.

A histerese, e por que ela não é detalhe
----------------------------------------
Um ativo no limite do critério oscila em torno dele. Sem histerese, ele sai
do universo em um ciclo, volta no seguinte, sai no outro — e cada saída
descarta a estatística acumulada daquele par, que é o ativo mais valioso que
o sistema tem.

Por isso a remoção exige `ciclos_para_remover` leituras ruins CONSECUTIVAS, e
a readmissão exige `ciclos_para_readmitir` leituras boas consecutivas, com o
segundo maior que o primeiro. Assimetria proposital: é mais barato ficar de
fora de um ativo bom do que dentro de um ativo caro.

O que derruba um ativo
----------------------
* **liquidez** abaixo do piso: com volume baixo, o próprio sistema move o
  preço, e o backtest deixa de descrever a execução;
* **spread** acima do teto: vantagem de 0,3R desaparece se entrar e sair
  custa 0,25R;
* **qualidade de dado** reprovada: sem série confiável não há medição, e sem
  medição não há decisão;
* **funding** extremo e persistente, em futuros: o custo de carrego consome a
  expectativa mesmo quando a direção está certa.

O ranking é relativo; os pisos são absolutos. Um ativo pode ser o melhor do
universo e ainda assim ser inoperável — e nesse caso ele sai, em vez de
liderar uma lista ruim.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

# Pisos e tetos absolutos. Abaixo/acima deles o ativo é inoperável,
# independente de como se compara aos outros.
VOLUME_MINIMO_USD = 20_000_000.0      # volume de 24h
SPREAD_MAXIMO_PCT = 0.15              # ida e volta consome ~0,30%
FUNDING_MAXIMO_ABS = 0.003            # 0,3% por período

CICLOS_PARA_REMOVER = 3
CICLOS_PARA_READMITIR = 5

# Pesos do score relativo. Somam 1,0.
PESOS = {
    "liquidez": 0.35,
    "spread": 0.30,
    "qualidade": 0.20,
    "estabilidade_funding": 0.15,
}


@dataclass(slots=True)
class LeituraAtivo:
    """O que se sabe de um ativo em um instante.

    `qualidade_ok` vem da camada de dados, não é recalculada aqui: quem sabe
    se a série tem furo é quem a montou.
    """

    symbol: str
    volume_24h_usd: float = 0.0
    spread_pct: float | None = None
    funding_rate: float | None = None
    qualidade_ok: bool = True
    motivos_qualidade: list[str] = field(default_factory=list)
    # Operações já medidas neste par. Entra no relatório para que a remoção
    # deixe claro quanta estatística está sendo descartada.
    n_trades_medidos: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "volume_24h_usd": round(self.volume_24h_usd, 2),
            "spread_pct": (round(self.spread_pct, 4)
                           if self.spread_pct is not None else None),
            "funding_rate": (round(self.funding_rate, 6)
                             if self.funding_rate is not None else None),
            "qualidade_ok": self.qualidade_ok,
            "motivos_qualidade": self.motivos_qualidade,
            "n_trades_medidos": self.n_trades_medidos,
        }


@dataclass(slots=True)
class Avaliacao:
    symbol: str
    score: float = 0.0              # já descontado pela completude
    score_medido: float = 0.0       # nota entre os componentes que existem
    completude: float = 1.0         # fração do peso que foi de fato medida
    operavel: bool = True
    reprovacoes: list[str] = field(default_factory=list)
    componentes: dict[str, float] = field(default_factory=dict)
    nao_medidos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "score": round(self.score, 2),
            "score_medido": round(self.score_medido, 2),
            "completude": round(self.completude, 3),
            "operavel": self.operavel, "reprovacoes": self.reprovacoes,
            "componentes": {k: round(v, 3)
                            for k, v in self.componentes.items()},
            "nao_medidos": self.nao_medidos,
        }


def _nota_liquidez(volume: float) -> float:
    """0 no piso, 1 em cem vezes o piso, em escala logarítmica.

    Logarítmica porque a diferença entre 20 e 40 milhões importa muito mais
    que entre 2 e 4 bilhões: acima de certo ponto, liquidez extra não muda
    nada para o tamanho de posição deste sistema.
    """
    if volume <= 0:
        return 0.0
    razao = volume / VOLUME_MINIMO_USD
    if razao <= 1.0:
        return 0.0
    return min(1.0, math.log10(razao) / 2.0)


def _nota_spread(spread: float) -> float:
    """1 em spread zero, 0 no teto."""
    if spread <= 0:
        return 1.0
    return max(0.0, 1.0 - spread / SPREAD_MAXIMO_PCT)


def _nota_funding(funding: float) -> float:
    """1 em funding neutro, 0 no limite absoluto."""
    return max(0.0, 1.0 - abs(funding) / FUNDING_MAXIMO_ABS)


def avaliar(leitura: LeituraAtivo) -> Avaliacao:
    """Nota relativa e veredicto absoluto de um ativo.

    Componente não medido não recebe nota neutra nem é simplesmente
    ignorado — as duas saídas erram, em direções opostas:

      dar 0,5 misturaria "medi e está no meio" com "não sei";
      omitir e renormalizar faria ativo SEM DADO ficar com o score máximo,
      porque sobrariam só os componentes bons.

    A segunda versão deste módulo tinha exatamente esse defeito: um ativo sem
    spread e sem funding medidos saía com 100 de score, no topo do ranking.
    A correção é `completude`: a nota é calculada entre os componentes que
    existem e depois multiplicada pela fração do peso que foi medida. Saber
    menos sobre um ativo o faz valer menos, que é o comportamento correto
    para quem vai colocar dinheiro nele.
    """
    a = Avaliacao(symbol=leitura.symbol)

    notas: dict[str, float] = {}
    notas["liquidez"] = _nota_liquidez(leitura.volume_24h_usd)
    if leitura.spread_pct is None:
        a.nao_medidos.append("spread")
    else:
        notas["spread"] = _nota_spread(leitura.spread_pct)
    notas["qualidade"] = 1.0 if leitura.qualidade_ok else 0.0
    if leitura.funding_rate is None:
        a.nao_medidos.append("funding")
    else:
        notas["estabilidade_funding"] = _nota_funding(leitura.funding_rate)

    peso_medido = sum(PESOS[k] for k in notas)
    peso_possivel = sum(PESOS.values())
    a.componentes = notas
    a.completude = peso_medido / peso_possivel if peso_possivel else 0.0
    a.score_medido = (sum(notas[k] * PESOS[k] for k in notas)
                      / peso_medido * 100.0 if peso_medido > 0 else 0.0)
    a.score = a.score_medido * a.completude

    # --------------------------------------------------- pisos absolutos
    if leitura.volume_24h_usd < VOLUME_MINIMO_USD:
        a.reprovacoes.append(
            f"volume de 24h US$ {leitura.volume_24h_usd:,.0f} abaixo do piso "
            f"de US$ {VOLUME_MINIMO_USD:,.0f}: com esse volume o próprio "
            f"sistema move o preço, e o backtest deixa de descrever a "
            f"execução")
    if leitura.spread_pct is not None and leitura.spread_pct > SPREAD_MAXIMO_PCT:
        a.reprovacoes.append(
            f"spread de {leitura.spread_pct:.3f}% acima do teto de "
            f"{SPREAD_MAXIMO_PCT}%: entrar e sair custaria "
            f"{leitura.spread_pct * 2:.3f}%, o que consome a vantagem")
    if not leitura.qualidade_ok:
        motivos = "; ".join(leitura.motivos_qualidade) or "sem detalhe"
        a.reprovacoes.append(
            f"qualidade de dados reprovada ({motivos}): sem série confiável "
            f"não há medição, e sem medição não há decisão")
    if (leitura.funding_rate is not None
            and abs(leitura.funding_rate) > FUNDING_MAXIMO_ABS):
        a.reprovacoes.append(
            f"funding de {leitura.funding_rate:+.4%} por período além do "
            f"limite de {FUNDING_MAXIMO_ABS:.2%}: o carrego consome a "
            f"expectativa mesmo com a direção certa")

    a.operavel = not a.reprovacoes
    return a


@dataclass(slots=True)
class EstadoAtivo:
    """Histerese de um ativo: quantos ciclos seguidos bons ou ruins."""

    symbol: str
    no_universo: bool = True
    ciclos_ruins: int = 0
    ciclos_bons: int = 0
    removido_em_ms: int = 0
    motivo_remocao: str = ""
    ultima_avaliacao: Avaliacao | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "no_universo": self.no_universo,
            "ciclos_ruins": self.ciclos_ruins,
            "ciclos_bons": self.ciclos_bons,
            "removido_em_ms": self.removido_em_ms,
            "motivo_remocao": self.motivo_remocao,
            "avaliacao": (self.ultima_avaliacao.to_dict()
                          if self.ultima_avaliacao else None),
        }


@dataclass(slots=True)
class RelatorioUniverso:
    ranking: list[Avaliacao] = field(default_factory=list)
    ativos: list[str] = field(default_factory=list)
    removidos_agora: list[dict[str, Any]] = field(default_factory=list)
    readmitidos_agora: list[str] = field(default_factory=list)
    em_observacao: list[dict[str, Any]] = field(default_factory=list)
    fora: list[dict[str, Any]] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranking": [a.to_dict() for a in self.ranking],
            "ativos": self.ativos,
            "removidos_agora": self.removidos_agora,
            "readmitidos_agora": self.readmitidos_agora,
            "em_observacao": self.em_observacao,
            "fora": self.fora,
            "avisos": self.avisos,
            "limites": {
                "volume_minimo_usd": VOLUME_MINIMO_USD,
                "spread_maximo_pct": SPREAD_MAXIMO_PCT,
                "funding_maximo_abs": FUNDING_MAXIMO_ABS,
                "ciclos_para_remover": CICLOS_PARA_REMOVER,
                "ciclos_para_readmitir": CICLOS_PARA_READMITIR,
            },
            "observacao": (
                "Remoção exige leituras ruins consecutivas e readmissão exige "
                "mais leituras boas do que isso. A assimetria é proposital: é "
                "mais barato ficar de fora de um ativo bom do que dentro de "
                "um ativo caro."),
        }


class GestorUniverso:
    """Mantém o universo, com histerese na entrada e na saída."""

    def __init__(self, symbols: Sequence[str] = (), *,
                 ciclos_para_remover: int = CICLOS_PARA_REMOVER,
                 ciclos_para_readmitir: int = CICLOS_PARA_READMITIR):
        if ciclos_para_readmitir <= ciclos_para_remover:
            raise ValueError(
                "readmitir tem de exigir mais ciclos do que remover; caso "
                "contrário um ativo no limite do critério oscila para dentro "
                "e para fora a cada leitura, e cada saída descarta a "
                "estatística acumulada daquele par")
        self.ciclos_para_remover = ciclos_para_remover
        self.ciclos_para_readmitir = ciclos_para_readmitir
        self._estado: dict[str, EstadoAtivo] = {
            s.upper(): EstadoAtivo(s.upper()) for s in symbols}

    @property
    def universo(self) -> list[str]:
        return sorted(s for s, e in self._estado.items() if e.no_universo)

    def estado_de(self, symbol: str) -> EstadoAtivo | None:
        return self._estado.get(symbol.upper())

    def atualizar(self, leituras: Sequence[LeituraAtivo], *,
                  agora_ms: int = 0) -> RelatorioUniverso:
        """Um ciclo de avaliação. Move ativos conforme a histerese."""
        rel = RelatorioUniverso()

        for leitura in leituras:
            symbol = leitura.symbol.upper()
            est = self._estado.setdefault(symbol, EstadoAtivo(symbol))
            a = avaliar(leitura)
            est.ultima_avaliacao = a
            rel.ranking.append(a)

            if a.operavel:
                est.ciclos_bons += 1
                est.ciclos_ruins = 0
            else:
                est.ciclos_ruins += 1
                est.ciclos_bons = 0

            if est.no_universo and est.ciclos_ruins >= self.ciclos_para_remover:
                est.no_universo = False
                est.removido_em_ms = agora_ms
                est.motivo_remocao = "; ".join(a.reprovacoes)
                rel.removidos_agora.append({
                    "symbol": symbol,
                    "motivo": est.motivo_remocao,
                    "ciclos_ruins": est.ciclos_ruins,
                    "estatistica_descartada": leitura.n_trades_medidos,
                })
            elif (not est.no_universo
                  and est.ciclos_bons >= self.ciclos_para_readmitir):
                est.no_universo = True
                est.motivo_remocao = ""
                est.removido_em_ms = 0
                rel.readmitidos_agora.append(symbol)
            elif est.no_universo and est.ciclos_ruins > 0:
                rel.em_observacao.append({
                    "symbol": symbol,
                    "ciclos_ruins": est.ciclos_ruins,
                    "faltam": self.ciclos_para_remover - est.ciclos_ruins,
                    "motivo": "; ".join(a.reprovacoes),
                })
            elif not est.no_universo:
                rel.fora.append({
                    "symbol": symbol,
                    "ciclos_bons": est.ciclos_bons,
                    "faltam_para_voltar": (self.ciclos_para_readmitir
                                           - est.ciclos_bons),
                    "motivo_remocao": est.motivo_remocao,
                })

        rel.ranking.sort(key=lambda a: (-a.score, a.symbol))
        rel.ativos = self.universo

        # ------------------------------------------------------- avisos
        if not rel.ativos:
            rel.avisos.append(
                "o universo ficou VAZIO: nenhum ativo passa nos pisos "
                "absolutos. O sistema não vai operar nada, o que é o "
                "comportamento correto — operar um ativo cujo custo consome "
                "a vantagem é pior que não operar")
        for r in rel.removidos_agora:
            extra = (f" Descarta {r['estatistica_descartada']} operações "
                     f"medidas." if r["estatistica_descartada"] else "")
            rel.avisos.append(
                f"{r['symbol']} REMOVIDO do universo após "
                f"{r['ciclos_ruins']} leituras ruins: {r['motivo']}.{extra}")
        for r in rel.readmitidos_agora:
            rel.avisos.append(
                f"{r} readmitido; a estatística anterior dele foi medida em "
                f"outras condições de liquidez e não se transfere")
        inoperaveis = [a for a in rel.ranking if not a.operavel]
        if inoperaveis and len(inoperaveis) == len(rel.ranking):
            rel.avisos.append(
                "TODOS os ativos avaliados estão inoperáveis; o ranking "
                "abaixo ordena candidatos ruins, não escolhas boas")
        sem_medida = [a for a in rel.ranking if a.nao_medidos]
        if sem_medida:
            pior = min(sem_medida, key=lambda a: a.completude)
            rel.avisos.append(
                f"{len(sem_medida)} ativo(s) com componente não medido "
                f"({', '.join(a.symbol for a in sem_medida[:5])}): o score "
                f"deles é descontado pela fração medida — {pior.symbol} tem "
                f"{pior.completude:.0%} do peso medido, então "
                f"{pior.score_medido:.0f} vira {pior.score:.0f}")
        return rel

    def estado(self) -> dict[str, Any]:
        return {
            "universo": self.universo,
            "total_conhecidos": len(self._estado),
            "ativos": {s: e.to_dict() for s, e in sorted(self._estado.items())},
            "ciclos_para_remover": self.ciclos_para_remover,
            "ciclos_para_readmitir": self.ciclos_para_readmitir,
        }


__all__ = [
    "Avaliacao", "CICLOS_PARA_READMITIR", "CICLOS_PARA_REMOVER",
    "EstadoAtivo", "FUNDING_MAXIMO_ABS", "GestorUniverso", "LeituraAtivo",
    "PESOS", "RelatorioUniverso", "SPREAD_MAXIMO_PCT", "VOLUME_MINIMO_USD",
    "avaliar",
]
