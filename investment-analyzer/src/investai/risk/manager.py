"""Gestão de risco: o módulo que impede a conta de ser zerada.

Este é o componente mais importante do sistema. Estratégia ruim com risco
controlado perde devagar; estratégia boa com risco descontrolado zera a conta
em uma sequência ruim — que sempre chega.

Toda entrada passa por `avaliar_entrada`, que só aprova se TODOS os limites
estiverem respeitados. A resposta é auditável: diz exatamente qual regra
bloqueou.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Sequence

from ..config import RiskConfig
from ..models import Position, Signal, Trade


@dataclass(slots=True)
class DecisaoRisco:
    aprovado: bool
    motivo: str = ""
    size: float = 0.0
    notional_usd: float = 0.0
    risco_usd: float = 0.0
    alavancagem: float = 1.0
    bloqueios: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "aprovado": self.aprovado, "motivo": self.motivo,
            "size": self.size, "notional_usd": round(self.notional_usd, 2),
            "risco_usd": round(self.risco_usd, 2),
            "alavancagem": round(self.alavancagem, 2),
            "bloqueios": self.bloqueios,
        }


@dataclass(slots=True)
class EstadoRisco:
    capital_atual: float
    pico_capital: float
    pnl_dia: float = 0.0
    pnl_semana: float = 0.0
    perdas_consecutivas: int = 0
    kill_switch: bool = False
    motivo_kill: str = ""
    cooldown_ate_ms: int = 0
    # Índice do dia/semana de calendário (UTC) a que os contadores se referem.
    # Guardar o ÍNDICE, e não o instante de início, faz o limite sobreviver a
    # reinício do processo: religar o robô no mesmo dia não zera a perda diária.
    dia_indice: int = 0
    semana_indice: int = 0

    @property
    def drawdown_pct(self) -> float:
        if self.pico_capital <= 0:
            return 0.0
        return max(0.0, (self.pico_capital - self.capital_atual) / self.pico_capital * 100.0)

    def to_dict(self) -> dict:
        return {
            "capital_atual": round(self.capital_atual, 2),
            "pico_capital": round(self.pico_capital, 2),
            "drawdown_pct": round(self.drawdown_pct, 2),
            "pnl_dia": round(self.pnl_dia, 2),
            "pnl_semana": round(self.pnl_semana, 2),
            "perdas_consecutivas": self.perdas_consecutivas,
            "kill_switch": self.kill_switch,
            "motivo_kill": self.motivo_kill,
            "cooldown_ate_ms": self.cooldown_ate_ms,
            "dia_indice": self.dia_indice,
            "semana_indice": self.semana_indice,
        }


MS_POR_DIA = 86_400_000


def indice_dia(ms: int) -> int:
    """Dia de calendário em UTC. Fronteira fixa, não relativa ao boot."""
    return ms // MS_POR_DIA


def indice_semana(ms: int) -> int:
    return ms // (MS_POR_DIA * 7)


# Grupos de ativos que costumam se mover juntos. Três posições no mesmo grupo
# não são três apostas: são uma aposta com três vezes o risco.
GRUPOS_CORRELACAO: dict[str, str] = {
    "BTCUSDT": "majors", "ETHUSDT": "majors",
    "SOLUSDT": "l1_alt", "AVAXUSDT": "l1_alt", "NEARUSDT": "l1_alt",
    "APTUSDT": "l1_alt", "SUIUSDT": "l1_alt", "ADAUSDT": "l1_alt",
    "DOTUSDT": "l1_alt", "ATOMUSDT": "l1_alt", "TONUSDT": "l1_alt",
    "ARBUSDT": "l2", "OPUSDT": "l2", "MATICUSDT": "l2",
    "DOGEUSDT": "meme",
    "XRPUSDT": "pagamentos", "LTCUSDT": "pagamentos", "BNBUSDT": "exchange",
    "LINKUSDT": "oraculo", "INJUSDT": "defi",
}


def grupo_de(symbol: str) -> str:
    return GRUPOS_CORRELACAO.get(symbol.upper(), "outros")


class RiskManager:
    def __init__(self, cfg: RiskConfig, capital_inicial: float):
        if capital_inicial <= 0:
            raise ValueError("capital inicial deve ser > 0")
        self.cfg = cfg
        agora = int(time.time() * 1000)
        self.estado = EstadoRisco(
            capital_atual=capital_inicial, pico_capital=capital_inicial,
            dia_indice=indice_dia(agora), semana_indice=indice_semana(agora),
        )

    # -------------------------------------------------------------- avaliação
    def avaliar_entrada(self, sinal: Signal, posicoes: Sequence[Position],
                        *, agora_ms: int | None = None) -> DecisaoRisco:
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        e = self.estado
        cfg = self.cfg
        bloqueios: list[str] = []

        if e.kill_switch:
            bloqueios.append(f"kill switch ativo: {e.motivo_kill}")
        if agora < e.cooldown_ate_ms:
            restante = (e.cooldown_ate_ms - agora) / 60000
            bloqueios.append(f"em cooldown por {restante:.0f} min após stop")
        if e.drawdown_pct >= cfg.drawdown_max_pct:
            bloqueios.append(f"drawdown {e.drawdown_pct:.2f}% >= limite "
                             f"{cfg.drawdown_max_pct:.2f}%")
        limite_dia = -abs(e.capital_atual * cfg.perda_diaria_max_pct / 100.0)
        if e.pnl_dia <= limite_dia:
            bloqueios.append(f"perda do dia US$ {e.pnl_dia:.2f} atingiu o limite "
                             f"US$ {limite_dia:.2f}")
        limite_semana = -abs(e.capital_atual * cfg.perda_semanal_max_pct / 100.0)
        if e.pnl_semana <= limite_semana:
            bloqueios.append(f"perda da semana US$ {e.pnl_semana:.2f} atingiu o "
                             f"limite US$ {limite_semana:.2f}")
        if e.perdas_consecutivas >= cfg.perdas_consecutivas_max:
            bloqueios.append(f"{e.perdas_consecutivas} perdas consecutivas "
                             f"(máximo {cfg.perdas_consecutivas_max})")
        if len(posicoes) >= cfg.max_posicoes_simultaneas:
            bloqueios.append(f"{len(posicoes)} posições abertas (máximo "
                             f"{cfg.max_posicoes_simultaneas})")
        if any(p.symbol == sinal.symbol for p in posicoes):
            bloqueios.append(f"já existe posição aberta em {sinal.symbol}")
        if sinal.risk_reward < cfg.min_risk_reward:
            bloqueios.append(f"risco/retorno {sinal.risk_reward:.2f} < mínimo "
                             f"{cfg.min_risk_reward:.2f}")

        # Concentração por grupo correlacionado.
        grupo = grupo_de(sinal.symbol)
        mesmo_grupo = [p for p in posicoes if grupo_de(p.symbol) == grupo]
        if grupo != "outros" and len(mesmo_grupo) >= 2:
            bloqueios.append(f"{len(mesmo_grupo)} posições já no grupo "
                             f"'{grupo}' — risco concentrado")

        risco_abs = abs(sinal.entry - sinal.stop_loss)
        if risco_abs <= 0:
            bloqueios.append("distância de stop inválida (zero)")

        if bloqueios:
            return DecisaoRisco(False, bloqueios[0], bloqueios=bloqueios)

        # ------------------------------------------------------ dimensionamento
        risco_usd = e.capital_atual * cfg.risco_por_trade_pct / 100.0
        size = risco_usd / risco_abs
        notional = size * sinal.entry

        # Alavancagem é consequência do stop, não uma escolha independente.
        alavancagem = notional / e.capital_atual if e.capital_atual else 0.0
        if alavancagem > cfg.alavancagem_max:
            fator = cfg.alavancagem_max / alavancagem
            size *= fator
            notional *= fator
            risco_usd *= fator
            alavancagem = cfg.alavancagem_max

        exposicao_atual = sum(p.notional_usd for p in posicoes)
        limite_exposicao = e.capital_atual * cfg.max_exposicao_notional_pct / 100.0
        if exposicao_atual + notional > limite_exposicao:
            disponivel = max(0.0, limite_exposicao - exposicao_atual)
            if disponivel <= 0:
                return DecisaoRisco(
                    False, "limite de exposição total atingido",
                    bloqueios=[f"exposição US$ {exposicao_atual:.2f} já no limite "
                               f"US$ {limite_exposicao:.2f}"])
            fator = disponivel / notional
            size *= fator
            notional = disponivel
            risco_usd *= fator
            alavancagem = notional / e.capital_atual if e.capital_atual else 0.0

        if size <= 0 or notional < 5.0:
            return DecisaoRisco(
                False, "tamanho resultante abaixo do mínimo operável",
                bloqueios=[f"notional US$ {notional:.2f} < US$ 5,00 (mínimo Bitget)"])

        return DecisaoRisco(
            True, "aprovado", size=size, notional_usd=notional,
            risco_usd=risco_usd, alavancagem=max(alavancagem, 1.0),
        )

    # ----------------------------------------------------------- atualizações
    def registrar_trade(self, trade: Trade, *, agora_ms: int | None = None) -> None:
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        e = self.estado
        self._rolar_janelas(agora)

        e.capital_atual += trade.pnl_usd
        e.pico_capital = max(e.pico_capital, e.capital_atual)
        e.pnl_dia += trade.pnl_usd
        e.pnl_semana += trade.pnl_usd

        if trade.pnl_usd < 0:
            e.perdas_consecutivas += 1
            e.cooldown_ate_ms = agora + self.cfg.cooldown_minutos_pos_stop * 60_000
        else:
            e.perdas_consecutivas = 0

        if e.drawdown_pct >= self.cfg.drawdown_max_pct:
            self.acionar_kill_switch(
                f"drawdown de {e.drawdown_pct:.2f}% atingiu o limite de "
                f"{self.cfg.drawdown_max_pct:.2f}%")

    def sincronizar_capital(self, capital: float) -> None:
        """Alinha o estado ao saldo real da exchange (fonte da verdade)."""
        if capital <= 0:
            return
        self.estado.capital_atual = capital
        self.estado.pico_capital = max(self.estado.pico_capital, capital)

    def _rolar_janelas(self, agora_ms: int) -> None:
        """Zera os contadores quando muda o dia/semana de calendário.

        Compara ÍNDICES, não diferença de instantes: qualquer mudança de dia
        (para frente ou para trás) reinicia a janela, e dois trades no mesmo
        dia sempre somam no mesmo contador.
        """
        dia = indice_dia(agora_ms)
        if dia != self.estado.dia_indice:
            self.estado.pnl_dia = 0.0
            self.estado.dia_indice = dia
        semana = indice_semana(agora_ms)
        if semana != self.estado.semana_indice:
            self.estado.pnl_semana = 0.0
            self.estado.semana_indice = semana

    def acionar_kill_switch(self, motivo: str) -> None:
        self.estado.kill_switch = True
        self.estado.motivo_kill = motivo

    def liberar_kill_switch(self) -> None:
        """Rearme manual — de propósito. Um robô que se rearma sozinho depois
        de estourar o drawdown volta a perder pelo mesmo motivo."""
        self.estado.kill_switch = False
        self.estado.motivo_kill = ""
        self.estado.perdas_consecutivas = 0
        self.estado.cooldown_ate_ms = 0
        self.estado.pico_capital = self.estado.capital_atual
