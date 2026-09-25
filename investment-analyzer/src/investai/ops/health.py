"""Health monitor dos serviços críticos.

Regra: **serviço crítico OFFLINE impede abrir posição nova.**

O raciocínio é assimétrico de propósito. Se o feed de preços cai, uma posição
aberta continua exposta e precisa ser gerenciada — o sistema tenta fechar, não
abre mais nada. Abrir posição enquanto não se consegue acompanhar o preço é
assumir risco que não se pode medir.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class EstadoSaude(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    DESCONHECIDO = "DESCONHECIDO"


@dataclass(slots=True)
class Componente:
    nome: str
    critico: bool
    descricao: str = ""
    checar: Callable[[], bool] | None = None
    estado: EstadoSaude = EstadoSaude.DESCONHECIDO
    ultimo_check_ms: int = 0
    ultimo_erro: str = ""
    latencia_ms: float | None = None
    falhas_consecutivas: int = 0
    # Após este número de falhas seguidas, DEGRADED vira OFFLINE.
    falhas_para_offline: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "nome": self.nome, "critico": self.critico,
            "descricao": self.descricao, "estado": self.estado.value,
            "ultimo_check_ms": self.ultimo_check_ms,
            "ultimo_erro": self.ultimo_erro,
            "latencia_ms": (round(self.latencia_ms, 1)
                            if self.latencia_ms is not None else None),
            "falhas_consecutivas": self.falhas_consecutivas,
        }


@dataclass(slots=True)
class RelatorioSaude:
    estado_geral: EstadoSaude
    componentes: list[Componente] = field(default_factory=list)
    pode_abrir_posicao: bool = True
    pode_gerenciar_posicao: bool = True
    motivos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "estado_geral": self.estado_geral.value,
            "pode_abrir_posicao": self.pode_abrir_posicao,
            "pode_gerenciar_posicao": self.pode_gerenciar_posicao,
            "motivos": self.motivos,
            "componentes": [c.to_dict() for c in self.componentes],
            "observacao": "serviço crítico OFFLINE impede ABRIR posição; "
                          "gerenciar posição aberta continua sendo tentado, "
                          "porque a exposição não desaparece junto com o feed",
        }


class HealthMonitor:
    def __init__(self) -> None:
        self.componentes: dict[str, Componente] = {}

    def registrar(self, componente: Componente) -> None:
        self.componentes[componente.nome] = componente

    def registrar_simples(self, nome: str, critico: bool,
                          checar: Callable[[], bool],
                          descricao: str = "") -> None:
        self.registrar(Componente(nome=nome, critico=critico,
                                  descricao=descricao, checar=checar))

    def marcar(self, nome: str, estado: EstadoSaude, erro: str = "") -> None:
        if c := self.componentes.get(nome):
            c.estado = estado
            c.ultimo_erro = erro
            c.ultimo_check_ms = int(time.time() * 1000)

    def checar_tudo(self) -> RelatorioSaude:
        agora = int(time.time() * 1000)
        for c in self.componentes.values():
            if c.checar is None:
                continue
            inicio = time.perf_counter()
            try:
                ok = bool(c.checar())
                erro = ""
            except Exception as exc:                # noqa: BLE001
                ok = False
                erro = f"{type(exc).__name__}: {exc}"
            c.latencia_ms = (time.perf_counter() - inicio) * 1000.0
            c.ultimo_check_ms = agora
            c.ultimo_erro = erro

            if ok:
                c.falhas_consecutivas = 0
                c.estado = EstadoSaude.HEALTHY
            else:
                c.falhas_consecutivas += 1
                # Uma falha isolada degrada; falhas repetidas derrubam. Isso
                # evita que um timeout momentâneo pare o sistema, e ao mesmo
                # tempo não deixa um serviço morto passar por "degradado".
                c.estado = (EstadoSaude.OFFLINE
                            if c.falhas_consecutivas >= c.falhas_para_offline
                            else EstadoSaude.DEGRADED)
        return self.relatorio()

    def relatorio(self) -> RelatorioSaude:
        comps = list(self.componentes.values())
        criticos = [c for c in comps if c.critico]
        motivos: list[str] = []

        criticos_offline = [c for c in criticos
                            if c.estado is EstadoSaude.OFFLINE]
        criticos_degradados = [c for c in criticos
                               if c.estado is EstadoSaude.DEGRADED]
        criticos_desconhecidos = [c for c in criticos
                                  if c.estado is EstadoSaude.DESCONHECIDO]
        nao_criticos_ruins = [
            c for c in comps
            if not c.critico and c.estado in (EstadoSaude.OFFLINE,
                                              EstadoSaude.DEGRADED)]

        if criticos_offline:
            estado = EstadoSaude.OFFLINE
            for c in criticos_offline:
                motivos.append(
                    f"{c.nome} OFFLINE após {c.falhas_consecutivas} falhas"
                    + (f": {c.ultimo_erro}" if c.ultimo_erro else ""))
        elif criticos_desconhecidos:
            estado = EstadoSaude.DEGRADED
            motivos.append(
                f"{len(criticos_desconhecidos)} componente(s) crítico(s) "
                f"nunca verificado(s): "
                f"{', '.join(c.nome for c in criticos_desconhecidos)}")
        elif criticos_degradados:
            estado = EstadoSaude.DEGRADED
            for c in criticos_degradados:
                motivos.append(f"{c.nome} degradado: "
                               f"{c.ultimo_erro or 'falha na verificação'}")
        elif nao_criticos_ruins:
            estado = EstadoSaude.DEGRADED
            motivos.append(
                f"componentes não críticos com problema: "
                f"{', '.join(c.nome for c in nao_criticos_ruins)}")
        elif not comps:
            estado = EstadoSaude.DESCONHECIDO
            motivos.append("nenhum componente registrado no monitor")
        else:
            estado = EstadoSaude.HEALTHY

        pode_abrir = estado is EstadoSaude.HEALTHY or (
            estado is EstadoSaude.DEGRADED and not criticos_offline)
        if estado is EstadoSaude.OFFLINE:
            pode_abrir = False
            motivos.append(
                "NÃO ABRIR POSIÇÃO: sem serviço crítico não é possível "
                "acompanhar preço nem executar stop de forma confiável")
        if estado is EstadoSaude.DESCONHECIDO:
            pode_abrir = False
            motivos.append(
                "NÃO ABRIR POSIÇÃO: estado do sistema não verificado")

        return RelatorioSaude(
            estado_geral=estado, componentes=comps,
            pode_abrir_posicao=pode_abrir,
            # Gerenciar é sempre tentado: a exposição não desaparece.
            pode_gerenciar_posicao=True, motivos=motivos)


def monitor_padrao(*, checar_dados: Callable[[], bool] | None = None,
                   checar_exchange: Callable[[], bool] | None = None,
                   checar_banco: Callable[[], bool] | None = None,
                   checar_risco: Callable[[], bool] | None = None,
                   checar_noticias: Callable[[], bool] | None = None
                   ) -> HealthMonitor:
    """Monitor com os componentes que este sistema realmente tem."""
    m = HealthMonitor()
    m.registrar(Componente(
        nome="feed_de_mercado", critico=True,
        descricao="candles e ticker do provedor de dados",
        checar=checar_dados))
    m.registrar(Componente(
        nome="exchange", critico=True,
        descricao="conectividade e credencial da exchange (só exigida em "
                  "modo real)",
        checar=checar_exchange))
    m.registrar(Componente(
        nome="banco_de_dados", critico=True,
        descricao="SQLite de sinais, operações e auditoria",
        checar=checar_banco))
    m.registrar(Componente(
        nome="motor_de_risco", critico=True,
        descricao="Risk Engine respondendo e sem halt",
        checar=checar_risco))
    m.registrar(Componente(
        nome="feed_de_noticias", critico=False,
        descricao="agregador de notícias; sem ele o agente de notícias se "
                  "abstém",
        checar=checar_noticias))
    return m
