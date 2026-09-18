"""Comparação entre classes de ativos.

O erro que este módulo existe para impedir
------------------------------------------
"O CDB paga 11% e o FII paga 9% de dividend yield, então o CDB é melhor." A
frase compara dois números que não são comparáveis:

* o CDB é nominal e tributado; o FII distribui isento mas tem risco de preço;
* o CDB não oscila (se mantido ao vencimento); o FII pode cair 20% e ainda
  distribuir;
* o CDB tem prazo definido; o FII é perpétuo;
* o cripto pode multiplicar e pode ir a zero — a média esconde as duas coisas.

Comparar exige converter tudo para a mesma régua: **retorno real esperado,
volatilidade, drawdown plausível, liquidez, tributação e horizonte**. E,
ainda assim, a saída correta muitas vezes é "depende do objetivo" — que este
módulo diz explicitamente em vez de forçar um vencedor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..data.quality import FONTE_NAO_CONFIGURADA
from ..data.symbols import AssetClass


@dataclass(slots=True)
class CandidatoComparacao:
    """Um investimento traduzido para a régua comum."""

    identificador: str
    classe: AssetClass
    nome: str = ""
    # Retorno nominal esperado ao ano, em %. Para ativo de risco é uma
    # estimativa com incerteza alta, e é isso que `incerteza_retorno` diz.
    retorno_nominal_aa: float | None = None
    # Amplitude plausível do retorno (desvio-padrão anualizado), em p.p.
    incerteza_retorno: float | None = None
    volatilidade_aa: float | None = None
    drawdown_plausivel_pct: float | None = None
    aliquota_ir: float = 0.0
    isento_ir: bool = False
    liquidez_dias: int | None = None       # dias para converter em caixa
    horizonte_minimo_meses: int | None = None
    risco_credito: int | None = None       # 1..5
    garantia: str = ""                     # ex.: "FGC", "soberano", "nenhuma"
    score_qualidade: float | None = None   # score do analisador da classe
    observacoes: list[str] = field(default_factory=list)
    dados_faltando: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identificador": self.identificador, "classe": self.classe.value,
            "nome": self.nome,
            "retorno_nominal_aa": self.retorno_nominal_aa,
            "incerteza_retorno": self.incerteza_retorno,
            "volatilidade_aa": self.volatilidade_aa,
            "drawdown_plausivel_pct": self.drawdown_plausivel_pct,
            "aliquota_ir": self.aliquota_ir, "isento_ir": self.isento_ir,
            "liquidez_dias": self.liquidez_dias,
            "horizonte_minimo_meses": self.horizonte_minimo_meses,
            "risco_credito": self.risco_credito, "garantia": self.garantia,
            "score_qualidade": self.score_qualidade,
            "observacoes": self.observacoes,
            "dados_faltando": self.dados_faltando,
        }


@dataclass(slots=True)
class LinhaComparacao:
    candidato: CandidatoComparacao
    retorno_liquido_aa: float | None = None
    retorno_real_liquido_aa: float | None = None
    # Retorno real por unidade de risco. É o número que permite comparar
    # classes diferentes — mas só quando há estimativa de volatilidade.
    retorno_por_risco: float | None = None
    # Risco total usado no denominador, já somando o risco de crédito.
    # Volatilidade sozinha é o denominador errado para instrumento de
    # crédito: um CDB mantido ao vencimento oscila quase nada e pode não
    # pagar nada. Usar só volatilidade colocaria um emissor frágil no topo
    # do ranking por "baixo risco".
    risco_total_aa: float | None = None
    # Piso plausível do retorno: estimativa central menos a incerteza.
    retorno_pessimista_aa: float | None = None
    comparavel: bool = True
    motivo_incomparavel: str = ""

    def to_dict(self) -> dict[str, Any]:
        def _r(v: float | None) -> float | None:
            return round(v, 3) if v is not None else None
        return {
            "candidato": self.candidato.to_dict(),
            "retorno_liquido_aa": _r(self.retorno_liquido_aa),
            "retorno_real_liquido_aa": _r(self.retorno_real_liquido_aa),
            "retorno_por_risco": _r(self.retorno_por_risco),
            "risco_total_aa": _r(self.risco_total_aa),
            "retorno_pessimista_aa": _r(self.retorno_pessimista_aa),
            "comparavel": self.comparavel,
            "motivo_incomparavel": self.motivo_incomparavel,
        }


@dataclass(slots=True)
class PerfilInvestidor:
    """Objetivo e restrições. Sem isso não existe "melhor investimento"."""

    horizonte_meses: int = 36
    tolerancia_drawdown_pct: float = 15.0
    necessidade_liquidez_dias: int = 30
    objetivo: str = "crescimento"   # crescimento | renda | preservacao

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizonte_meses": self.horizonte_meses,
            "tolerancia_drawdown_pct": self.tolerancia_drawdown_pct,
            "necessidade_liquidez_dias": self.necessidade_liquidez_dias,
            "objetivo": self.objetivo,
        }


@dataclass(slots=True)
class RelatorioComparacao:
    inflacao_aa: float | None
    perfil: PerfilInvestidor
    linhas: list[LinhaComparacao] = field(default_factory=list)
    incompativeis: list[dict[str, str]] = field(default_factory=list)
    conclusao: str = ""
    avisos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inflacao_aa": self.inflacao_aa,
            "perfil": self.perfil.to_dict(),
            "linhas": [l.to_dict() for l in self.linhas],
            "incompativeis": self.incompativeis,
            "conclusao": self.conclusao,
            "avisos": self.avisos,
            "observacao": "comparação por retorno REAL LÍQUIDO ajustado a "
                          "risco, liquidez e horizonte — nunca por "
                          "rentabilidade nominal",
        }


# Perda esperada anualizada, em pontos percentuais, associada a cada nota de
# risco de crédito. São ordens de grandeza para tornar o risco comparável à
# volatilidade, não estimativas de probabilidade de default de um emissor
# específico. Nota 1 (soberano em moeda local) é tratada como ~0.
RISCO_CREDITO_EM_PP: dict[int, float] = {
    1: 0.2, 2: 1.0, 3: 2.5, 4: 6.0, 5: 12.0,
}


def _risco_credito_em_pontos(c: CandidatoComparacao) -> float:
    """Converte a nota de crédito em pontos percentuais de risco anual.

    Garantia do FGC ou soberana reduz o valor, mas não o zera: o FGC paga
    depois de semanas a meses e tem teto por CPF.
    """
    if c.risco_credito is None:
        return 0.0
    base = RISCO_CREDITO_EM_PP.get(int(c.risco_credito), 3.0)
    garantia = (c.garantia or "").strip().lower()
    if garantia == "soberano":
        return min(base, 0.3)
    if garantia == "fgc":
        return base * 0.45
    return base


def comparar(candidatos: Sequence[CandidatoComparacao], *,
             inflacao_aa: float | None,
             perfil: PerfilInvestidor | None = None
             ) -> RelatorioComparacao:
    """Coloca os candidatos na mesma régua e confronta com o perfil."""
    perfil = perfil or PerfilInvestidor()
    rel = RelatorioComparacao(inflacao_aa=inflacao_aa, perfil=perfil)

    if inflacao_aa is None:
        rel.avisos.append(
            f"{FONTE_NAO_CONFIGURADA}: sem a inflação corrente não é possível "
            f"calcular retorno real. A comparação abaixo fica em termos "
            f"nominais, o que favorece artificialmente o que tem retorno "
            f"nominal maior.")

    for c in candidatos:
        linha = LinhaComparacao(candidato=c)

        if c.retorno_nominal_aa is None:
            linha.comparavel = False
            linha.motivo_incomparavel = (
                f"sem estimativa de retorno para {c.identificador}: "
                f"{FONTE_NAO_CONFIGURADA}")
            rel.linhas.append(linha)
            continue

        aliq = 0.0 if c.isento_ir else c.aliquota_ir
        linha.retorno_liquido_aa = c.retorno_nominal_aa * (1 - aliq)

        if inflacao_aa is not None:
            linha.retorno_real_liquido_aa = (
                (1 + linha.retorno_liquido_aa / 100.0)
                / (1 + inflacao_aa / 100.0) - 1) * 100.0

        base = (linha.retorno_real_liquido_aa
                if linha.retorno_real_liquido_aa is not None
                else linha.retorno_liquido_aa)

        # O denominador do retorno ajustado soma DUAS fontes de risco:
        # oscilação de preço (volatilidade) e possibilidade de não receber
        # (crédito). Somar em quadratura trata as duas como independentes,
        # que é a hipótese razoável.
        risco_credito_aa = _risco_credito_em_pontos(c)
        if c.volatilidade_aa is not None:
            linha.risco_total_aa = (
                (c.volatilidade_aa ** 2 + risco_credito_aa ** 2) ** 0.5)
        elif risco_credito_aa > 0:
            linha.risco_total_aa = risco_credito_aa

        if linha.risco_total_aa and linha.risco_total_aa > 0:
            linha.retorno_por_risco = base / linha.risco_total_aa
        else:
            linha.retorno_por_risco = None
            c.observacoes.append(
                "sem estimativa de risco: comparável em retorno, mas a razão "
                "retorno/risco não é definida")

        if (c.risco_credito is not None and c.risco_credito >= 4
                and (c.volatilidade_aa or 0) < 5.0):
            c.observacoes.append(
                f"risco de crédito {c.risco_credito}/5 com volatilidade de "
                f"apenas {(c.volatilidade_aa or 0):.1f}%: a oscilação de "
                f"preço NÃO mede o risco aqui. O que pode dar errado é o "
                f"emissor não pagar, e isso não aparece na volatilidade — "
                f"por isso o risco de crédito entra no denominador como "
                f"{risco_credito_aa:.1f} p.p. equivalentes.")

        if c.incerteza_retorno is not None:
            linha.retorno_pessimista_aa = base - c.incerteza_retorno

        rel.linhas.append(linha)

        # ------------------------------------- compatibilidade com o perfil
        if (c.horizonte_minimo_meses is not None
                and c.horizonte_minimo_meses > perfil.horizonte_meses):
            rel.incompativeis.append({
                "identificador": c.identificador,
                "motivo": f"exige horizonte de {c.horizonte_minimo_meses} "
                          f"meses, acima dos {perfil.horizonte_meses} "
                          f"informados"})
        if (c.liquidez_dias is not None
                and c.liquidez_dias > perfil.necessidade_liquidez_dias):
            rel.incompativeis.append({
                "identificador": c.identificador,
                "motivo": f"leva {c.liquidez_dias} dias para virar caixa, "
                          f"acima dos {perfil.necessidade_liquidez_dias} "
                          f"necessários"})
        if (c.drawdown_plausivel_pct is not None
                and c.drawdown_plausivel_pct > perfil.tolerancia_drawdown_pct):
            rel.incompativeis.append({
                "identificador": c.identificador,
                "motivo": f"drawdown plausível de "
                          f"{c.drawdown_plausivel_pct:.0f}% excede a "
                          f"tolerância de "
                          f"{perfil.tolerancia_drawdown_pct:.0f}%"})

    # ------------------------------------------------------------ conclusão
    comparaveis = [l for l in rel.linhas if l.comparavel]
    incompativeis_ids = {x["identificador"] for x in rel.incompativeis}
    elegiveis = [l for l in comparaveis
                 if l.candidato.identificador not in incompativeis_ids]

    if not comparaveis:
        rel.conclusao = (
            "NENHUMA COMPARAÇÃO POSSÍVEL: falta estimativa de retorno para "
            "todos os candidatos.")
        return rel

    if not elegiveis:
        rel.conclusao = (
            f"Nenhum dos {len(comparaveis)} candidatos é compatível com o "
            f"perfil informado (horizonte de {perfil.horizonte_meses} meses, "
            f"tolerância de {perfil.tolerancia_drawdown_pct:.0f}% de drawdown, "
            f"liquidez em {perfil.necessidade_liquidez_dias} dias). "
            f"Ampliar o horizonte ou aceitar mais risco são decisões suas, "
            f"não do sistema.")
        return rel

    # Ordena por retorno ajustado a risco quando disponível; senão por real.
    com_razao = [l for l in elegiveis if l.retorno_por_risco is not None]
    sem_razao = [l for l in elegiveis if l.retorno_por_risco is None]
    com_razao.sort(key=lambda l: l.retorno_por_risco or 0, reverse=True)
    # Empate técnico no topo merece nota: a diferença pode não ser real.
    sem_razao.sort(key=lambda l: l.retorno_real_liquido_aa
                   or l.retorno_liquido_aa or 0, reverse=True)

    melhor_nominal = max(comparaveis,
                         key=lambda l: l.candidato.retorno_nominal_aa or 0)
    melhor_ajustado = (com_razao[0] if com_razao
                       else sem_razao[0] if sem_razao else None)

    partes: list[str] = []
    if melhor_ajustado is not None:
        partes.append(
            f"Pelo retorno real líquido ajustado a risco, "
            f"{melhor_ajustado.candidato.identificador} lidera.")
        if (melhor_nominal.candidato.identificador
                != melhor_ajustado.candidato.identificador):
            partes.append(
                f"Repare que {melhor_nominal.candidato.identificador} tem a "
                f"MAIOR taxa nominal "
                f"({melhor_nominal.candidato.retorno_nominal_aa:.2f}% a.a.) e "
                f"não lidera: é exatamente por isso que comparar por "
                f"rentabilidade nominal engana.")

    if perfil.objetivo == "renda":
        partes.append(
            "Objetivo declarado é renda: priorize previsibilidade de fluxo "
            "(FII e renda fixa com pagamento periódico) sobre retorno total.")
    elif perfil.objetivo == "preservacao":
        partes.append(
            "Objetivo declarado é preservação: retorno real positivo com "
            "drawdown baixo vale mais que retorno alto com oscilação.")

    if rel.incompativeis:
        partes.append(
            f"{len(rel.incompativeis)} candidato(s) foram excluídos por "
            f"incompatibilidade com o perfil, não por retorno.")

    partes.append(
        "Nenhuma dessas estimativas é garantia; retorno de ativo de risco tem "
        "incerteza alta e o piso pessimista da tabela é mais informativo que "
        "a estimativa central.")
    rel.conclusao = " ".join(partes)
    return rel
