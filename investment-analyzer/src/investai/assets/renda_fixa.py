"""Renda fixa: comparação por retorno LÍQUIDO e risco real.

O erro que este módulo evita
----------------------------
Comparar "CDB de 115% do CDI" com "Tesouro IPCA+ 6%" pelo número maior. São
grandezas diferentes: uma é pós-fixada e nominal, a outra é indexada e real;
uma tem IR regressivo, a outra também mas com prazos distintos; uma tem FGC,
a outra tem risco soberano; uma marca a mercado, a outra pode ser mantida ao
par no vencimento.

A comparação só faz sentido depois de converter tudo para **retorno real
líquido esperado**, e mesmo então precisa vir acompanhada de duration, risco
de crédito e liquidez — porque taxa alta em emissor frágil não é retorno, é
prêmio de risco que pode não se realizar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..data.quality import FONTE_NAO_CONFIGURADA


class Indexador(str, Enum):
    PRE = "prefixado"
    CDI = "pos_cdi"
    SELIC = "pos_selic"
    IPCA = "ipca_mais"
    IGPM = "igpm_mais"


class TipoRendaFixa(str, Enum):
    TESOURO = "tesouro"
    CDB = "cdb"
    LCI = "lci"
    LCA = "lca"
    LC = "lc"
    DEBENTURE = "debenture"
    DEBENTURE_INCENTIVADA = "debenture_incentivada"
    CRI = "cri"
    CRA = "cra"
    FUNDO_DI = "fundo_di"


# Instrumentos isentos de imposto de renda para pessoa física.
ISENTOS_IR = {TipoRendaFixa.LCI, TipoRendaFixa.LCA,
              TipoRendaFixa.DEBENTURE_INCENTIVADA, TipoRendaFixa.CRI,
              TipoRendaFixa.CRA}

# Cobertos pelo FGC, até o limite legal por CPF e por instituição.
COBERTOS_FGC = {TipoRendaFixa.CDB, TipoRendaFixa.LCI, TipoRendaFixa.LCA,
                TipoRendaFixa.LC}
LIMITE_FGC = 250_000.0
# Teto global por CPF a cada 4 anos, somando todas as instituições.
TETO_GLOBAL_FGC = 1_000_000.0

# Tabela regressiva de IR sobre rendimento, por prazo em dias.
FAIXAS_IR = ((180, 0.225), (360, 0.20), (720, 0.175), (10**9, 0.15))


def aliquota_ir(dias: int, tipo: TipoRendaFixa) -> float:
    """Alíquota de IR sobre o rendimento, conforme prazo e isenção."""
    if tipo in ISENTOS_IR:
        return 0.0
    for limite, aliq in FAIXAS_IR:
        if dias <= limite:
            return aliq
    return 0.15


@dataclass(slots=True)
class Titulo:
    """Um título de renda fixa com os parâmetros necessários à comparação."""

    nome: str
    tipo: TipoRendaFixa
    indexador: Indexador
    # Taxa contratada: % ao ano no prefixado, % do índice no pós, spread no
    # IPCA+ / IGPM+.
    taxa: float
    prazo_dias: int
    emissor: str = ""
    # Nota de crédito simplificada: 1 (soberano) a 5 (alto risco).
    risco_credito: int = 3
    liquidez_diaria: bool = False
    valor_minimo: float = 0.0
    marcacao_a_mercado: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "nome": self.nome, "tipo": self.tipo.value,
            "indexador": self.indexador.value, "taxa": self.taxa,
            "prazo_dias": self.prazo_dias, "emissor": self.emissor,
            "risco_credito": self.risco_credito,
            "liquidez_diaria": self.liquidez_diaria,
            "valor_minimo": self.valor_minimo,
            "marcacao_a_mercado": self.marcacao_a_mercado,
        }


@dataclass(slots=True)
class CenarioMacro:
    """Premissas de mercado. Sem elas não há comparação possível."""

    cdi_aa: float | None = None
    selic_aa: float | None = None
    ipca_aa: float | None = None
    igpm_aa: float | None = None

    @property
    def completo(self) -> bool:
        return self.cdi_aa is not None and self.ipca_aa is not None

    def to_dict(self) -> dict[str, Any]:
        return {"cdi_aa": self.cdi_aa, "selic_aa": self.selic_aa,
                "ipca_aa": self.ipca_aa, "igpm_aa": self.igpm_aa,
                "completo": self.completo}


@dataclass(slots=True)
class AnaliseTitulo:
    titulo: Titulo
    bruto_aa: float = 0.0
    liquido_aa: float = 0.0
    real_liquido_aa: float = 0.0
    aliquota_ir: float = 0.0
    duration_anos: float = 0.0
    coberto_fgc: bool = False
    dentro_do_fgc: bool = False
    score: float = 0.0
    alertas: list[str] = field(default_factory=list)
    disponivel: bool = True
    mensagem: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "titulo": self.titulo.to_dict(),
            "bruto_aa": round(self.bruto_aa, 3),
            "liquido_aa": round(self.liquido_aa, 3),
            "real_liquido_aa": round(self.real_liquido_aa, 3),
            "aliquota_ir": self.aliquota_ir,
            "duration_anos": round(self.duration_anos, 2),
            "coberto_fgc": self.coberto_fgc,
            "dentro_do_fgc": self.dentro_do_fgc,
            "score": round(self.score, 2),
            "alertas": self.alertas,
            "disponivel": self.disponivel, "mensagem": self.mensagem,
        }


def analisar_titulo(titulo: Titulo, macro: CenarioMacro, *,
                    valor_aplicado: float = 10_000.0) -> AnaliseTitulo:
    """Converte a taxa contratada em retorno real líquido comparável."""
    a = AnaliseTitulo(titulo=titulo)

    if not macro.completo:
        a.disponivel = False
        a.mensagem = (
            f"{FONTE_NAO_CONFIGURADA}: comparar renda fixa exige CDI e IPCA "
            f"correntes. Sem eles, '115% do CDI' e 'IPCA+6%' não são "
            f"comparáveis.")
        return a

    cdi = macro.cdi_aa or 0.0
    selic = macro.selic_aa if macro.selic_aa is not None else cdi
    ipca = macro.ipca_aa or 0.0
    igpm = macro.igpm_aa if macro.igpm_aa is not None else ipca

    # ------------------------------------------------------------ bruto
    if titulo.indexador is Indexador.PRE:
        a.bruto_aa = titulo.taxa
    elif titulo.indexador is Indexador.CDI:
        a.bruto_aa = cdi * titulo.taxa / 100.0
    elif titulo.indexador is Indexador.SELIC:
        a.bruto_aa = selic + titulo.taxa
    elif titulo.indexador is Indexador.IPCA:
        # Composição correta: (1+ipca)(1+spread) - 1, não a soma.
        a.bruto_aa = ((1 + ipca / 100.0) * (1 + titulo.taxa / 100.0) - 1) * 100
    elif titulo.indexador is Indexador.IGPM:
        a.bruto_aa = ((1 + igpm / 100.0) * (1 + titulo.taxa / 100.0) - 1) * 100

    # ----------------------------------------------------------- líquido
    a.aliquota_ir = aliquota_ir(titulo.prazo_dias, titulo.tipo)
    a.liquido_aa = a.bruto_aa * (1 - a.aliquota_ir)

    # -------------------------------------------------------------- real
    a.real_liquido_aa = ((1 + a.liquido_aa / 100.0) / (1 + ipca / 100.0) - 1) * 100

    a.duration_anos = titulo.prazo_dias / 365.0
    a.coberto_fgc = titulo.tipo in COBERTOS_FGC
    a.dentro_do_fgc = a.coberto_fgc and valor_aplicado <= LIMITE_FGC

    # ------------------------------------------------------------ score
    # Retorno real líquido é o eixo principal; risco de crédito, liquidez e
    # duration ajustam.
    ret = max(-1.0, min(1.0, (a.real_liquido_aa - 4.0) / 5.0))
    credito = max(-1.0, min(1.0, (3.0 - titulo.risco_credito) / 2.0))
    if a.dentro_do_fgc:
        # O FGC MITIGA o risco de emissor, mas não o elimina: o pagamento
        # leva de semanas a meses (dinheiro indisponível justamente na crise
        # que quebrou o banco), o limite é por CPF e por instituição, e há
        # teto global de R$ 1 milhão a cada 4 anos. Zerar a penalidade de um
        # emissor nota 5 faria o sistema tratá-lo como equivalente ao
        # Tesouro, o que não é verdade.
        credito = max(credito, -0.1) + 0.35
        credito = max(-1.0, min(1.0, credito))
    liquidez = 0.6 if titulo.liquidez_diaria else -0.2
    # Duration longa é risco quando há marcação a mercado.
    dur = (max(-1.0, min(1.0, (4.0 - a.duration_anos) / 4.0))
           if titulo.marcacao_a_mercado else 0.3)

    bruto_score = ret * 0.45 + credito * 0.25 + liquidez * 0.15 + dur * 0.15
    a.score = max(0.0, min(100.0, bruto_score * 50.0 + 50.0))

    # ----------------------------------------------------------- alertas
    if a.real_liquido_aa < 0:
        a.alertas.append(
            f"retorno real líquido NEGATIVO ({a.real_liquido_aa:.2f}% a.a.): "
            f"o título perde poder de compra depois de IR e inflação")
    if titulo.risco_credito >= 4:
        if a.dentro_do_fgc:
            a.alertas.append(
                f"risco de crédito alto (nota {titulo.risco_credito}/5). O "
                f"FGC cobre até R$ {LIMITE_FGC:,.2f} por CPF e por "
                f"instituição, mas o pagamento leva semanas a meses — o "
                f"dinheiro fica indisponível exatamente na crise que quebrou "
                f"o emissor. Há ainda teto global de "
                f"R$ {TETO_GLOBAL_FGC:,.2f} por CPF a cada 4 anos, somando "
                f"todas as instituições.")
        else:
            a.alertas.append(
                f"risco de crédito alto (nota {titulo.risco_credito}/5) SEM "
                f"cobertura do FGC: a taxa elevada é prêmio por esse risco, "
                f"e o risco pode se materializar")
    if a.coberto_fgc and valor_aplicado > LIMITE_FGC:
        a.alertas.append(
            f"aplicação de R$ {valor_aplicado:,.2f} excede o limite do FGC "
            f"(R$ {LIMITE_FGC:,.2f}): o excedente fica sem cobertura")
    if titulo.marcacao_a_mercado and a.duration_anos > 5:
        a.alertas.append(
            f"duration de {a.duration_anos:.1f} anos com marcação a mercado: "
            f"venda antecipada pode sair com prejuízo se os juros subirem")
    if not titulo.liquidez_diaria:
        a.alertas.append(
            f"sem liquidez diária: o dinheiro fica preso até o vencimento "
            f"({titulo.prazo_dias} dias) ou sujeito a deságio no mercado "
            f"secundário")
    if titulo.indexador is Indexador.PRE and a.duration_anos > 3:
        a.alertas.append(
            "prefixado longo: se a inflação surpreender para cima, o retorno "
            "real contratado se perde")
    return a


def comparar_titulos(titulos: list[Titulo], macro: CenarioMacro, *,
                     valor_aplicado: float = 10_000.0) -> dict[str, Any]:
    """Ranqueia por retorno real líquido, não por taxa nominal."""
    analises = [analisar_titulo(t, macro, valor_aplicado=valor_aplicado)
                for t in titulos]
    disponiveis = [a for a in analises if a.disponivel]
    if not disponiveis:
        return {
            "disponivel": False,
            "mensagem": (analises[0].mensagem if analises
                         else FONTE_NAO_CONFIGURADA),
            "titulos": [a.to_dict() for a in analises],
        }

    ordenado = sorted(disponiveis, key=lambda a: a.score, reverse=True)
    return {
        "disponivel": True,
        "macro": macro.to_dict(),
        "valor_aplicado": valor_aplicado,
        "titulos": [a.to_dict() for a in ordenado],
        "melhor_retorno_real": ordenado[0].titulo.nome if ordenado else "",
        "observacao": (
            "a ordenação usa RETORNO REAL LÍQUIDO ajustado por risco de "
            "crédito, liquidez e duration — não a taxa nominal contratada. "
            "Taxa alta em emissor frágil é prêmio de risco, não retorno."),
    }
