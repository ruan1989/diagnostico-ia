"""Análise de ETFs: custo, aderência ao índice e concentração.

O que diferencia um ETF bom de um ruim
--------------------------------------
Não é o retorno — o retorno é do índice, não do gestor. O que o ETF controla
é o **custo** (taxa de administração), a **aderência** (tracking error) e a
**liquidez**. Um ETF que promete seguir o Ibovespa e erra 3% ao ano não está
entregando o produto, independentemente de o Ibovespa ter subido.

E há uma armadilha específica: ETF que parece diversificado porque tem 60
ativos, mas com 45% do peso em 5 papéis. Concentração real se mede pelo peso,
não pela contagem.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..data.quality import FONTE_NAO_CONFIGURADA

PESOS_ETF: dict[str, float] = {
    "custo": 0.30,
    "aderencia": 0.28,
    "liquidez": 0.24,
    "diversificacao_real": 0.18,
}


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


@dataclass(slots=True)
class AnaliseEtf:
    ticker: str
    nome: str = ""
    benchmark: str = ""
    taxa_administracao: float | None = None
    tracking_error: float | None = None
    liquidez_diaria: float | None = None
    n_ativos: int | None = None
    peso_top5: float | None = None
    patrimonio: float | None = None
    score: float = 0.0
    classificacao: str = ""
    hhi_estimado: float | None = None
    fatores: list[dict[str, Any]] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    dados_faltando: list[str] = field(default_factory=list)
    cobertura: float = 0.0
    disponivel: bool = True
    mensagem: str = ""
    fonte: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker, "nome": self.nome,
            "benchmark": self.benchmark,
            "taxa_administracao": self.taxa_administracao,
            "tracking_error": self.tracking_error,
            "liquidez_diaria": self.liquidez_diaria,
            "n_ativos": self.n_ativos, "peso_top5": self.peso_top5,
            "patrimonio": self.patrimonio,
            "hhi_estimado": (round(self.hhi_estimado, 4)
                             if self.hhi_estimado is not None else None),
            "score": round(self.score, 2),
            "classificacao": self.classificacao,
            "cobertura": round(self.cobertura, 3),
            "fatores": self.fatores, "alertas": self.alertas,
            "dados_faltando": self.dados_faltando,
            "disponivel": self.disponivel, "mensagem": self.mensagem,
            "fonte": self.fonte,
        }


def classificar_etf(score: float) -> str:
    if score >= 78:
        return "eficiente"
    if score >= 62:
        return "adequado"
    if score >= 48:
        return "custo ou aderência questionáveis"
    return "ineficiente"


def analisar_etf(ticker: str, dados: dict[str, Any] | None, *,
                 fonte: str = "", cobertura_minima: float = 0.50) -> AnaliseEtf:
    """Avalia um ETF pelo que ele de fato controla."""
    a = AnaliseEtf(ticker=ticker.upper(), fonte=fonte)
    if not dados:
        a.disponivel = False
        a.mensagem = (
            f"{FONTE_NAO_CONFIGURADA}: análise de ETF exige taxa de "
            f"administração, tracking error e composição. Esses dados vêm do "
            f"informe do fundo, não da cotação.")
        a.dados_faltando = ["taxa_administracao", "tracking_error",
                            "liquidez_diaria", "composicao"]
        return a

    a.nome = str(dados.get("nome", ""))
    a.benchmark = str(dados.get("benchmark", ""))
    a.taxa_administracao = dados.get("taxa_administracao")
    a.tracking_error = dados.get("tracking_error")
    a.liquidez_diaria = dados.get("liquidez_diaria")
    a.n_ativos = dados.get("n_ativos")
    a.peso_top5 = dados.get("peso_top5")
    a.patrimonio = dados.get("patrimonio")

    faltando: list[str] = []
    componentes: list[tuple[str, float, float, str]] = []

    # ------------------------------------------------------------- custo
    if a.taxa_administracao is None:
        faltando.append("taxa_administracao")
    else:
        # 0,10% é excelente; acima de 0,80% é caro para um produto indexado.
        v = _clamp((0.45 - a.taxa_administracao) / 0.45)
        componentes.append(("custo", PESOS_ETF["custo"], v,
                            f"taxa de administração {a.taxa_administracao:.2f}% a.a."))
        if a.taxa_administracao > 0.80:
            a.alertas.append(
                f"taxa de {a.taxa_administracao:.2f}% a.a. é alta para um "
                f"produto indexado: ao longo de 10 anos isso consome parte "
                f"relevante do retorno sem entregar gestão ativa")

    # ---------------------------------------------------------- aderência
    if a.tracking_error is None:
        faltando.append("tracking_error")
    else:
        v = _clamp((1.2 - a.tracking_error) / 1.2)
        componentes.append(("aderencia", PESOS_ETF["aderencia"], v,
                            f"tracking error {a.tracking_error:.2f}% a.a. "
                            f"contra {a.benchmark or 'o benchmark'}"))
        if a.tracking_error > 2.0:
            a.alertas.append(
                f"tracking error de {a.tracking_error:.2f}%: o fundo não "
                f"está entregando o índice que promete seguir — o produto "
                f"não cumpre sua função")

    # ----------------------------------------------------------- liquidez
    if a.liquidez_diaria is None:
        faltando.append("liquidez_diaria")
    else:
        import math
        minimo = 2_000_000.0
        razao = a.liquidez_diaria / minimo
        v = _clamp(math.log10(max(razao, 1e-6)) / 1.2)
        componentes.append(("liquidez", PESOS_ETF["liquidez"], v,
                            f"liquidez R$ {a.liquidez_diaria:,.0f}/dia"))
        if a.liquidez_diaria < minimo * 0.25:
            a.alertas.append(
                f"liquidez de R$ {a.liquidez_diaria:,.0f}/dia é baixa: o "
                f"spread de compra e venda pode custar mais que a taxa de "
                f"administração")

    # ------------------------------------------------ diversificação real
    if a.peso_top5 is None:
        faltando.append("peso_top5")
        if a.n_ativos is not None:
            a.alertas.append(
                f"o ETF declara {a.n_ativos} ativos, mas sem o peso dos "
                f"maiores não é possível medir a concentração real — "
                f"contagem de ativos não mede diversificação")
    else:
        v = _clamp((45.0 - a.peso_top5) / 30.0)
        detalhe = f"as 5 maiores posições somam {a.peso_top5:.1f}% da carteira"
        if a.n_ativos:
            detalhe += f" (de {a.n_ativos} ativos)"
            # HHI aproximado: top5 com peso igual + resto distribuído.
            p5 = a.peso_top5 / 100.0
            resto = max(0.0, 1.0 - p5)
            n_resto = max(1, a.n_ativos - 5)
            a.hhi_estimado = (5 * (p5 / 5) ** 2 + n_resto * (resto / n_resto) ** 2)
        componentes.append(("diversificacao_real",
                            PESOS_ETF["diversificacao_real"], v, detalhe))
        if a.peso_top5 > 55.0:
            a.alertas.append(
                f"concentração alta: {a.peso_top5:.0f}% em 5 ativos. Ter "
                f"{a.n_ativos or 'muitos'} papéis na carteira não significa "
                f"diversificação quando o peso está concentrado")

    if not componentes:
        a.disponivel = False
        a.dados_faltando = faltando
        a.mensagem = "nenhum indicador utilizável nos dados fornecidos"
        return a

    peso_disponivel = sum(p for _, p, _, _ in componentes)
    a.cobertura = peso_disponivel / sum(PESOS_ETF.values())
    bruto = sum(p * v for _, p, v, _ in componentes) / peso_disponivel
    a.score = max(0.0, min(100.0, _clamp(bruto) * 50.0 + 50.0))
    a.classificacao = classificar_etf(a.score)
    a.dados_faltando = sorted(set(faltando))
    a.fatores = [{"nome": n, "peso": p, "valor": round(v, 3),
                  "contribuicao": round(p * v, 3), "detalhe": d}
                 for n, p, v, d in componentes]

    if a.cobertura < cobertura_minima:
        a.disponivel = False
        a.mensagem = (
            f"cobertura de {a.cobertura:.0%} dos indicadores (mínimo "
            f"{cobertura_minima:.0%}): faltam {', '.join(a.dados_faltando)}")
        a.alertas.insert(0, a.mensagem)
    return a


def comparar_etfs(entradas: Sequence[tuple[str, dict[str, Any] | None]]
                  ) -> dict[str, Any]:
    analises = [analisar_etf(t, d) for t, d in entradas]
    disponiveis = sorted((a for a in analises if a.disponivel),
                         key=lambda a: a.score, reverse=True)
    return {
        "total": len(analises),
        "disponiveis": len(disponiveis),
        "etfs": [a.to_dict() for a in disponiveis]
                + [a.to_dict() for a in analises if not a.disponivel],
        "observacao": "o retorno de um ETF é do índice; o que o gestor "
                      "controla é custo, aderência e liquidez — é isso que "
                      "este score mede",
    }
