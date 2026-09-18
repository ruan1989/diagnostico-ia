"""Risk Engine — autoridade final e independente sobre qualquer operação.

Posição deste módulo na arquitetura
-----------------------------------
Os agentes de análise *propõem*. O Risk Engine *decide*. Não há caminho de
código pelo qual um agente, um score alto, uma estratégia promovida ou uma
ordem manual passe por cima de um veto daqui. Isso é intencional: em toda
conta que quebra, a causa proximal é a mesma — alguém encontrou um jeito de
contornar o limite "só esta vez".

O que este módulo recusa por princípio, não por parametrização
-------------------------------------------------------------
* **Martingale / dobrar após perda** — aumentar posição para recuperar
  prejuízo transforma uma sequência ruim normal em ruína.
* **Averaging down sem regra prévia** — adicionar a uma posição perdedora
  porque ela está perdendo é a mesma coisa com outro nome.
* **Afastar o stop** — mover a invalidação porque o preço chegou nela é
  desistir da gestão de risco no momento exato em que ela seria usada.
* **Revenge trading** — reentrar imediatamente após stop, no mesmo ativo e
  direção.
* **Alavancagem sem teto** e **stop depois da liquidação** (ver
  `liquidation.py`).

Essas proibições não têm flag de configuração. Um sistema em que elas podem
ser desligadas não as tem.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from ..config import RiskConfig
from ..models import Position, Side, Signal, Trade
from . import liquidation
from .manager import RiskManager, grupo_de, indice_dia


class Veredicto(str, Enum):
    APROVADO = "aprovado"
    APROVADO_COM_REDUCAO = "aprovado_com_reducao"
    REJEITADO = "rejeitado_pelo_risco"
    TRADING_HALTED = "trading_halted"


class Severidade(str, Enum):
    VETO = "veto"            # impede a operação, sem exceção
    REDUCAO = "reducao"      # permite, com tamanho menor
    AVISO = "aviso"          # registra, não impede


@dataclass(slots=True)
class Achado:
    """Uma checagem que falhou, com severidade e explicação."""

    regra: str
    severidade: Severidade
    mensagem: str
    detalhes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"regra": self.regra, "severidade": self.severidade.value,
                "mensagem": self.mensagem, "detalhes": self.detalhes}


@dataclass(slots=True)
class DecisaoRiskEngine:
    veredicto: Veredicto
    size: float = 0.0
    notional_usd: float = 0.0
    risco_usd: float = 0.0
    alavancagem: float = 1.0
    achados: list[Achado] = field(default_factory=list)
    analise_liquidacao: dict[str, Any] | None = None
    fator_reducao: float = 1.0

    @property
    def aprovado(self) -> bool:
        return self.veredicto in (Veredicto.APROVADO,
                                  Veredicto.APROVADO_COM_REDUCAO)

    @property
    def vetos(self) -> list[Achado]:
        return [a for a in self.achados if a.severidade is Severidade.VETO]

    @property
    def motivo_principal(self) -> str:
        if self.vetos:
            return self.vetos[0].mensagem
        if self.veredicto is Veredicto.APROVADO_COM_REDUCAO:
            reducoes = [a for a in self.achados
                        if a.severidade is Severidade.REDUCAO]
            return reducoes[0].mensagem if reducoes else "aprovado com redução"
        return "aprovado"

    def to_dict(self) -> dict[str, Any]:
        return {
            "veredicto": self.veredicto.value,
            "aprovado": self.aprovado,
            "motivo_principal": self.motivo_principal,
            "size": self.size,
            "notional_usd": round(self.notional_usd, 2),
            "risco_usd": round(self.risco_usd, 2),
            "alavancagem": round(self.alavancagem, 2),
            "fator_reducao": round(self.fator_reducao, 4),
            "n_vetos": len(self.vetos),
            "achados": [a.to_dict() for a in self.achados],
            "analise_liquidacao": self.analise_liquidacao,
        }


@dataclass(slots=True)
class ContextoMercado:
    """Informações externas que o Risk Engine consulta, quando disponíveis.

    Campos ausentes NÃO são tratados como "está tudo bem": o engine registra
    a ausência como aviso, porque decidir sem saber é diferente de decidir
    sabendo que está tudo em ordem.
    """

    atr_pct: float | None = None
    volume_24h_usd: float | None = None
    spread_pct: float | None = None
    # Minutos até o próximo evento econômico de impacto alto/crítico.
    minutos_ate_evento_critico: int | None = None
    descricao_evento: str = ""
    # Regime atual, para vetar estratégia em regime incompatível.
    regime: str = ""
    # Qualidade dos dados que geraram o sinal.
    dados_confiaveis: bool = True
    motivo_dados: str = ""
    # Gap histórico típico do ativo entre fechamento e abertura, em %.
    gap_tipico_pct: float | None = None
    saude_sistema: str = "HEALTHY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "atr_pct": self.atr_pct, "volume_24h_usd": self.volume_24h_usd,
            "spread_pct": self.spread_pct,
            "minutos_ate_evento_critico": self.minutos_ate_evento_critico,
            "descricao_evento": self.descricao_evento, "regime": self.regime,
            "dados_confiaveis": self.dados_confiaveis,
            "motivo_dados": self.motivo_dados,
            "gap_tipico_pct": self.gap_tipico_pct,
            "saude_sistema": self.saude_sistema,
        }


@dataclass(slots=True)
class LimitesExtra:
    """Limites que complementam o RiskConfig, específicos de derivativos."""

    minutos_bloqueio_pre_evento: int = 45
    max_spread_pct: float = 0.20
    folga_liquidacao_minima: float = liquidation.FOLGA_MINIMA
    min_atr_ate_liquidacao: float = liquidation.MIN_ATR_ATE_LIQUIDACAO
    # Janela em que reentrar no mesmo ativo e direção após stop é considerado
    # revenge trading.
    minutos_revenge: int = 120
    # Gap típico acima disto exige redução de tamanho: o stop não protege
    # contra abertura em gap.
    gap_alerta_pct: float = 2.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "minutos_bloqueio_pre_evento": self.minutos_bloqueio_pre_evento,
            "max_spread_pct": self.max_spread_pct,
            "folga_liquidacao_minima": self.folga_liquidacao_minima,
            "min_atr_ate_liquidacao": self.min_atr_ate_liquidacao,
            "minutos_revenge": self.minutos_revenge,
            "gap_alerta_pct": self.gap_alerta_pct,
        }


class ProibicaoError(RuntimeError):
    """Tentativa de executar prática estruturalmente proibida."""


class RiskEngine:
    """Envolve o `RiskManager` e adiciona as checagens de derivativos.

    O `RiskManager` continua responsável por capital, janelas de perda e
    kill switch. O engine adiciona liquidação, evento econômico, spread,
    qualidade de dados, saúde do sistema e as proibições estruturais — e é
    ele quem o resto do sistema consulta.
    """

    def __init__(self, cfg: RiskConfig, capital_inicial: float,
                 extra: LimitesExtra | None = None,
                 manager: RiskManager | None = None):
        self.cfg = cfg
        self.extra = extra or LimitesExtra()
        self.manager = manager or RiskManager(cfg, capital_inicial)
        # Histórico mínimo para detectar revenge trading e contar operações
        # por dia.
        self._ultimos_stops: dict[tuple[str, str], int] = {}
        self._trades_por_dia: dict[int, int] = {}
        self._halt_manual = False
        self._motivo_halt = ""

    # ------------------------------------------------------------- estado
    @property
    def estado(self):
        return self.manager.estado

    @property
    def halted(self) -> bool:
        return self._halt_manual or self.manager.estado.kill_switch

    def halt(self, motivo: str) -> None:
        """TRADING HALTED. Reativação exige `retomar` explícito."""
        self._halt_manual = True
        self._motivo_halt = motivo

    def retomar(self, confirmacao: str) -> tuple[bool, str]:
        """Reativa após halt. Exige confirmação explícita, por design."""
        # A fricção é a frase, não a tecla Caps Lock: espaços em volta e
        # caixa são tolerados de propósito. Travar a retomada por causa de
        # maiúsculas deixaria o operador sem saída justamente no momento em
        # que ele precisa reagir. Qualquer OUTRA frase é recusada.
        if confirmacao.strip().upper() != "RETOMAR OPERACAO":
            return False, ("confirmação incorreta: envie a frase "
                           "'RETOMAR OPERACAO' para reativar")
        self._halt_manual = False
        self._motivo_halt = ""
        self.manager.liberar_kill_switch()
        return True, "operação retomada; kill switch rearmado"

    # ------------------------------------------------- proibições estruturais
    def verificar_proibicoes(self, symbol: str, side: Side, *,
                             posicao_existente: Position | None = None,
                             size_proposto: float = 0.0,
                             stop_proposto: float | None = None,
                             agora_ms: int | None = None) -> list[Achado]:
        """Checa as práticas que o sistema recusa por princípio."""
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        achados: list[Achado] = []

        # ---------------------------------- martingale / dobrar após perda
        if self.estado.perdas_consecutivas > 0 and posicao_existente is None:
            risco_padrao = (self.estado.capital_atual
                            * self.cfg.risco_por_trade_pct / 100.0)
            # Só é martingale se o risco proposto é MAIOR que o padrão após
            # perda. Risco igual ou menor é operação normal.
            if size_proposto > 0 and stop_proposto is not None:
                # Não há como saber o risco sem preço; o engine valida isso
                # no dimensionamento. Aqui registra apenas a condição.
                pass
            achados.append(Achado(
                "pos_perda_tamanho_normal", Severidade.AVISO,
                f"{self.estado.perdas_consecutivas} perda(s) consecutiva(s): o "
                f"tamanho da próxima entrada permanece o padrão de "
                f"{self.cfg.risco_por_trade_pct}% (US$ {risco_padrao:.2f}). "
                f"Aumentar para recuperar é martingale e está bloqueado."))

        # ----------------------------------------------- averaging down
        if posicao_existente is not None:
            mesma_direcao = posicao_existente.side is side
            if mesma_direcao:
                achados.append(Achado(
                    "averaging_down", Severidade.VETO,
                    f"PROIBIDO: já existe posição {side.value} em {symbol}. "
                    f"Adicionar a uma posição aberta sem regra de pirâmide "
                    f"predefinida é averaging down — a posição cresce "
                    f"justamente quando a tese está sendo contrariada.",
                    {"posicao_entry": posicao_existente.entry,
                     "posicao_size": posicao_existente.size}))
            else:
                achados.append(Achado(
                    "hedge_nao_suportado", Severidade.VETO,
                    f"PROIBIDO: existe posição {posicao_existente.side.value} "
                    f"aberta em {symbol}. Abrir o lado oposto no mesmo ativo "
                    f"não é hedge, é pagar taxa duas vezes para ficar neutro."))

        # ------------------------------------------------- revenge trading
        chave = (symbol, side.value)
        if ts_stop := self._ultimos_stops.get(chave):
            decorrido_min = (agora - ts_stop) / 60_000
            if decorrido_min < self.extra.minutos_revenge:
                achados.append(Achado(
                    "revenge_trading", Severidade.VETO,
                    f"PROIBIDO: stop em {symbol} ({side.value}) há "
                    f"{decorrido_min:.0f} min. Reentrar no mesmo ativo e "
                    f"direção antes de {self.extra.minutos_revenge} min é "
                    f"revenge trading.",
                    {"minutos_desde_stop": round(decorrido_min, 1)}))

        return achados

    def validar_ajuste_de_stop(self, posicao: Position,
                               novo_stop: float) -> tuple[bool, str]:
        """Permite apertar o stop; recusa afastá-lo.

        Esta é a proibição mais importante da lista, porque é a mais tentadora:
        o preço chega no stop, a tese "ainda faz sentido", e mover o stop
        parece prudência. Não é — é transformar uma perda definida em uma
        perda indefinida.
        """
        long = posicao.side is Side.LONG
        afastou = (novo_stop < posicao.stop_loss if long
                   else novo_stop > posicao.stop_loss)
        if afastou:
            return False, (
                f"PROIBIDO: mover o stop de {posicao.symbol} de "
                f"{posicao.stop_loss:.6g} para {novo_stop:.6g} AFASTA a "
                f"invalidação. O stop só pode ser apertado na direção do "
                f"lucro (trailing), nunca alargado para evitar a perda.")
        if (long and novo_stop >= posicao.entry * 1.5) or (
                not long and novo_stop <= posicao.entry * 0.5):
            return False, (
                f"stop de {novo_stop:.6g} está implausível em relação à "
                f"entrada {posicao.entry:.6g}")
        return True, f"stop apertado para {novo_stop:.6g}"

    # --------------------------------------------------------- avaliação
    def avaliar(self, sinal: Signal, posicoes: Sequence[Position], *,
                contexto: ContextoMercado | None = None,
                agora_ms: int | None = None) -> DecisaoRiskEngine:
        """Decisão final sobre uma entrada proposta."""
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        ctx = contexto or ContextoMercado()
        achados: list[Achado] = []

        # ------------------------------------------------------- 0) halt
        if self.halted:
            motivo = (self._motivo_halt
                      or self.manager.estado.motivo_kill or "halt ativo")
            return DecisaoRiskEngine(
                Veredicto.TRADING_HALTED,
                achados=[Achado("trading_halted", Severidade.VETO,
                                f"TRADING HALTED: {motivo}. Nenhuma nova "
                                f"operação até reativação explícita.")])

        # -------------------------------------------- 1) saúde e dados
        if ctx.saude_sistema.upper() == "OFFLINE":
            achados.append(Achado(
                "sistema_offline", Severidade.VETO,
                "serviço crítico OFFLINE: não é possível gerenciar posição "
                "aberta com segurança, então não se abre posição nova."))
        elif ctx.saude_sistema.upper() == "DEGRADED":
            achados.append(Achado(
                "sistema_degradado", Severidade.REDUCAO,
                "sistema em estado DEGRADED: tamanho reduzido enquanto a "
                "infraestrutura não voltar ao normal.", {"fator": 0.5}))

        if not ctx.dados_confiaveis:
            achados.append(Achado(
                "dados_inadequados", Severidade.VETO,
                f"NÃO É POSSÍVEL VALIDAR ESTA OPORTUNIDADE COM SEGURANÇA: "
                f"{ctx.motivo_dados or 'qualidade de dados abaixo do mínimo'}"))

        # ------------------------------------------ 2) evento econômico
        if ctx.minutos_ate_evento_critico is None:
            achados.append(Achado(
                "calendario_indisponivel", Severidade.AVISO,
                "calendário econômico não configurado: não foi possível "
                "verificar proximidade de evento de alto impacto."))
        elif 0 <= ctx.minutos_ate_evento_critico <= self.extra.minutos_bloqueio_pre_evento:
            achados.append(Achado(
                "evento_critico_proximo", Severidade.VETO,
                f"evento de alto impacto em {ctx.minutos_ate_evento_critico} "
                f"min ({ctx.descricao_evento or 'sem descrição'}). Entrada "
                f"alavancada antes de evento assume risco de gap que o stop "
                f"não protege.",
                {"minutos": ctx.minutos_ate_evento_critico}))

        # ------------------------------------------------- 3) microestrutura
        if ctx.spread_pct is not None and ctx.spread_pct > self.extra.max_spread_pct:
            achados.append(Achado(
                "spread_alto", Severidade.VETO,
                f"spread de {ctx.spread_pct:.3f}% acima do limite de "
                f"{self.extra.max_spread_pct:.3f}%: o custo de entrada e "
                f"saída consome a vantagem esperada."))

        if ctx.gap_tipico_pct is not None and ctx.gap_tipico_pct > self.extra.gap_alerta_pct:
            achados.append(Achado(
                "risco_de_gap", Severidade.REDUCAO,
                f"gap típico de {ctx.gap_tipico_pct:.2f}% neste ativo excede "
                f"{self.extra.gap_alerta_pct:.2f}%: o stop pode ser executado "
                f"bem além do preço definido. Tamanho reduzido.",
                {"fator": 0.6}))

        # -------------------------------- 4) proibições e posição existente
        existente = next((p for p in posicoes if p.symbol == sinal.symbol), None)
        achados.extend(self.verificar_proibicoes(
            sinal.symbol, sinal.side, posicao_existente=existente,
            agora_ms=agora))

        # ------------------------------- 5) limites do RiskManager (base)
        base = self.manager.avaliar_entrada(sinal, posicoes, agora_ms=agora)
        if not base.aprovado:
            for bloqueio in base.bloqueios:
                achados.append(Achado("limite_de_risco", Severidade.VETO,
                                      bloqueio))

        # ------------------------------ 6) concentração por fator de risco
        achados.extend(self._checar_concentracao(sinal, posicoes))

        # ------------------------------------------------ veto acumulado?
        vetos = [a for a in achados if a.severidade is Severidade.VETO]
        if vetos:
            return DecisaoRiskEngine(Veredicto.REJEITADO, achados=achados)

        # ---------------------------------- 7) dimensionamento e liquidação
        size = base.size
        notional = base.notional_usd
        risco = base.risco_usd
        alavancagem = base.alavancagem

        fator = 1.0
        for a in achados:
            if a.severidade is Severidade.REDUCAO:
                fator *= float(a.detalhes.get("fator", 0.7))
        if fator < 1.0:
            size *= fator
            notional *= fator
            risco *= fator
            alavancagem = (notional / self.estado.capital_atual
                           if self.estado.capital_atual else alavancagem)

        analise_liq = liquidation.analisar(
            sinal.entry, sinal.stop_loss, sinal.side,
            max(alavancagem, 1.0), notional_usd=notional,
            folga_minima=self.extra.folga_liquidacao_minima,
            atr_pct=ctx.atr_pct,
            min_atr_liquidacao=self.extra.min_atr_ate_liquidacao)

        if not analise_liq.aprovado:
            achados.append(Achado(
                "protecao_de_liquidacao", Severidade.VETO,
                analise_liq.motivo, analise_liq.to_dict()))
            return DecisaoRiskEngine(
                Veredicto.REJEITADO, achados=achados,
                analise_liquidacao=analise_liq.to_dict())

        if notional < 5.0 or size <= 0:
            achados.append(Achado(
                "tamanho_minimo", Severidade.VETO,
                f"após as reduções, o notional de US$ {notional:.2f} fica "
                f"abaixo do mínimo operável de US$ 5,00"))
            return DecisaoRiskEngine(Veredicto.REJEITADO, achados=achados,
                                     analise_liquidacao=analise_liq.to_dict())

        veredicto = (Veredicto.APROVADO_COM_REDUCAO if fator < 1.0
                     else Veredicto.APROVADO)
        return DecisaoRiskEngine(
            veredicto=veredicto, size=size, notional_usd=notional,
            risco_usd=risco, alavancagem=max(alavancagem, 1.0),
            achados=achados, analise_liquidacao=analise_liq.to_dict(),
            fator_reducao=fator)

    def _checar_concentracao(self, sinal: Signal,
                             posicoes: Sequence[Position]) -> list[Achado]:
        """Concentração medida por FATOR DE RISCO, não por ticker.

        Três posições em SOL, AVAX e NEAR são três tickers e uma aposta. O
        grupo vem de `manager.grupo_de`, e o total exposto ao mesmo grupo é
        limitado em notional, não só em contagem.
        """
        achados: list[Achado] = []
        grupo = grupo_de(sinal.symbol)
        if grupo == "outros":
            return achados

        mesmo_grupo = [p for p in posicoes if grupo_de(p.symbol) == grupo]
        exposicao_grupo = sum(p.notional_usd for p in mesmo_grupo)
        capital = self.estado.capital_atual
        limite_grupo = capital * self.cfg.max_exposicao_notional_pct / 100.0 / 2

        if exposicao_grupo > limite_grupo:
            achados.append(Achado(
                "concentracao_por_fator", Severidade.VETO,
                f"exposição de US$ {exposicao_grupo:.2f} no grupo '{grupo}' "
                f"({', '.join(p.symbol for p in mesmo_grupo)}) já excede o "
                f"limite de US$ {limite_grupo:.2f}. Estes ativos se movem "
                f"juntos: são uma aposta, não várias.",
                {"grupo": grupo, "exposicao": exposicao_grupo,
                 "limite": limite_grupo}))
        elif len(mesmo_grupo) >= 1:
            achados.append(Achado(
                "concentracao_por_fator", Severidade.AVISO,
                f"{len(mesmo_grupo)} posição(ões) já no grupo '{grupo}'; a "
                f"diversificação aparente é menor do que o número de ativos "
                f"sugere.", {"grupo": grupo}))
        return achados

    # ------------------------------------------------------ registro
    def registrar_trade(self, trade: Trade, *,
                        agora_ms: int | None = None) -> None:
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        self.manager.registrar_trade(trade, agora_ms=agora)
        dia = indice_dia(agora)
        self._trades_por_dia[dia] = self._trades_por_dia.get(dia, 0) + 1
        if trade.pnl_usd < 0 and trade.motivo_saida in ("stop_loss", "manual",
                                                        "liquidacao"):
            self._ultimos_stops[(trade.symbol, trade.side.value)] = agora

    def trades_hoje(self, agora_ms: int | None = None) -> int:
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        return self._trades_por_dia.get(indice_dia(agora), 0)

    def sincronizar_capital(self, capital: float) -> None:
        self.manager.sincronizar_capital(capital)

    def status(self) -> dict[str, Any]:
        return {
            "halted": self.halted,
            "motivo_halt": (self._motivo_halt
                            or self.manager.estado.motivo_kill),
            "estado": self.manager.estado.to_dict(),
            "limites": self.cfg.to_dict(),
            "limites_extra": self.extra.to_dict(),
            "trades_hoje": self.trades_hoje(),
            "proibicoes_estruturais": [
                "martingale / aumentar tamanho após perda",
                "averaging down sem regra de pirâmide predefinida",
                "afastar o stop para evitar a perda",
                "revenge trading (reentrada imediata após stop)",
                "alavancagem que coloque a liquidação antes do stop",
                "abrir posição com dados de qualidade inadequada",
            ],
            "confirmacao_para_retomar": "RETOMAR OPERACAO",
        }
