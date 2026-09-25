"""Análise fundamentalista de ações.

Premissa deste módulo
---------------------
Preço isolado não decide investimento de médio prazo. O que decide é se a
empresa gera caixa, a que preço isso está sendo vendido e quanto de dívida
existe no caminho. Por isso a análise combina três blocos independentes —
qualidade, endividamento e valuation — e **nenhum campo é inventado**: se o
dado não veio, ele aparece em `dados_faltando` e o peso é redistribuído.

Não há análise de ação neste sistema sem fonte de fundamentos configurada. O
resultado nesse caso é `FONTE NÃO CONFIGURADA`, não um score baseado só em
gráfico.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..data.quality import FONTE_NAO_CONFIGURADA
from ..models import FactorScore

# Pesos dos blocos. Qualidade pesa mais que valuation de propósito: empresa
# ruim barata continua ruim, e o desconto costuma ser merecido.
PESOS_ACAO: dict[str, float] = {
    "rentabilidade": 0.24,      # ROE, ROIC
    "margens": 0.14,
    "crescimento": 0.16,
    "endividamento": 0.20,
    "geracao_de_caixa": 0.14,
    "valuation": 0.12,
}

CAMPOS = (
    "roe", "roic", "margem_liquida", "margem_ebitda", "crescimento_receita",
    "crescimento_lucro", "divida_liquida_ebitda", "divida_liquida_patrimonio",
    "fluxo_caixa_livre", "p_l", "p_vp", "ev_ebitda", "dividend_yield",
    "payout",
)


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


@dataclass(slots=True)
class AnaliseAcao:
    ticker: str
    nome: str = ""
    setor: str = ""
    preco: float | None = None
    score: float = 0.0
    classificacao: str = ""
    fatores: list[FactorScore] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    dados_faltando: list[str] = field(default_factory=list)
    cobertura: float = 0.0
    fonte: str = ""
    disponivel: bool = True
    mensagem: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker, "nome": self.nome, "setor": self.setor,
            "preco": self.preco, "score": round(self.score, 2),
            "classificacao": self.classificacao,
            "cobertura": round(self.cobertura, 3),
            "disponivel": self.disponivel, "mensagem": self.mensagem,
            "alertas": self.alertas, "dados_faltando": self.dados_faltando,
            "fonte": self.fonte,
            "fatores": [
                {"nome": f.nome, "peso": f.peso, "valor": round(f.valor, 3),
                 "contribuicao": round(f.contribuicao, 3),
                 "detalhe": f.detalhe}
                for f in self.fatores
            ],
        }


def _fator_rentabilidade(d: dict[str, Any],
                         faltando: list[str]) -> FactorScore | None:
    roe, roic = d.get("roe"), d.get("roic")
    if roe is None and roic is None:
        faltando.extend(["roe", "roic"])
        return None
    partes: list[tuple[float, str]] = []
    if roe is not None:
        # 15% é a referência de custo de capital próprio no Brasil.
        partes.append((_clamp((roe - 15.0) / 15.0), f"ROE {roe:.1f}%"))
    else:
        faltando.append("roe")
    if roic is not None:
        partes.append((_clamp((roic - 12.0) / 12.0), f"ROIC {roic:.1f}%"))
    else:
        faltando.append("roic")
    valor = sum(v for v, _ in partes) / len(partes)
    return FactorScore("rentabilidade", PESOS_ACAO["rentabilidade"], valor,
                       " | ".join(t for _, t in partes))


def _fator_margens(d: dict[str, Any],
                   faltando: list[str]) -> FactorScore | None:
    ml, me = d.get("margem_liquida"), d.get("margem_ebitda")
    if ml is None and me is None:
        faltando.extend(["margem_liquida", "margem_ebitda"])
        return None
    partes: list[tuple[float, str]] = []
    if ml is not None:
        partes.append((_clamp((ml - 8.0) / 10.0),
                       f"margem líquida {ml:.1f}%"))
    if me is not None:
        partes.append((_clamp((me - 15.0) / 15.0),
                       f"margem EBITDA {me:.1f}%"))
    valor = sum(v for v, _ in partes) / len(partes)
    return FactorScore("margens", PESOS_ACAO["margens"], valor,
                       " | ".join(t for _, t in partes))


def _fator_crescimento(d: dict[str, Any],
                       faltando: list[str]) -> FactorScore | None:
    cr, cl = d.get("crescimento_receita"), d.get("crescimento_lucro")
    if cr is None and cl is None:
        faltando.extend(["crescimento_receita", "crescimento_lucro"])
        return None
    partes: list[tuple[float, str]] = []
    if cr is not None:
        partes.append((_clamp(cr / 15.0), f"receita {cr:+.1f}%"))
    if cl is not None:
        partes.append((_clamp(cl / 20.0), f"lucro {cl:+.1f}%"))
    valor = sum(v for v, _ in partes) / len(partes)
    detalhe = " | ".join(t for _, t in partes)
    # Lucro crescendo muito mais que receita costuma ser corte de custo ou
    # evento não recorrente — não é crescimento sustentável.
    if cr is not None and cl is not None and cl > cr * 3 and cr < 5:
        valor = _clamp(valor - 0.3)
        detalhe += " (lucro cresce muito acima da receita: verificar se é "
        detalhe += "recorrente)"
    return FactorScore("crescimento", PESOS_ACAO["crescimento"], valor,
                       detalhe)


def _fator_endividamento(d: dict[str, Any],
                         faltando: list[str]) -> FactorScore | None:
    de, dp = (d.get("divida_liquida_ebitda"),
              d.get("divida_liquida_patrimonio"))
    if de is None and dp is None:
        faltando.extend(["divida_liquida_ebitda",
                         "divida_liquida_patrimonio"])
        return None
    partes: list[tuple[float, str]] = []
    if de is not None:
        # Caixa líquido (negativo) é ótimo; acima de 3x é alavancagem alta.
        partes.append((_clamp((2.0 - de) / 2.0),
                       f"dívida líq./EBITDA {de:.2f}x"))
    if dp is not None:
        partes.append((_clamp((0.6 - dp) / 0.6),
                       f"dívida líq./PL {dp:.2f}x"))
    valor = sum(v for v, _ in partes) / len(partes)
    return FactorScore("endividamento", PESOS_ACAO["endividamento"], valor,
                       " | ".join(t for _, t in partes))


def _fator_caixa(d: dict[str, Any],
                 faltando: list[str]) -> FactorScore | None:
    fcl = d.get("fluxo_caixa_livre")
    if fcl is None:
        faltando.append("fluxo_caixa_livre")
        return None
    lucro = d.get("lucro_liquido")
    if lucro and lucro > 0:
        conversao = fcl / lucro
        valor = _clamp((conversao - 0.5) / 0.5)
        detalhe = (f"fluxo de caixa livre converte {conversao:.0%} do lucro "
                   f"contábil")
    else:
        valor = 0.7 if fcl > 0 else -0.9
        detalhe = ("fluxo de caixa livre positivo" if fcl > 0
                   else "fluxo de caixa livre NEGATIVO")
    return FactorScore("geracao_de_caixa", PESOS_ACAO["geracao_de_caixa"],
                       valor, detalhe)


def _fator_valuation(d: dict[str, Any],
                     faltando: list[str]) -> FactorScore | None:
    pl, pvp, ev = d.get("p_l"), d.get("p_vp"), d.get("ev_ebitda")
    if pl is None and pvp is None and ev is None:
        faltando.extend(["p_l", "p_vp", "ev_ebitda"])
        return None
    partes: list[tuple[float, str]] = []
    if pl is not None and pl > 0:
        partes.append((_clamp((14.0 - pl) / 10.0), f"P/L {pl:.1f}"))
    if pvp is not None and pvp > 0:
        partes.append((_clamp((2.0 - pvp) / 1.5), f"P/VP {pvp:.2f}"))
    if ev is not None and ev > 0:
        partes.append((_clamp((8.0 - ev) / 6.0), f"EV/EBITDA {ev:.1f}"))
    if not partes:
        faltando.append("valuation_valido")
        return None
    valor = sum(v for v, _ in partes) / len(partes)
    return FactorScore("valuation", PESOS_ACAO["valuation"], valor,
                       " | ".join(t for _, t in partes))


def _alertas(d: dict[str, Any]) -> list[str]:
    out: list[str] = []
    de = d.get("divida_liquida_ebitda")
    if de is not None and de > 3.5:
        out.append(f"dívida líquida de {de:.1f}x EBITDA: sensível a alta de "
                   f"juros e a queda de resultado")
    fcl = d.get("fluxo_caixa_livre")
    if fcl is not None and fcl < 0:
        out.append("queima de caixa: lucro contábil sem geração de caixa não "
                   "paga dividendo nem reduz dívida")
    payout = d.get("payout")
    if payout is not None and payout > 100:
        out.append(f"payout de {payout:.0f}%: distribui mais que o lucro, o "
                   f"que não é sustentável")
    ml = d.get("margem_liquida")
    if ml is not None and ml < 0:
        out.append("prejuízo no período")
    pvp = d.get("p_vp")
    if pvp is not None and pvp > 4:
        out.append(f"P/VP de {pvp:.1f}: preço embute expectativa alta de "
                   f"crescimento")
    cr = d.get("crescimento_receita")
    if cr is not None and cr < -10:
        out.append(f"receita caindo {abs(cr):.0f}%: verificar se é ciclo ou "
                   f"perda estrutural de mercado")
    return out


def classificar(score: float) -> str:
    if score >= 75:
        return "fundamentos fortes"
    if score >= 62:
        return "bons fundamentos, com ressalvas"
    if score >= 50:
        return "neutro"
    if score >= 38:
        return "fundamentos fracos"
    return "evitar"


def analisar_acao(ticker: str, fundamentos: dict[str, Any] | None, *,
                  nome: str = "", setor: str = "",
                  preco: float | None = None,
                  fonte: str = "",
                  cobertura_minima: float = 0.50) -> AnaliseAcao:
    """Avalia uma ação. Sem fundamentos, devolve indisponível — não um score."""
    if not fundamentos:
        return AnaliseAcao(
            ticker=ticker.upper(), nome=nome, setor=setor, preco=preco,
            disponivel=False, fonte=fonte,
            mensagem=f"{FONTE_NAO_CONFIGURADA}: análise de ação exige dados "
                     f"fundamentalistas (receita, margens, dívida, fluxo de "
                     f"caixa). Não é possível analisar {ticker.upper()} "
                     f"apenas com gráfico.",
            dados_faltando=list(CAMPOS))

    faltando: list[str] = []
    candidatos = [
        _fator_rentabilidade(fundamentos, faltando),
        _fator_margens(fundamentos, faltando),
        _fator_crescimento(fundamentos, faltando),
        _fator_endividamento(fundamentos, faltando),
        _fator_caixa(fundamentos, faltando),
        _fator_valuation(fundamentos, faltando),
    ]
    fatores = [f for f in candidatos if f is not None]

    if not fatores:
        return AnaliseAcao(
            ticker=ticker.upper(), nome=nome, setor=setor, preco=preco,
            disponivel=False, fonte=fonte, dados_faltando=faltando,
            mensagem="nenhum indicador fundamentalista utilizável nos dados "
                     "fornecidos")

    peso_disponivel = sum(f.peso for f in fatores)
    cobertura = peso_disponivel / sum(PESOS_ACAO.values())
    # Renormaliza pelos fatores presentes: ausência de dado não deve virar
    # nota baixa, e sim cobertura menor, declarada.
    bruto = sum(f.contribuicao for f in fatores) / peso_disponivel
    score = _clamp(bruto) * 50.0 + 50.0

    analise = AnaliseAcao(
        ticker=ticker.upper(),
        nome=nome or str(fundamentos.get("nome", "")),
        setor=setor or str(fundamentos.get("setor", "")),
        preco=preco if preco is not None else fundamentos.get("preco"),
        score=score, classificacao=classificar(score), fatores=fatores,
        alertas=_alertas(fundamentos), dados_faltando=sorted(set(faltando)),
        cobertura=cobertura, fonte=fonte, disponivel=True)

    if cobertura < cobertura_minima:
        analise.disponivel = False
        analise.mensagem = (
            f"cobertura de apenas {cobertura:.0%} dos indicadores "
            f"(mínimo {cobertura_minima:.0%}): score calculado sobre poucos "
            f"fatores não é comparável. Faltam: "
            f"{', '.join(analise.dados_faltando[:6])}")
        analise.alertas.insert(0, analise.mensagem)
    return analise
