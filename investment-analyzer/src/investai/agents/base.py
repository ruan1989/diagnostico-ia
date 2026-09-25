"""Contrato dos agentes de análise.

Três decisões de projeto que importam
-------------------------------------
1. **`N/A` é um resultado válido e distinto de "neutro".** Funding não se
   aplica a um FII; vacância não se aplica a BTC. Um agente que devolvesse
   0,5 ("neutro") nesses casos estaria inventando uma pontuação e diluindo o
   score com um número sem significado. Aqui ele devolve `NAO_APLICAVEL`, e o
   consolidador redistribui o peso entre os fatores que existem.

2. **Abstenção é diferente de neutralidade.** Se a fonte de notícias não está
   configurada, o agente de notícias não diz "sentimento neutro" — ele diz
   `SEM_DADOS`. A diferença é que "neutro" entra na conta e "sem dados" sai
   dela, com a lacuna registrada.

3. **O agente de risco não pontua: ele veta.** Está fora deste contrato de
   propósito (ver `risk/engine.py`), porque misturar veto com pontuação
   permitiria que um score alto compensasse um risco inaceitável.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from ..data.symbols import AssetClass


class Postura(str, Enum):
    FORTE_ALTA = "forte_alta"
    ALTA = "alta"
    NEUTRO = "neutro"
    BAIXA = "baixa"
    FORTE_BAIXA = "forte_baixa"
    NAO_APLICAVEL = "nao_aplicavel"    # o fator não existe nesta classe
    SEM_DADOS = "sem_dados"            # o fator existe mas a fonte falta


# Posturas que NÃO entram no cálculo do score.
FORA_DO_CALCULO = (Postura.NAO_APLICAVEL, Postura.SEM_DADOS)


def postura_de_valor(valor: float) -> Postura:
    """Traduz um valor em [-1, +1] para postura."""
    if valor >= 0.60:
        return Postura.FORTE_ALTA
    if valor >= 0.20:
        return Postura.ALTA
    if valor <= -0.60:
        return Postura.FORTE_BAIXA
    if valor <= -0.20:
        return Postura.BAIXA
    return Postura.NEUTRO


@dataclass(slots=True)
class ParecerAgente:
    """Saída de um agente. `valor` só é usado se a postura entra no cálculo."""

    agente: str
    postura: Postura
    valor: float = 0.0            # -1.0 (baixa) .. +1.0 (alta)
    confianca: float = 0.0        # 0..1 — quanta evidência sustenta
    peso_base: float = 0.0        # peso nominal deste agente
    evidencias: list[str] = field(default_factory=list)
    contraindicacoes: list[str] = field(default_factory=list)
    dados_faltando: list[str] = field(default_factory=list)
    metricas: dict[str, Any] = field(default_factory=dict)

    @property
    def entra_no_calculo(self) -> bool:
        return self.postura not in FORA_DO_CALCULO

    @property
    def peso_efetivo(self) -> float:
        """Peso ajustado pela confiança: evidência fraca pesa menos."""
        if not self.entra_no_calculo:
            return 0.0
        return self.peso_base * max(0.0, min(1.0, self.confianca))

    def to_dict(self) -> dict[str, Any]:
        return {
            "agente": self.agente, "postura": self.postura.value,
            "valor": round(self.valor, 4) if self.entra_no_calculo else None,
            "confianca": round(self.confianca, 3),
            "peso_base": self.peso_base,
            "peso_efetivo": round(self.peso_efetivo, 4),
            "entra_no_calculo": self.entra_no_calculo,
            "evidencias": self.evidencias,
            "contraindicacoes": self.contraindicacoes,
            "dados_faltando": self.dados_faltando,
            "metricas": self.metricas,
        }


@dataclass(slots=True)
class ContextoAnalise:
    """Tudo o que os agentes podem consultar sobre um candidato."""

    symbol: str
    asset_class: AssetClass
    timeframe: str = "1H"
    # Features técnicas por timeframe (do módulo analysis).
    features: dict[str, Any] = field(default_factory=dict)
    snapshot: Any = None                     # MarketSnapshot
    # Fundamentos, quando a classe tiver e a fonte existir.
    fundamentos: dict[str, Any] | None = None
    # Dados de derivativos.
    derivativos: dict[str, Any] | None = None
    # Notícias e sentimento.
    noticias: list[dict[str, Any]] | None = None
    # Macro.
    macro: dict[str, Any] | None = None
    # Estatística histórica medida (do pipeline de validação).
    estatistica: Any = None                  # BacktestStats | ExpectedValue
    regime: str = ""
    qualidade_dados: Any = None              # QualityReport
    agora_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "asset_class": self.asset_class.value,
            "timeframe": self.timeframe, "regime": self.regime,
            "tem_fundamentos": self.fundamentos is not None,
            "tem_derivativos": self.derivativos is not None,
            "tem_noticias": self.noticias is not None,
            "tem_macro": self.macro is not None,
            "tem_estatistica": self.estatistica is not None,
        }


@runtime_checkable
class Agente(Protocol):
    nome: str
    peso: float

    def aplicavel(self, ctx: ContextoAnalise) -> bool:
        """False quando o fator não existe para esta classe de ativo."""
        ...

    def analisar(self, ctx: ContextoAnalise, direcao: int) -> ParecerAgente:
        """`direcao` é +1 para avaliar compra e -1 para venda."""
        ...


class AgenteBase:
    """Implementação comum: cuida de `N/A` e `SEM_DADOS` corretamente."""

    nome: str = "base"
    peso: float = 0.0
    classes_suportadas: tuple[AssetClass, ...] = ()

    def aplicavel(self, ctx: ContextoAnalise) -> bool:
        if not self.classes_suportadas:
            return True
        return ctx.asset_class in self.classes_suportadas

    def _na(self, motivo: str) -> ParecerAgente:
        return ParecerAgente(
            agente=self.nome, postura=Postura.NAO_APLICAVEL,
            peso_base=self.peso, evidencias=[f"N/A: {motivo}"])

    def _sem_dados(self, faltando: list[str], motivo: str) -> ParecerAgente:
        return ParecerAgente(
            agente=self.nome, postura=Postura.SEM_DADOS, peso_base=self.peso,
            dados_faltando=faltando, evidencias=[motivo])

    def _parecer(self, valor: float, confianca: float, *,
                 evidencias: list[str] | None = None,
                 contraindicacoes: list[str] | None = None,
                 metricas: dict[str, Any] | None = None) -> ParecerAgente:
        v = max(-1.0, min(1.0, valor))
        return ParecerAgente(
            agente=self.nome, postura=postura_de_valor(v), valor=v,
            confianca=max(0.0, min(1.0, confianca)), peso_base=self.peso,
            evidencias=evidencias or [],
            contraindicacoes=contraindicacoes or [],
            metricas=metricas or {})

    def analisar(self, ctx: ContextoAnalise, direcao: int) -> ParecerAgente:
        raise NotImplementedError
