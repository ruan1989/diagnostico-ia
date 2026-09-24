"""Chief Investment Engine — consolida os pareceres e emite a decisão.

O que o consolidador faz de diferente de uma média
--------------------------------------------------
1. **Renormaliza pelos pesos efetivos.** Fatores `N/A` e `SEM_DADOS` saem da
   conta e seus pesos são redistribuídos. Um FII não é penalizado por não ter
   funding, e um ativo sem fonte de notícias não recebe "sentimento neutro".

2. **Registra a cobertura.** Se metade dos agentes se absteve, o score pode
   ser alto e a decisão ainda é `DADOS_INSUFICIENTES`. Score calculado sobre
   poucos fatores não é a mesma coisa que score calculado sobre todos.

3. **Exige confirmação múltipla.** Um único agente forte não aprova nada: a
   decisão exige que um número mínimo de agentes independentes concorde com a
   direção.

4. **Trata contraindicação como informação, não como ruído.** Elas aparecem
   na decisão final, sempre, mesmo quando o score é bom.

5. **`NAO_OPERAR` é uma saída normal e frequente**, não uma falha.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from ..data.quality import QualityStatus
from ..risk.engine import DecisaoRiskEngine
from .base import AgenteBase, ContextoAnalise, ParecerAgente


class DecisaoFinal(str, Enum):
    VALIDADA = "validada_pelo_modelo"
    AGUARDAR_CONFIRMACAO = "aguardar_confirmacao"
    OBSERVAR = "observar"
    REJEITADA_PELO_RISCO = "rejeitada_pelo_risco"
    DADOS_INSUFICIENTES = "dados_insuficientes"
    NAO_OPERAR = "nao_operar"


# Faixas do score, conforme a especificação. O score é ferramenta interna e
# NÃO representa probabilidade de lucro.
FAIXAS_SCORE = (
    (0.0, 39.9, "REJEITAR"),
    (40.0, 59.9, "OBSERVAR"),
    (60.0, 74.9, "OPORTUNIDADE POTENCIAL"),
    (75.0, 89.9, "CONVICÇÃO QUANTITATIVA ELEVADA"),
    (90.0, 100.0, "EXCEPCIONAL — exige múltiplas confirmações independentes"),
)


def faixa_do_score(score: float) -> str:
    for lo, hi, rotulo in FAIXAS_SCORE:
        if lo <= score <= hi:
            return rotulo
    return "FORA DA FAIXA"


@dataclass(slots=True)
class CriteriosConsenso:
    """Quantos agentes precisam concordar, e com que cobertura."""

    # Fração mínima do peso nominal total que precisa estar disponível.
    cobertura_minima: float = 0.55
    # Número mínimo de agentes que entram no cálculo.
    min_agentes_participando: int = 3
    # Número mínimo de agentes que concordam com a direção avaliada.
    min_agentes_concordantes: int = 3
    # Score mínimo para virar candidato operável.
    score_min_operavel: float = 60.0
    score_min_conviccao: float = 75.0
    # O agente quantitativo tem poder de bloqueio: sem evidência estatística,
    # nada é "validado pelo modelo". Ele pode se abster, mas então a decisão
    # máxima possível é AGUARDAR_CONFIRMACAO.
    exigir_agente_quantitativo: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "cobertura_minima": self.cobertura_minima,
            "min_agentes_participando": self.min_agentes_participando,
            "min_agentes_concordantes": self.min_agentes_concordantes,
            "score_min_operavel": self.score_min_operavel,
            "score_min_conviccao": self.score_min_conviccao,
            "exigir_agente_quantitativo": self.exigir_agente_quantitativo,
        }


@dataclass(slots=True)
class Consenso:
    symbol: str
    direcao: str                     # "compra" ou "venda"
    score: float
    faixa: str
    decisao: DecisaoFinal
    cobertura: float
    n_participando: int
    n_concordantes: int
    n_divergentes: int
    pareceres: list[ParecerAgente] = field(default_factory=list)
    fatores_favoraveis: list[str] = field(default_factory=list)
    fatores_contrarios: list[str] = field(default_factory=list)
    dados_faltando: list[str] = field(default_factory=list)
    nao_aplicaveis: list[str] = field(default_factory=list)
    motivos_decisao: list[str] = field(default_factory=list)
    risco: DecisaoRiskEngine | None = None
    # Quanta da evidência acima é independente. None quando não há histórico
    # suficiente para medir — e nesse caso o consenso funciona igual, só sem
    # reportar redundância.
    redundancia: Any = None

    @property
    def operavel(self) -> bool:
        return self.decisao is DecisaoFinal.VALIDADA

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "direcao": self.direcao,
            "score": round(self.score, 2), "faixa": self.faixa,
            "decisao": self.decisao.value, "operavel": self.operavel,
            "cobertura": round(self.cobertura, 3),
            "redundancia": (self.redundancia.to_dict()
                            if self.redundancia is not None else None),
            "n_participando": self.n_participando,
            "n_concordantes": self.n_concordantes,
            "n_divergentes": self.n_divergentes,
            "fatores_favoraveis": self.fatores_favoraveis,
            "fatores_contrarios": self.fatores_contrarios,
            "dados_faltando": self.dados_faltando,
            "nao_aplicaveis": self.nao_aplicaveis,
            "motivos_decisao": self.motivos_decisao,
            "pareceres": [p.to_dict() for p in self.pareceres],
            "risco": self.risco.to_dict() if self.risco else None,
            "aviso_score": "score é ferramenta quantitativa interna; NÃO "
                           "representa probabilidade de lucro",
        }


def agentes_padrao_lazy(registro_modelos):
    from .especialistas import agentes_padrao
    return agentes_padrao(registro_modelos)


class ChiefInvestmentEngine:
    def __init__(self, agentes: Sequence[AgenteBase] | None = None,
                 criterios: CriteriosConsenso | None = None, *,
                 registro_modelos=None, historico_opinioes=None):
        from .especialistas import agentes_padrao
        self._agentes_fixos = list(agentes) if agentes else None
        self.registro_modelos = registro_modelos
        self.agentes = (self._agentes_fixos if self._agentes_fixos
                        else agentes_padrao(registro_modelos))
        self.criterios = criterios or CriteriosConsenso()
        self.peso_nominal_total = sum(a.peso for a in self.agentes)
        # Histórico das opiniões, para medir quanta evidência é de fato
        # independente. Opcional: sem ele o consenso funciona igual, só não
        # reporta redundância.
        self.historico_opinioes = historico_opinioes

    def recarregar_agentes(self) -> list[str]:
        """Remonta o painel de agentes. Chamado quando um modelo é promovido.

        O agente de ML entra e sai conforme existir modelo em produção, e o
        peso nominal total precisa acompanhar — senão a cobertura passaria a
        ser medida contra um denominador que não corresponde aos agentes que
        de fato rodaram.
        """
        if self._agentes_fixos:
            return [a.nome for a in self.agentes]
        self.agentes = agentes_padrao_lazy(self.registro_modelos)
        self.peso_nominal_total = sum(a.peso for a in self.agentes)
        return [a.nome for a in self.agentes]

    # -------------------------------------------------------------- análise
    def consultar(self, ctx: ContextoAnalise,
                  direcao: int) -> list[ParecerAgente]:
        """Roda todos os agentes. Exceção em um não derruba os outros."""
        out: list[ParecerAgente] = []
        for agente in self.agentes:
            if not agente.aplicavel(ctx):
                out.append(ParecerAgente(
                    agente=agente.nome,
                    postura=__import__(
                        "investai.agents.base", fromlist=["Postura"]
                    ).Postura.NAO_APLICAVEL,
                    peso_base=agente.peso,
                    evidencias=[f"N/A para {ctx.asset_class.value}"]))
                continue
            try:
                out.append(agente.analisar(ctx, direcao))
            except Exception as exc:                # noqa: BLE001
                from .base import Postura
                out.append(ParecerAgente(
                    agente=agente.nome, postura=Postura.SEM_DADOS,
                    peso_base=agente.peso,
                    dados_faltando=[f"erro interno: {type(exc).__name__}"],
                    evidencias=[f"agente falhou: {exc}"]))
        return out

    def consolidar(self, ctx: ContextoAnalise, direcao: int, *,
                   risco: DecisaoRiskEngine | None = None,
                   pareceres: Sequence[ParecerAgente] | None = None
                   ) -> Consenso:
        """Junta os pareceres, aplica os critérios e emite a decisão."""
        ps = list(pareceres) if pareceres is not None else self.consultar(
            ctx, direcao)
        rotulo_dir = "compra" if direcao > 0 else "venda"

        participando = [p for p in ps if p.entra_no_calculo]
        peso_efetivo_total = sum(p.peso_efetivo for p in participando)

        # ------------------------------------------------- redundância
        # Registra as opiniões desta rodada e mede quanta evidência é
        # independente. Só entram rodadas em que todos os participantes
        # opinaram: séries desalinhadas correlacionariam coisas diferentes.
        redundancia = None
        if self.historico_opinioes is not None and participando:
            self.historico_opinioes.registrar(
                {p.agente: p.valor for p in participando})
            redundancia = self.historico_opinioes.avaliar(
                {p.agente: p.peso_base for p in participando},
                fontes=[p.agente for p in participando])
        # Cobertura é medida sobre o peso NOMINAL dos agentes que puderam
        # opinar — não sobre o peso efetivo, que já desconta confiança.
        peso_nominal_disponivel = sum(p.peso_base for p in participando)
        cobertura = (peso_nominal_disponivel / self.peso_nominal_total
                     if self.peso_nominal_total else 0.0)

        # ---------------------------------------------------------- score
        if peso_efetivo_total > 0:
            bruto = sum(p.valor * p.peso_efetivo
                        for p in participando) / peso_efetivo_total
        else:
            bruto = 0.0
        score = max(0.0, min(100.0, bruto * 50.0 + 50.0))

        concordantes = [p for p in participando if p.valor > 0.10]
        divergentes = [p for p in participando if p.valor < -0.10]

        favoraveis = [f"[{p.agente}] {e}" for p in concordantes
                      for e in p.evidencias[:3]]
        contrarios = [f"[{p.agente}] {c}" for p in ps
                      for c in p.contraindicacoes]
        contrarios += [f"[{p.agente}] parecer contrário à direção avaliada"
                       for p in divergentes]
        faltando = [f"{p.agente}: {d}" for p in ps for d in p.dados_faltando]
        faltando += [f"{p.agente}: abstenção por ausência de fonte"
                     for p in ps if p.postura.value == "sem_dados"
                     and not p.dados_faltando]
        nao_aplicaveis = [p.agente for p in ps
                          if p.postura.value == "nao_aplicavel"]

        consenso = Consenso(
            symbol=ctx.symbol, direcao=rotulo_dir, score=score,
            faixa=faixa_do_score(score), decisao=DecisaoFinal.NAO_OPERAR,
            cobertura=cobertura, n_participando=len(participando),
            n_concordantes=len(concordantes), n_divergentes=len(divergentes),
            pareceres=ps, fatores_favoraveis=favoraveis,
            fatores_contrarios=contrarios, dados_faltando=faltando,
            nao_aplicaveis=nao_aplicaveis, risco=risco,
            redundancia=redundancia)

        # A redundância NÃO desconta o score em silêncio: ela aparece como
        # contraindicação, para que o número continue reproduzível na mão.
        if redundancia is not None and redundancia.material:
            consenso.fatores_contrarios.append(
                f"[redundância] apenas {redundancia.fator:.0%} do peso "
                f"analítico é independente: "
                + (f"{' e '.join('+'.join(g) for g in redundancia.grupos)} "
                   f"se movem juntos"
                   if redundancia.grupos else
                   "as fontes se movem juntas"))

        consenso.decisao = self._decidir(consenso, ctx, risco)
        return consenso

    def _decidir(self, c: Consenso, ctx: ContextoAnalise,
                 risco: DecisaoRiskEngine | None) -> DecisaoFinal:
        crit = self.criterios
        motivos = c.motivos_decisao

        # ------------------------------------- 1) risco tem a palavra final
        if risco is not None and not risco.aprovado:
            motivos.append(f"Risk Engine vetou: {risco.motivo_principal}")
            return DecisaoFinal.REJEITADA_PELO_RISCO

        # -------------------------------------- 2) qualidade de dados
        if ctx.qualidade_dados is not None:
            status = getattr(ctx.qualidade_dados, "status", None)
            if status in (QualityStatus.INADEQUADO,
                          QualityStatus.NAO_CONFIGURADO):
                motivos.append(
                    "NÃO É POSSÍVEL VALIDAR ESTA OPORTUNIDADE COM SEGURANÇA: "
                    + "; ".join(getattr(ctx.qualidade_dados, "motivos", [])))
                return DecisaoFinal.DADOS_INSUFICIENTES

        # ------------------------------------------------- 3) cobertura
        if c.n_participando < crit.min_agentes_participando:
            motivos.append(
                f"apenas {c.n_participando} agentes puderam opinar (mínimo "
                f"{crit.min_agentes_participando}): score calculado sobre "
                f"poucos fatores não é comparável a um score completo")
            return DecisaoFinal.DADOS_INSUFICIENTES

        if c.cobertura < crit.cobertura_minima:
            motivos.append(
                f"cobertura de {c.cobertura:.0%} do peso analítico, abaixo do "
                f"mínimo de {crit.cobertura_minima:.0%}. Faltam: "
                f"{', '.join(c.dados_faltando[:4])}")
            return DecisaoFinal.DADOS_INSUFICIENTES

        # --------------------------------- 4) agente quantitativo presente
        quant = next((p for p in c.pareceres
                      if p.agente == "quantitativo"), None)
        quant_ausente = quant is None or not quant.entra_no_calculo
        if crit.exigir_agente_quantitativo and quant_ausente:
            motivos.append(
                "sem evidência estatística medida (o agente quantitativo se "
                "absteve): nada pode ser 'validado pelo modelo' sem amostra "
                "histórica. Máximo possível é acompanhamento.")
            if c.score >= crit.score_min_operavel:
                return DecisaoFinal.AGUARDAR_CONFIRMACAO
            return DecisaoFinal.OBSERVAR

        # -------------------------------------- 5) confirmação múltipla
        if c.n_concordantes < crit.min_agentes_concordantes:
            motivos.append(
                f"apenas {c.n_concordantes} agentes concordam com a direção "
                f"(mínimo {crit.min_agentes_concordantes}): nenhuma operação "
                f"depende de um único ângulo de análise")
            return (DecisaoFinal.AGUARDAR_CONFIRMACAO
                    if c.score >= crit.score_min_operavel
                    else DecisaoFinal.OBSERVAR)

        # ------------------------------------------------- 6) score
        if c.score < crit.score_min_operavel:
            motivos.append(
                f"score {c.score:.1f} na faixa '{c.faixa}': abaixo do mínimo "
                f"operável de {crit.score_min_operavel:.0f}")
            return (DecisaoFinal.OBSERVAR if c.score >= 40.0
                    else DecisaoFinal.NAO_OPERAR)

        # ----------------------------- 7) divergência relevante entre agentes
        if c.n_divergentes >= c.n_concordantes:
            motivos.append(
                f"{c.n_divergentes} agentes divergem contra "
                f"{c.n_concordantes} que concordam: sem consenso, não se "
                f"opera")
            return DecisaoFinal.AGUARDAR_CONFIRMACAO

        # --------------------------- 8) contraindicação de peso com score alto
        if c.score >= crit.score_min_conviccao and len(c.fatores_contrarios) >= 4:
            motivos.append(
                f"score alto ({c.score:.1f}) mas {len(c.fatores_contrarios)} "
                f"contraindicações registradas: convém aguardar confirmação")
            return DecisaoFinal.AGUARDAR_CONFIRMACAO

        motivos.append(
            f"score {c.score:.1f} ({c.faixa}), {c.n_concordantes} agentes "
            f"concordantes e {c.cobertura:.0%} de cobertura analítica; "
            f"Risk Engine aprovou")
        return DecisaoFinal.VALIDADA

    # ------------------------------------------------------ melhor direção
    def avaliar_ambas_direcoes(self, ctx: ContextoAnalise, *,
                               risco_compra: DecisaoRiskEngine | None = None,
                               risco_venda: DecisaoRiskEngine | None = None
                               ) -> tuple[Consenso, Consenso]:
        """Avalia compra e venda. Ver os dois lados evita o viés de só
        procurar compra em mercado que está caindo."""
        compra = self.consolidar(ctx, +1, risco=risco_compra)
        venda = self.consolidar(ctx, -1, risco=risco_venda)
        return compra, venda

    def melhor(self, ctx: ContextoAnalise, *,
               risco_compra: DecisaoRiskEngine | None = None,
               risco_venda: DecisaoRiskEngine | None = None) -> Consenso:
        compra, venda = self.avaliar_ambas_direcoes(
            ctx, risco_compra=risco_compra, risco_venda=risco_venda)
        # Prefere a decisão mais avançada; empate vai para o maior score.
        ordem = {
            DecisaoFinal.VALIDADA: 5,
            DecisaoFinal.AGUARDAR_CONFIRMACAO: 4,
            DecisaoFinal.OBSERVAR: 3,
            DecisaoFinal.DADOS_INSUFICIENTES: 2,
            DecisaoFinal.REJEITADA_PELO_RISCO: 1,
            DecisaoFinal.NAO_OPERAR: 0,
        }
        return max((compra, venda),
                   key=lambda c: (ordem[c.decisao], c.score))

    def config(self) -> dict[str, Any]:
        return {
            "agentes": [{"nome": a.nome, "peso": a.peso,
                         "classes": [c.value for c in a.classes_suportadas]
                         or ["todas"]}
                        for a in self.agentes],
            "peso_nominal_total": round(self.peso_nominal_total, 4),
            "criterios": self.criterios.to_dict(),
            "faixas_score": [{"de": lo, "ate": hi, "rotulo": r}
                             for lo, hi, r in FAIXAS_SCORE],
            "decisoes_possiveis": [d.value for d in DecisaoFinal],
            "observacao": "o agente de risco não pontua: ele veta "
                          "(ver risk/engine.py)",
        }
