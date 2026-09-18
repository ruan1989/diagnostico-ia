"""Teste de estresse do portfólio.

Para que serve
--------------
A gestão de risco cuida de cada operação isolada. O stress test responde a
outra pergunta: *se o cenário ruim acontecer com TODAS as posições ao mesmo
tempo, quanto sobra?*

A diferença é o que quebra contas. Cinco posições com 0,5% de risco cada
parecem arriscar 2,5%. Mas se os cinco stops forem executados no mesmo evento,
com gap e correlação indo a 1, a perda real passa disso — e é isso que o
cenário `CORRELACAO_TOTAL` mede.

Cada cenário é declarado com premissas explícitas. Nenhum número aqui é
previsão; são hipóteses de trabalho para dimensionar tolerância.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..models import Position, Side
from ..risk.manager import grupo_de


@dataclass(slots=True)
class Cenario:
    """Um choque de mercado descrito em parâmetros."""

    nome: str
    descricao: str
    # Choque por fator de risco, em % (negativo = queda).
    choque_por_grupo: dict[str, float] = field(default_factory=dict)
    # Choque aplicado a tudo o que não tiver grupo específico.
    choque_default_pct: float = 0.0
    # Multiplicador de spread e slippage na saída.
    multiplicador_spread: float = 1.0
    # Queda de liquidez, que piora a execução dos stops.
    reducao_liquidez_pct: float = 0.0
    # Se True, assume correlação 1 entre todas as posições.
    correlacao_total: bool = False
    # Gap na abertura: fração do choque que ocorre sem chance de stop.
    fracao_em_gap: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "nome": self.nome, "descricao": self.descricao,
            "choque_por_grupo": self.choque_por_grupo,
            "choque_default_pct": self.choque_default_pct,
            "multiplicador_spread": self.multiplicador_spread,
            "reducao_liquidez_pct": self.reducao_liquidez_pct,
            "correlacao_total": self.correlacao_total,
            "fracao_em_gap": self.fracao_em_gap,
        }


# Cenários pedidos na especificação, mais os que faltavam para cobrir o
# caminho pelo qual contas alavancadas efetivamente quebram.
CENARIOS_PADRAO: tuple[Cenario, ...] = (
    Cenario("btc_-10", "Bitcoin cai 10%; altcoins acompanham amplificado",
            choque_por_grupo={"majors": -10.0, "l1_alt": -14.0, "l2": -15.0,
                              "meme": -18.0, "defi": -15.0, "oraculo": -13.0,
                              "pagamentos": -11.0, "exchange": -9.0},
            choque_default_pct=-12.0, multiplicador_spread=1.8,
            fracao_em_gap=0.15),
    Cenario("btc_-20", "Bitcoin cai 20% em poucas horas",
            choque_por_grupo={"majors": -20.0, "l1_alt": -28.0, "l2": -30.0,
                              "meme": -36.0, "defi": -30.0, "oraculo": -26.0,
                              "pagamentos": -22.0, "exchange": -18.0},
            choque_default_pct=-24.0, multiplicador_spread=3.0,
            reducao_liquidez_pct=50.0, fracao_em_gap=0.30),
    Cenario("btc_-30", "Colapso: Bitcoin cai 30%, liquidez evapora",
            choque_por_grupo={"majors": -30.0, "l1_alt": -42.0, "l2": -45.0,
                              "meme": -55.0, "defi": -45.0, "oraculo": -40.0,
                              "pagamentos": -33.0, "exchange": -28.0},
            choque_default_pct=-36.0, multiplicador_spread=5.0,
            reducao_liquidez_pct=70.0, correlacao_total=True,
            fracao_em_gap=0.45),
    Cenario("ibov_-10", "Ibovespa cai 10%",
            choque_por_grupo={"acao": -10.0, "fii": -6.0, "etf": -9.0},
            choque_default_pct=-4.0, multiplicador_spread=1.5),
    Cenario("ibov_-20", "Ibovespa cai 20%",
            choque_por_grupo={"acao": -20.0, "fii": -13.0, "etf": -18.0},
            choque_default_pct=-8.0, multiplicador_spread=2.5,
            reducao_liquidez_pct=40.0, fracao_em_gap=0.25),
    Cenario("dolar_+15", "Dólar sobe 15% contra o real",
            choque_por_grupo={"acao": -8.0, "fii": -10.0, "majors": 3.0},
            choque_default_pct=-2.0, multiplicador_spread=1.6),
    Cenario("juros_+300bps", "Juros sobem 300 pontos-base",
            choque_por_grupo={"fii": -18.0, "acao": -12.0, "majors": -15.0,
                              "l1_alt": -22.0, "renda_fixa": -9.0},
            choque_default_pct=-14.0, multiplicador_spread=2.0),
    Cenario("liquidez_-70", "Liquidez cai 70% e o spread quintuplica",
            choque_default_pct=-5.0, multiplicador_spread=5.0,
            reducao_liquidez_pct=70.0, fracao_em_gap=0.20),
    Cenario("spread_x5", "Spread quintuplica sem choque de preço",
            choque_default_pct=0.0, multiplicador_spread=5.0),
    Cenario("correlacao_1", "Correlação entre todos os ativos vai a 1",
            choque_por_grupo={}, choque_default_pct=-15.0,
            multiplicador_spread=2.5, correlacao_total=True,
            fracao_em_gap=0.20),
    Cenario("gap_de_abertura", "Gap de 8% contra a posição, sem stop possível",
            choque_default_pct=-8.0, multiplicador_spread=2.0,
            fracao_em_gap=1.0),
)


@dataclass(slots=True)
class ResultadoPosicao:
    symbol: str
    side: str
    notional_usd: float
    grupo: str
    choque_pct: float
    # Perda se o stop funcionar normalmente.
    perda_com_stop_usd: float
    # Perda considerando gap e spread ampliado.
    perda_realista_usd: float
    stop_furado: bool
    liquidada: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "side": self.side,
            "notional_usd": round(self.notional_usd, 2), "grupo": self.grupo,
            "choque_pct": round(self.choque_pct, 2),
            "perda_com_stop_usd": round(self.perda_com_stop_usd, 2),
            "perda_realista_usd": round(self.perda_realista_usd, 2),
            "stop_furado": self.stop_furado, "liquidada": self.liquidada,
        }


@dataclass(slots=True)
class ResultadoCenario:
    cenario: Cenario
    capital_antes: float
    perda_planejada_usd: float
    perda_realista_usd: float
    capital_depois: float
    drawdown_pct: float
    posicoes: list[ResultadoPosicao] = field(default_factory=list)
    stops_furados: int = 0
    posicoes_liquidadas: int = 0
    sobrevive: bool = True
    avisos: list[str] = field(default_factory=list)

    @property
    def amplificacao(self) -> float:
        """Quantas vezes a perda real supera a perda planejada pelos stops."""
        if self.perda_planejada_usd <= 0:
            return 0.0
        return self.perda_realista_usd / self.perda_planejada_usd

    def to_dict(self) -> dict[str, Any]:
        return {
            "cenario": self.cenario.to_dict(),
            "capital_antes": round(self.capital_antes, 2),
            "perda_planejada_usd": round(self.perda_planejada_usd, 2),
            "perda_realista_usd": round(self.perda_realista_usd, 2),
            "amplificacao": round(self.amplificacao, 2),
            "capital_depois": round(self.capital_depois, 2),
            "drawdown_pct": round(self.drawdown_pct, 2),
            "stops_furados": self.stops_furados,
            "posicoes_liquidadas": self.posicoes_liquidadas,
            "sobrevive": self.sobrevive,
            "posicoes": [p.to_dict() for p in self.posicoes],
            "avisos": self.avisos,
        }


def aplicar_cenario(posicoes: Sequence[Position], capital: float,
                    cenario: Cenario, *,
                    slippage_base_pct: float = 0.03,
                    limite_drawdown_pct: float = 25.0) -> ResultadoCenario:
    """Aplica o choque à carteira e mede o dano.

    Calcula DUAS perdas:

    * **planejada** — o que os stops deveriam limitar;
    * **realista** — considerando que parte do movimento acontece em gap
      (sem chance de stop) e que o spread ampliado piora cada saída.

    A diferença entre as duas é o risco que o dimensionamento por stop não
    captura, e é ela que surpreende operadores em evento de mercado.
    """
    resultado = ResultadoCenario(
        cenario=cenario, capital_antes=capital, perda_planejada_usd=0.0,
        perda_realista_usd=0.0, capital_depois=capital, drawdown_pct=0.0)

    for p in posicoes:
        grupo = grupo_de(p.symbol)
        choque = cenario.choque_por_grupo.get(grupo,
                                              cenario.choque_default_pct)
        # Posição short ganha quando o mercado cai.
        direcao = 1.0 if p.side is Side.LONG else -1.0
        movimento_pct = choque * direcao

        notional = abs(p.notional_usd)
        dist_stop_pct = (abs(p.entry - p.stop_loss) / p.entry * 100.0
                         if p.entry > 0 else 0.0)

        # ------------------------------------------------- perda planejada
        if movimento_pct >= 0:
            planejada = 0.0     # o cenário é favorável a esta posição
        else:
            planejada = notional * min(abs(movimento_pct),
                                       dist_stop_pct) / 100.0

        # -------------------------------------------------- perda realista
        gap_pct = abs(movimento_pct) * cenario.fracao_em_gap
        stop_furado = gap_pct > dist_stop_pct > 0
        if movimento_pct >= 0:
            realista = 0.0
        elif stop_furado:
            # O stop não protege: a perda é o gap inteiro, mais o custo de
            # sair num mercado sem liquidez.
            realista = notional * gap_pct / 100.0
        else:
            realista = planejada

        custo_saida = (notional * slippage_base_pct
                       * cenario.multiplicador_spread / 100.0)
        if cenario.reducao_liquidez_pct > 0:
            custo_saida *= 1.0 + cenario.reducao_liquidez_pct / 100.0
        realista += custo_saida

        # ------------------------------------------------------ liquidação
        alavancagem = notional / capital if capital > 0 else 0.0
        margem = notional / max(alavancagem, 1.0)
        liquidada = realista >= margem * 0.95 and alavancagem > 1.0

        resultado.posicoes.append(ResultadoPosicao(
            symbol=p.symbol, side=p.side.value, notional_usd=notional,
            grupo=grupo, choque_pct=movimento_pct,
            perda_com_stop_usd=planejada, perda_realista_usd=realista,
            stop_furado=stop_furado, liquidada=liquidada))
        resultado.perda_planejada_usd += planejada
        resultado.perda_realista_usd += realista
        resultado.stops_furados += int(stop_furado)
        resultado.posicoes_liquidadas += int(liquidada)

    resultado.capital_depois = capital - resultado.perda_realista_usd
    resultado.drawdown_pct = (resultado.perda_realista_usd / capital * 100.0
                              if capital > 0 else 0.0)
    resultado.sobrevive = (resultado.capital_depois > 0
                           and resultado.drawdown_pct < limite_drawdown_pct)

    # ------------------------------------------------------------ avisos
    if resultado.stops_furados:
        resultado.avisos.append(
            f"{resultado.stops_furados} stop(s) furado(s) por gap: o preço "
            f"salta além do stop e a saída acontece bem depois dele")
    if resultado.amplificacao > 1.5 and resultado.perda_planejada_usd > 0:
        resultado.avisos.append(
            f"a perda real é {resultado.amplificacao:.1f}x a perda planejada "
            f"pelos stops (US$ {resultado.perda_realista_usd:.2f} contra "
            f"US$ {resultado.perda_planejada_usd:.2f})")
    if resultado.posicoes_liquidadas:
        resultado.avisos.append(
            f"{resultado.posicoes_liquidadas} posição(ões) seria(m) "
            f"liquidada(s) neste cenário")
    if resultado.drawdown_pct >= 100.0:
        resultado.avisos.append(
            f"RUÍNA: a perda de US$ {resultado.perda_realista_usd:.2f} "
            f"excede o capital de US$ {capital:.2f} em "
            f"{resultado.drawdown_pct - 100:.0f} pontos percentuais. Na "
            f"prática isso é liquidação total, possivelmente com saldo "
            f"negativo — o drawdown acima de 100% mede o quanto o cenário "
            f"passa da ruína, não uma perda que se possa absorver.")
    elif not resultado.sobrevive:
        resultado.avisos.append(
            f"CENÁRIO NÃO SOBREVIVÍVEL: drawdown de "
            f"{resultado.drawdown_pct:.1f}% excede o limite de "
            f"{limite_drawdown_pct:.0f}%")
    if cenario.correlacao_total:
        resultado.avisos.append(
            "este cenário assume correlação 1: a diversificação da carteira "
            "não oferece proteção nenhuma")
    return resultado


@dataclass(slots=True)
class RelatorioStress:
    capital: float
    n_posicoes: int
    exposicao_total_usd: float
    resultados: list[ResultadoCenario] = field(default_factory=list)
    pior_cenario: str = ""
    pior_drawdown_pct: float = 0.0
    cenarios_nao_sobreviveis: list[str] = field(default_factory=list)
    cenarios_de_ruina: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capital": round(self.capital, 2),
            "n_posicoes": self.n_posicoes,
            "exposicao_total_usd": round(self.exposicao_total_usd, 2),
            "alavancagem_efetiva": round(
                self.exposicao_total_usd / self.capital, 2)
            if self.capital > 0 else 0.0,
            "pior_cenario": self.pior_cenario,
            "pior_drawdown_pct": round(self.pior_drawdown_pct, 2),
            "cenarios_nao_sobreviveis": self.cenarios_nao_sobreviveis,
            "cenarios_de_ruina": self.cenarios_de_ruina,
            "resultados": [r.to_dict() for r in self.resultados],
            "observacao": "cenários são hipóteses de trabalho para dimensionar "
                          "tolerância, não previsões",
        }


def rodar_stress_test(posicoes: Sequence[Position], capital: float, *,
                      cenarios: Sequence[Cenario] | None = None,
                      slippage_base_pct: float = 0.03,
                      limite_drawdown_pct: float = 25.0) -> RelatorioStress:
    """Roda todos os cenários e resume o pior caso."""
    if capital <= 0:
        raise ValueError("capital deve ser > 0")
    cenarios = cenarios or CENARIOS_PADRAO

    rel = RelatorioStress(
        capital=capital, n_posicoes=len(posicoes),
        exposicao_total_usd=sum(abs(p.notional_usd) for p in posicoes))

    for cenario in cenarios:
        r = aplicar_cenario(posicoes, capital, cenario,
                            slippage_base_pct=slippage_base_pct,
                            limite_drawdown_pct=limite_drawdown_pct)
        rel.resultados.append(r)
        if r.drawdown_pct > rel.pior_drawdown_pct:
            rel.pior_drawdown_pct = r.drawdown_pct
            rel.pior_cenario = cenario.nome
        if not r.sobrevive:
            rel.cenarios_nao_sobreviveis.append(cenario.nome)
        if r.drawdown_pct >= 100.0:
            rel.cenarios_de_ruina.append(cenario.nome)

    return rel
