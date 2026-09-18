"""Orquestrador: liga todas as camadas num ciclo de análise completo.

É aqui que as peças se encontram na ordem correta:

    qualidade de dados → regime → anomalias → agentes → consenso
    → Risk Engine (veto) → journal → alertas

Cada etapa pode interromper o fluxo, e o motivo fica registrado. Uma
oportunidade que morre na primeira etapa aparece no relatório com o motivo, em
vez de simplesmente não aparecer.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .agents import (
    ChiefInvestmentEngine, Consenso, ContextoAnalise, DecisaoFinal,
)
from .analysis.features import BARRAS_MINIMAS, DadosInsuficientes, SerieFeatures
from .config import Settings, tf_ms
from .data.quality import QualityReport, avaliar_serie
from .data.registry import DataKind, DataRegistry
from .data.symbols import AssetClass, SymbolError, normalizar
from .datahub import DataHub
from .models import Position, Side, Signal, SignalGrade
from .ops.anomalias import RelatorioAnomalias, detectar_anomalias
from .ops.health import HealthMonitor, RelatorioSaude
from .ops.regime import LeituraRegime, RegimeMercado, detectar_regime
from .portfolio.correlacao import matriz_correlacao, analisar_diversificacao
from .portfolio.stress import rodar_stress_test
from .reporting.alertas import (
    CentralDeAlertas, alerta_liquidacao, alerta_oportunidade,
    alerta_qualidade_dados, alerta_risco, alerta_saude,
)
from .reporting.diario import (
    BlocoDesempenho, OportunidadeRejeitada, RelatorioDiario, montar_relatorio,
)
from .reporting.journal import Journal
from .risk.engine import ContextoMercado, DecisaoRiskEngine, RiskEngine
from .validation.stats import ExpectedValue, calcular_ev

log = logging.getLogger("investai.orquestrador")


@dataclass(slots=True)
class AnaliseCompleta:
    """Resultado da análise de um símbolo, com o motivo de cada parada."""

    symbol: str
    canonical: str = ""
    asset_class: str = ""
    etapa_final: str = ""
    qualidade: QualityReport | None = None
    regime: LeituraRegime | None = None
    anomalias: RelatorioAnomalias | None = None
    consenso: Consenso | None = None
    risco: DecisaoRiskEngine | None = None
    preco: float = 0.0
    erro: str = ""

    @property
    def decisao(self) -> str:
        if self.erro:
            return "erro"
        if self.consenso is None:
            return DecisaoFinal.DADOS_INSUFICIENTES.value
        return self.consenso.decisao.value

    @property
    def operavel(self) -> bool:
        return (self.consenso is not None
                and self.consenso.decisao is DecisaoFinal.VALIDADA)

    @property
    def motivo(self) -> str:
        """Nunca devolve vazio quando existe uma decisão.

        A análise pode parar antes do consenso (qualidade de dados reprovada,
        anomalia bloqueante, histórico insuficiente). Nesses casos o motivo
        vive no relatório da etapa que barrou, não em `motivos_decisao`. Sem
        este encadeamento o painel exibiria "dados insuficientes" com
        justificativa em branco — que é pior do que não exibir nada, porque o
        operador não tem como saber se o sistema pensou ou apenas falhou.
        """
        if self.erro:
            return self.erro
        if self.consenso is not None and self.consenso.motivos_decisao:
            return self.consenso.motivos_decisao[0]
        if self.qualidade is not None and not self.qualidade.pode_gerar_sinal:
            motivos = "; ".join(self.qualidade.motivos) or "sem detalhe"
            return f"qualidade de dados reprovada: {motivos}"
        if self.anomalias is not None and self.anomalias.bloqueia_entrada:
            motivos = "; ".join(self.anomalias.motivos_de_bloqueio())
            return f"anomalia bloqueante: {motivos}"
        if self.etapa_final:
            return (f"análise interrompida na etapa '{self.etapa_final}' sem "
                    f"consenso formado")
        return "análise não executada"

    def categoria_motivo(self) -> str:
        """Agrupa o motivo para o relatório diário contar por causa."""
        if self.operavel:
            return "aprovado"
        if self.erro:
            return "erro_de_dados"
        if self.qualidade and not self.qualidade.pode_gerar_sinal:
            return "qualidade_de_dados"
        if self.anomalias and self.anomalias.bloqueia_entrada:
            return "anomalia_bloqueante"
        if self.risco is not None and not self.risco.aprovado:
            return "veto_de_risco"
        if self.consenso is None:
            return "sem_analise"
        d = self.consenso.decisao
        if d is DecisaoFinal.DADOS_INSUFICIENTES:
            return "dados_insuficientes"
        if d is DecisaoFinal.AGUARDAR_CONFIRMACAO:
            return "sem_consenso"
        if d is DecisaoFinal.OBSERVAR:
            return "score_insuficiente"
        if d is DecisaoFinal.NAO_OPERAR:
            return "score_insuficiente"
        if d is DecisaoFinal.REJEITADA_PELO_RISCO:
            return "veto_de_risco"
        return "outro"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "canonical": self.canonical,
            "asset_class": self.asset_class, "preco": self.preco,
            "etapa_final": self.etapa_final, "decisao": self.decisao,
            "operavel": self.operavel, "motivo": self.motivo,
            "categoria_motivo": self.categoria_motivo(),
            "erro": self.erro,
            "qualidade": self.qualidade.to_dict() if self.qualidade else None,
            "regime": self.regime.to_dict() if self.regime else None,
            "anomalias": (self.anomalias.to_dict()
                          if self.anomalias else None),
            "consenso": self.consenso.to_dict() if self.consenso else None,
            "risco": self.risco.to_dict() if self.risco else None,
        }


@dataclass(slots=True)
class ResultadoCiclo:
    executado_em_ms: int
    duracao_s: float = 0.0
    analises: list[AnaliseCompleta] = field(default_factory=list)
    saude: RelatorioSaude | None = None
    portfolio: dict[str, Any] = field(default_factory=dict)
    stress: dict[str, Any] = field(default_factory=dict)
    lacunas: list[str] = field(default_factory=list)

    @property
    def operaveis(self) -> list[AnaliseCompleta]:
        return [a for a in self.analises if a.operavel]

    @property
    def em_observacao(self) -> list[AnaliseCompleta]:
        return [a for a in self.analises
                if a.consenso is not None
                and a.consenso.decisao in (DecisaoFinal.OBSERVAR,
                                           DecisaoFinal.AGUARDAR_CONFIRMACAO)]

    @property
    def rejeitadas(self) -> list[AnaliseCompleta]:
        return [a for a in self.analises
                if not a.operavel and a not in self.em_observacao]

    def to_dict(self) -> dict[str, Any]:
        return {
            "executado_em_ms": self.executado_em_ms,
            "duracao_s": round(self.duracao_s, 2),
            "total_analisado": len(self.analises),
            "total_operavel": len(self.operaveis),
            "total_em_observacao": len(self.em_observacao),
            "total_rejeitado": len(self.rejeitadas),
            "saude": self.saude.to_dict() if self.saude else None,
            "portfolio": self.portfolio,
            "stress": self.stress,
            "lacunas": self.lacunas,
            "analises": [a.to_dict() for a in self.analises],
            "resumo": (
                f"{len(self.operaveis)} candidato(s) aprovado(s) de "
                f"{len(self.analises)} analisados"
                if self.operaveis else
                f"NENHUMA OPORTUNIDADE ATENDE AOS CRITÉRIOS de "
                f"{len(self.analises)} pares analisados. CAPITAL PRESERVADO."),
        }


class Orquestrador:
    def __init__(self, settings: Settings, hub: DataHub,
                 risk: RiskEngine, *,
                 registry: DataRegistry | None = None,
                 chief: ChiefInvestmentEngine | None = None,
                 monitor: HealthMonitor | None = None,
                 alertas: CentralDeAlertas | None = None,
                 journal: Journal | None = None,
                 relogio: Callable[[], int] | None = None):
        self.settings = settings
        # Relógio injetável. Em produção é o horário real; em teste é o mesmo
        # instante usado para gerar a série, senão a checagem de dados
        # desatualizados reprova uma série que está íntegra.
        self.relogio: Callable[[], int] = (
            relogio or (lambda: int(time.time() * 1000)))
        self.hub = hub
        self.risk = risk
        self.registry = registry
        self.chief = chief or ChiefInvestmentEngine()
        self.monitor = monitor
        self.alertas = alertas or CentralDeAlertas()
        self.journal = journal or Journal()
        # Estatística por símbolo/direção, alimentada pelo pipeline de
        # validação. Sem ela o agente quantitativo se abstém — de propósito.
        self.estatisticas: dict[str, ExpectedValue] = {}
        self._regime_anterior: dict[str, RegimeMercado] = {}

    # ------------------------------------------------------- estatística
    def registrar_estatistica(self, symbol: str, side: Side,
                              retornos_r: Sequence[float]) -> ExpectedValue:
        ev = calcular_ev(list(retornos_r))
        self.estatisticas[f"{symbol.upper()}:{side.value}"] = ev
        return ev

    def estatistica(self, symbol: str, side: Side) -> ExpectedValue | None:
        return self.estatisticas.get(f"{symbol.upper()}:{side.value}")

    # ----------------------------------------------------------- análise
    def analisar(self, symbol: str, *,
                 posicoes: Sequence[Position] = (),
                 contexto_extra: dict[str, Any] | None = None,
                 agora_ms: int | None = None) -> AnaliseCompleta:
        agora = agora_ms if agora_ms is not None else self.relogio()
        extra = contexto_extra or {}
        a = AnaliseCompleta(symbol=symbol.upper())

        # ------------------------------------------- 0) normalização
        try:
            sid = normalizar(symbol, venue="bitget")
            a.canonical = sid.canonical
            a.asset_class = sid.asset_class.value
        except SymbolError as exc:
            a.erro = str(exc)
            a.etapa_final = "normalizacao"
            return a

        # ------------------------------------------------ 1) dados
        cfg = self.settings.signal
        try:
            velas_por_tf = {
                tf: self.hub.candles(a.symbol, tf,
                                     limit=max(400, BARRAS_MINIMAS + 50))
                for tf in cfg.timeframes
            }
        except Exception as exc:                        # noqa: BLE001
            a.erro = f"falha ao obter dados: {exc}"
            a.etapa_final = "coleta"
            return a

        principais = velas_por_tf.get(cfg.timeframe_principal, [])
        if not principais:
            a.erro = "sem candles no timeframe principal"
            a.etapa_final = "coleta"
            return a
        a.preco = principais[-1].close

        # ------------------------------------- 2) qualidade dos dados
        a.qualidade = avaliar_serie(
            principais, tf_ms(cfg.timeframe_principal), agora_ms=agora)
        a.etapa_final = "qualidade"
        if not a.qualidade.pode_gerar_sinal:
            self.alertas.emitir(alerta_qualidade_dados(
                a.symbol, a.qualidade.motivos, ts=agora))
            return a

        # --------------------------------------------------- 3) regime
        a.regime = detectar_regime(
            principais, contexto_mercado=extra.get("contexto_mercado"))
        a.etapa_final = "regime"
        anterior = self._regime_anterior.get(a.symbol)
        if anterior is not None and anterior is not a.regime.regime:
            from .reporting.alertas import alerta_regime
            self.alertas.emitir(alerta_regime(
                a.symbol, anterior.value, a.regime.regime.value,
                a.regime.estrategias_desabilitadas, ts=agora))
        self._regime_anterior[a.symbol] = a.regime.regime

        # ------------------------------------------------ 4) anomalias
        a.anomalias = RelatorioAnomalias(a.symbol, detectar_anomalias(
            a.symbol, principais,
            derivativos=extra.get("derivativos"),
            historico_derivativos=extra.get("historico_derivativos")))
        a.etapa_final = "anomalias"
        if a.anomalias.bloqueia_entrada:
            for an in a.anomalias.anomalias:
                if an.tipo.value == "onda_de_liquidacoes":
                    self.alertas.emitir(alerta_liquidacao(
                        a.symbol, an.valor, an.desvios, ts=agora))
            return a

        # -------------------------------------------------- 5) features
        try:
            features = {
                tf: SerieFeatures(a.symbol, tf, v).at(len(v) - 1)
                for tf, v in velas_por_tf.items() if len(v) >= BARRAS_MINIMAS
            }
        except DadosInsuficientes as exc:
            a.erro = str(exc)
            a.etapa_final = "features"
            return a
        if cfg.timeframe_principal not in features:
            a.erro = (f"histórico insuficiente no timeframe principal "
                      f"({cfg.timeframe_principal})")
            a.etapa_final = "features"
            return a

        snapshot = self.hub.ticker(a.symbol)

        # --------------------------------- 6) agentes e consenso (2 lados)
        a.etapa_final = "consenso"
        melhor: Consenso | None = None
        melhor_risco: DecisaoRiskEngine | None = None

        for direcao, side in ((+1, Side.LONG), (-1, Side.SHORT)):
            ctx = ContextoAnalise(
                symbol=a.symbol, asset_class=AssetClass(a.asset_class),
                timeframe=cfg.timeframe_principal, features=features,
                snapshot=snapshot, derivativos=extra.get("derivativos"),
                noticias=extra.get("noticias"), macro=extra.get("macro"),
                fundamentos=extra.get("fundamentos"),
                estatistica=self.estatistica(a.symbol, side),
                regime=a.regime.regime.value if a.regime else "",
                qualidade_dados=a.qualidade, agora_ms=agora)

            sinal = self._montar_sinal(a.symbol, side, features[cfg.timeframe_principal])
            decisao_risco = self.risk.avaliar(
                sinal, posicoes,
                contexto=self._contexto_risco(a, snapshot, extra),
                agora_ms=agora)
            consenso = self.chief.consolidar(ctx, direcao, risco=decisao_risco)

            ordem = {DecisaoFinal.VALIDADA: 5,
                     DecisaoFinal.AGUARDAR_CONFIRMACAO: 4,
                     DecisaoFinal.OBSERVAR: 3,
                     DecisaoFinal.DADOS_INSUFICIENTES: 2,
                     DecisaoFinal.REJEITADA_PELO_RISCO: 1,
                     DecisaoFinal.NAO_OPERAR: 0}
            if melhor is None or (ordem[consenso.decisao], consenso.score) > (
                    ordem[melhor.decisao], melhor.score):
                melhor = consenso
                melhor_risco = decisao_risco

        a.consenso = melhor
        a.risco = melhor_risco
        a.etapa_final = "decisao"

        # ------------------------------------------------- 7) alertas
        if melhor is not None:
            if melhor.decisao is DecisaoFinal.VALIDADA:
                est = self.estatistica(
                    a.symbol,
                    Side.LONG if melhor.direcao == "compra" else Side.SHORT)
                self.alertas.emitir(alerta_oportunidade(
                    a.symbol, melhor.direcao, melhor.score,
                    melhor.decisao.value,
                    est.ev_liquido_r if est else 0.0,
                    est.n if est else 0, ts=agora))
            elif melhor.decisao is DecisaoFinal.REJEITADA_PELO_RISCO:
                self.alertas.emitir(alerta_risco(
                    a.symbol, melhor.motivos_decisao[0]
                    if melhor.motivos_decisao else "veto do Risk Engine",
                    ts=agora))
        return a

    def _montar_sinal(self, symbol: str, side: Side, features) -> Signal:
        from .analysis.confluence import montar_plano
        cfg = self.settings.signal
        plano = montar_plano(features, side, atr_mult_stop=cfg.atr_mult_stop,
                             alvos_r=cfg.alvos_r)
        return Signal(
            symbol=symbol, timeframe=cfg.timeframe_principal, side=side,
            grade=SignalGrade.C, score=0.0, entry=plano["entry"],
            stop_loss=plano["stop_loss"],
            take_profits=plano["take_profits"],
            risk_reward=plano["risk_reward"], atr=features.atr,
            regime=features.regime)

    def _contexto_risco(self, a: AnaliseCompleta, snapshot,
                        extra: dict[str, Any]) -> ContextoMercado:
        saude = "HEALTHY"
        if self.monitor is not None:
            rel = self.monitor.relatorio()
            saude = rel.estado_geral.value
        deriv = extra.get("derivativos") or {}
        principal = a.regime.metricas.get("atr_pct") if a.regime else None
        return ContextoMercado(
            atr_pct=principal,
            volume_24h_usd=getattr(snapshot, "volume_24h_usd", None)
            if snapshot else None,
            spread_pct=deriv.get("spread_pct"),
            minutos_ate_evento_critico=extra.get(
                "minutos_ate_evento_critico"),
            descricao_evento=extra.get("descricao_evento", ""),
            regime=a.regime.regime.value if a.regime else "",
            dados_confiaveis=bool(a.qualidade and a.qualidade.pode_gerar_sinal),
            motivo_dados="; ".join(a.qualidade.motivos) if a.qualidade else "",
            gap_tipico_pct=extra.get("gap_tipico_pct"),
            saude_sistema=saude)

    # ------------------------------------------------------------ ciclo
    def ciclo(self, symbols: Sequence[str] | None = None, *,
              posicoes: Sequence[Position] = (),
              contexto_por_symbol: dict[str, dict[str, Any]] | None = None,
              agora_ms: int | None = None) -> ResultadoCiclo:
        inicio = time.time()
        agora = agora_ms if agora_ms is not None else self.relogio()
        alvos = list(symbols or self.settings.universo)
        ctx_por_symbol = contexto_por_symbol or {}

        resultado = ResultadoCiclo(executado_em_ms=agora)

        # Saúde primeiro: se serviço crítico está fora, o Risk Engine veta
        # tudo, mas a análise continua para o relatório mostrar o motivo.
        if self.monitor is not None:
            resultado.saude = self.monitor.checar_tudo()
            if not resultado.saude.pode_abrir_posicao:
                self.alertas.emitir(alerta_saude(
                    resultado.saude.estado_geral.value,
                    resultado.saude.motivos, ts=agora))

        for sym in alvos:
            try:
                resultado.analises.append(self.analisar(
                    sym, posicoes=posicoes,
                    contexto_extra=ctx_por_symbol.get(sym.upper()),
                    agora_ms=agora))
            except Exception as exc:                    # noqa: BLE001
                log.exception("análise de %s falhou", sym)
                resultado.analises.append(AnaliseCompleta(
                    symbol=sym.upper(), erro=f"{type(exc).__name__}: {exc}",
                    etapa_final="excecao"))

        # ------------------------------------------------- portfólio
        if posicoes:
            try:
                series = {p.symbol: self.hub.candles(p.symbol, "1H", limit=500)
                          for p in posicoes}
                matriz = matriz_correlacao(series, janela=400)
                resultado.portfolio = analisar_diversificacao(
                    posicoes, matriz).to_dict()
                resultado.stress = rodar_stress_test(
                    posicoes, self.risk.estado.capital_atual).to_dict()
            except Exception as exc:                    # noqa: BLE001
                log.warning("análise de portfólio falhou: %s", exc)
                resultado.portfolio = {"erro": str(exc)}

        # -------------------------------------------------- lacunas
        if self.registry is not None:
            for classe in (AssetClass.CRIPTO, AssetClass.ACAO,
                           AssetClass.FII, AssetClass.RENDA_FIXA):
                for tipo in (DataKind.FUNDAMENTOS, DataKind.NOTICIAS,
                             DataKind.CALENDARIO_ECONOMICO,
                             DataKind.LIQUIDACOES, DataKind.CURVA_JUROS):
                    cov = self.registry.cobertura(classe, tipo)
                    if not cov.disponivel:
                        resultado.lacunas.append(
                            f"{classe.value}/{tipo.value}: {cov.mensagem}")

        resultado.duracao_s = time.time() - inicio
        return resultado

    # ------------------------------------------------- relatório diário
    def relatorio_diario(self, ciclo: ResultadoCiclo, *,
                         desempenho: Sequence[BlocoDesempenho] = (),
                         estrategias_por_fase: dict[str, list[str]] | None = None,
                         modo_de_dados: str = "") -> RelatorioDiario:
        rejeitadas = [
            OportunidadeRejeitada(
                symbol=a.symbol,
                direcao=a.consenso.direcao if a.consenso else "—",
                score=a.consenso.score if a.consenso else 0.0,
                decisao=a.decisao, motivo=a.motivo,
                categoria_motivo=a.categoria_motivo())
            for a in ciclo.rejeitadas
        ]
        return montar_relatorio(
            pares_analisados=len(ciclo.analises),
            operaveis=[{"symbol": a.symbol,
                        "direcao": a.consenso.direcao if a.consenso else "",
                        "score": round(a.consenso.score, 2) if a.consenso else 0,
                        "decisao": a.decisao}
                       for a in ciclo.operaveis],
            em_observacao=[{"symbol": a.symbol, "decisao": a.decisao}
                           for a in ciclo.em_observacao],
            rejeitadas=rejeitadas,
            risco=self.risk.estado.to_dict(),
            portfolio=ciclo.portfolio, stress=ciclo.stress,
            desempenho=desempenho,
            estrategias_por_fase=estrategias_por_fase,
            journal=self.journal.resumo_por_quadrante(),
            saude=ciclo.saude.to_dict() if ciclo.saude else None,
            regimes={a.symbol: a.regime.regime.value
                     for a in ciclo.analises if a.regime},
            anomalias=[an.to_dict() for a in ciclo.analises
                       if a.anomalias for an in a.anomalias.anomalias][:20],
            alertas_urgentes=[
                x.to_dict() for x in self.alertas.listar(limite=10)
                if x.nivel.value == "urgente"],
            lacunas=ciclo.lacunas, modo_de_dados=modo_de_dados,
            agora_ms=ciclo.executado_em_ms)
