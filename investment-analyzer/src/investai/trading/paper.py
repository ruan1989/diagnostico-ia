"""Simulador de execução realista.

A premissa que este módulo recusa
--------------------------------
"O preço tocou meu limite, então minha ordem executou." Não necessariamente. O
preço tocar um nível significa que houve *pelo menos uma* negociação ali. Se a
sua ordem estava atrás de outras 400 na fila, ela não executou. Backtests que
assumem fill garantido em ordem limitada superestimam sistematicamente o
resultado — e o erro aparece só quando o dinheiro é real.

O que este simulador modela
---------------------------
* **fila de ordens limitadas** — só executa se o preço ATRAVESSAR o nível, ou
  se negociou nele com volume suficiente para consumir a fila estimada;
* **profundidade do livro** — ordem grande em relação ao topo do livro executa
  em partes, a preços progressivamente piores;
* **spread** — compra no ask, vende no bid, sempre;
* **latência** — a ordem chega alguns instantes depois da decisão, e o preço
  pode ter andado;
* **slippage por tamanho** — cresce com a fração do livro consumida;
* **taxas** — maker e taker corretas por tipo de execução;
* **funding** — cobrado por período de 8h em perpétuos.

Nenhum desses efeitos ajuda o operador. É assim de propósito.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..models import Candle, Side


class TipoOrdem(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"                # stop de mercado (gatilho → market)
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"


class StatusOrdem(str, Enum):
    PENDENTE = "pendente"
    PARCIAL = "parcialmente_executada"
    EXECUTADA = "executada"
    CANCELADA = "cancelada"
    REJEITADA = "rejeitada"
    EXPIRADA = "expirada"


@dataclass(slots=True)
class LivroSintetico:
    """Aproximação de profundidade quando não há livro real.

    Enquanto o conector de order book não existir (ver `data/registry.py`), o
    simulador usa este modelo — e o declara explicitamente em vez de fingir
    que tem livro real.
    """

    spread_pct: float = 0.02
    # Volume disponível no topo do livro, em USD.
    profundidade_topo_usd: float = 25_000.0
    # Quanto o preço anda por cada múltiplo da profundidade do topo consumido.
    impacto_por_nivel_pct: float = 0.03
    niveis: int = 10

    def preco_execucao(self, referencia: float, side: Side,
                       notional_usd: float) -> tuple[float, float]:
        """Preço médio de execução e fração preenchida.

        Consome o livro nível a nível. Uma ordem que exige mais que
        `niveis × profundidade_topo` não preenche por completo — o que é o
        comportamento real em ativo ilíquido.
        """
        if notional_usd <= 0:
            return referencia, 0.0

        meio_spread = referencia * self.spread_pct / 200.0
        base = referencia + meio_spread if side is Side.LONG else referencia - meio_spread

        capacidade = self.profundidade_topo_usd * self.niveis
        preenchido = min(notional_usd, capacidade)
        fracao = preenchido / notional_usd

        # Preço médio ponderado ao longo dos níveis consumidos.
        niveis_consumidos = preenchido / self.profundidade_topo_usd
        # Impacto médio é metade do impacto do último nível tocado.
        impacto_medio_pct = (self.impacto_por_nivel_pct
                             * max(0.0, niveis_consumidos - 1.0) / 2.0)
        fator = impacto_medio_pct / 100.0
        preco = base * (1 + fator) if side is Side.LONG else base * (1 - fator)
        return preco, fracao

    def to_dict(self) -> dict[str, Any]:
        return {
            "tipo": "livro_sintetico",
            "spread_pct": self.spread_pct,
            "profundidade_topo_usd": self.profundidade_topo_usd,
            "impacto_por_nivel_pct": self.impacto_por_nivel_pct,
            "niveis": self.niveis,
            "aviso": "modelo aproximado; não é profundidade real de livro",
        }


@dataclass(slots=True)
class ConfigSimulador:
    taxa_maker_pct: float = 0.02
    taxa_taker_pct: float = 0.06
    # Latência entre a decisão e a chegada da ordem à exchange.
    latencia_ms_min: int = 80
    latencia_ms_max: int = 450
    # Fração da fila à frente da ordem limitada que precisa ser consumida
    # para que ela execute quando o preço apenas TOCA o nível.
    fila_fracao_volume: float = 0.35
    funding_intervalo_ms: int = 8 * 3600 * 1000
    permitir_fill_parcial: bool = True
    seed: int = 20240918

    def to_dict(self) -> dict[str, Any]:
        return {
            "taxa_maker_pct": self.taxa_maker_pct,
            "taxa_taker_pct": self.taxa_taker_pct,
            "latencia_ms": [self.latencia_ms_min, self.latencia_ms_max],
            "fila_fracao_volume": self.fila_fracao_volume,
            "permitir_fill_parcial": self.permitir_fill_parcial,
        }


@dataclass(slots=True)
class Ordem:
    id: str
    symbol: str
    side: Side
    tipo: TipoOrdem
    size: float
    preco_limite: float | None = None
    preco_gatilho: float | None = None
    reduce_only: bool = False
    criada_em_ms: int = 0
    chega_em_ms: int = 0          # criada + latência
    status: StatusOrdem = StatusOrdem.PENDENTE
    size_executada: float = 0.0
    preco_medio: float = 0.0
    taxas_usd: float = 0.0
    gatilho_disparado: bool = False
    motivo: str = ""
    fills: list[dict[str, Any]] = field(default_factory=list)

    @property
    def restante(self) -> float:
        return max(0.0, self.size - self.size_executada)

    @property
    def finalizada(self) -> bool:
        return self.status in (StatusOrdem.EXECUTADA, StatusOrdem.CANCELADA,
                               StatusOrdem.REJEITADA, StatusOrdem.EXPIRADA)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "symbol": self.symbol, "side": self.side.value,
            "tipo": self.tipo.value, "size": self.size,
            "preco_limite": self.preco_limite,
            "preco_gatilho": self.preco_gatilho,
            "reduce_only": self.reduce_only,
            "criada_em_ms": self.criada_em_ms, "chega_em_ms": self.chega_em_ms,
            "status": self.status.value,
            "size_executada": round(self.size_executada, 10),
            "preco_medio": self.preco_medio,
            "taxas_usd": round(self.taxas_usd, 6),
            "gatilho_disparado": self.gatilho_disparado,
            "motivo": self.motivo, "fills": self.fills,
        }


class SimuladorExecucao:
    """Processa ordens contra candles, com as fricções reais."""

    def __init__(self, cfg: ConfigSimulador | None = None,
                 livro: LivroSintetico | None = None):
        self.cfg = cfg or ConfigSimulador()
        self.livro = livro or LivroSintetico()
        self._rnd = random.Random(self.cfg.seed)
        self._seq = 0
        self.ordens: dict[str, Ordem] = {}

    # ------------------------------------------------------------- registro
    def _novo_id(self) -> str:
        self._seq += 1
        return f"sim-{self._seq:06d}"

    def enviar(self, symbol: str, side: Side, tipo: TipoOrdem, size: float, *,
               preco_limite: float | None = None,
               preco_gatilho: float | None = None,
               reduce_only: bool = False,
               agora_ms: int = 0) -> Ordem:
        """Registra a ordem. Ela só pode executar depois da latência."""
        oid = self._novo_id()
        latencia = self._rnd.randint(self.cfg.latencia_ms_min,
                                     self.cfg.latencia_ms_max)
        ordem = Ordem(id=oid, symbol=symbol, side=side, tipo=tipo, size=size,
                      preco_limite=preco_limite, preco_gatilho=preco_gatilho,
                      reduce_only=reduce_only, criada_em_ms=agora_ms,
                      chega_em_ms=agora_ms + latencia)

        if size <= 0:
            ordem.status = StatusOrdem.REJEITADA
            ordem.motivo = "tamanho deve ser > 0"
        elif tipo is TipoOrdem.LIMIT and preco_limite is None:
            ordem.status = StatusOrdem.REJEITADA
            ordem.motivo = "ordem limitada exige preco_limite"
        elif tipo in (TipoOrdem.STOP, TipoOrdem.STOP_LIMIT,
                      TipoOrdem.TAKE_PROFIT) and preco_gatilho is None:
            ordem.status = StatusOrdem.REJEITADA
            ordem.motivo = f"ordem {tipo.value} exige preco_gatilho"
        elif tipo is TipoOrdem.STOP_LIMIT and preco_limite is None:
            ordem.status = StatusOrdem.REJEITADA
            ordem.motivo = "stop-limit exige preco_limite além do gatilho"

        self.ordens[oid] = ordem
        return ordem

    def cancelar(self, ordem_id: str, motivo: str = "cancelada") -> bool:
        ordem = self.ordens.get(ordem_id)
        if ordem is None or ordem.finalizada:
            return False
        ordem.status = StatusOrdem.CANCELADA
        ordem.motivo = motivo
        return True

    def pendentes(self, symbol: str | None = None) -> list[Ordem]:
        return [o for o in self.ordens.values()
                if not o.finalizada
                and (symbol is None or o.symbol == symbol)]

    # -------------------------------------------------------- processamento
    def processar_candle(self, candle: Candle, *,
                         volume_usd: float | None = None,
                         volume_em_usd: bool = True,
                         duracao_ms: int = 60_000) -> list[Ordem]:
        """Aplica um candle a todas as ordens pendentes.

        `volume_usd` é o volume negociado na barra em dólares. Quando não
        informado, cai para `candle.volume`, e `volume_em_usd` diz como
        interpretá-lo: True trata o campo como notional (é o caso do provider
        deste projeto e do campo `usdtVolume` da Bitget); False multiplica
        pelo preço típico, para feeds que reportam volume em unidades do ativo
        base.

        A alternativa — adivinhar pela magnitude do número — daria errado em
        silêncio justamente nos ativos de preço muito alto ou muito baixo.

        Devolve as ordens que tiveram execução (total ou parcial) nesta barra.
        """
        if volume_usd is not None:
            vol_usd = volume_usd
        elif volume_em_usd:
            vol_usd = candle.volume
        else:
            vol_usd = candle.volume * candle.typical
        executadas: list[Ordem] = []

        for ordem in list(self.ordens.values()):
            if ordem.finalizada:
                continue
            # Latência: a ordem ainda não chegou à exchange nesta barra.
            if ordem.chega_em_ms > candle.ts + duracao_ms:
                continue
            if self._processar_ordem(ordem, candle, vol_usd):
                executadas.append(ordem)
        return executadas

    def _processar_ordem(self, ordem: Ordem, candle: Candle,
                         vol_usd: float) -> bool:
        tipo = ordem.tipo

        # -------------------------------------------------------- gatilhos
        if tipo in (TipoOrdem.STOP, TipoOrdem.STOP_LIMIT,
                    TipoOrdem.TAKE_PROFIT) and not ordem.gatilho_disparado:
            if not self._gatilho_atingido(ordem, candle):
                return False
            ordem.gatilho_disparado = True
            if tipo in (TipoOrdem.STOP, TipoOrdem.TAKE_PROFIT):
                # Vira ordem a mercado no preço do gatilho, com slippage.
                return self._executar_mercado(
                    ordem, candle, vol_usd,
                    referencia=ordem.preco_gatilho or candle.close,
                    taker=True)
            # STOP_LIMIT vira limitada; pode não executar nesta barra.

        if tipo is TipoOrdem.MARKET:
            return self._executar_mercado(ordem, candle, vol_usd,
                                          referencia=candle.open, taker=True)

        if tipo in (TipoOrdem.LIMIT, TipoOrdem.STOP_LIMIT):
            return self._executar_limitada(ordem, candle, vol_usd)

        return False

    def _gatilho_atingido(self, ordem: Ordem, candle: Candle) -> bool:
        g = ordem.preco_gatilho
        if g is None:
            return False
        if ordem.tipo is TipoOrdem.TAKE_PROFIT:
            # TP de uma posição long dispara na alta; de short, na baixa.
            return (candle.high >= g if ordem.side is Side.SHORT
                    else candle.low <= g) if ordem.reduce_only else (
                candle.high >= g or candle.low <= g)
        # STOP: dispara quando o preço atravessa o nível em qualquer direção
        # relevante ao lado da ordem.
        if ordem.side is Side.SHORT:      # stop de uma posição long
            return candle.low <= g
        return candle.high >= g

    def _executar_mercado(self, ordem: Ordem, candle: Candle, vol_usd: float,
                          *, referencia: float, taker: bool) -> bool:
        notional_pedido = ordem.restante * referencia
        preco, fracao = self.livro.preco_execucao(referencia, ordem.side,
                                                  notional_pedido)
        if fracao <= 0:
            return False
        if not self.cfg.permitir_fill_parcial and fracao < 1.0:
            ordem.status = StatusOrdem.REJEITADA
            ordem.motivo = (f"livro raso: só {fracao:.0%} do tamanho pedido "
                            f"caberia, e fill parcial está desabilitado")
            return False

        # O preço de execução não pode sair da faixa do candle: o mercado não
        # negociou fora dela.
        preco = max(candle.low, min(candle.high, preco))
        size = ordem.restante * fracao
        self._registrar_fill(ordem, size, preco, candle.ts, taker=taker,
                             nota="mercado")
        return True

    def _executar_limitada(self, ordem: Ordem, candle: Candle,
                           vol_usd: float) -> bool:
        limite = ordem.preco_limite
        if limite is None:
            return False
        comprando = ordem.side is Side.LONG

        # Atravessou o nível? Execução garantida (havia contraparte melhor).
        atravessou = (candle.low < limite if comprando
                      else candle.high > limite)
        # Apenas tocou? Depende de a fila à frente ter sido consumida.
        tocou = (candle.low <= limite <= candle.high)

        if not tocou:
            return False

        if atravessou:
            preco = limite
            fracao_fila = 1.0
        else:
            # O preço tocou o nível sem atravessar: a ordem está na fila.
            # Só executa se o volume negociado na barra for grande o
            # suficiente para consumir a fila estimada à frente dela.
            notional_ordem = ordem.restante * limite
            fila_estimada = notional_ordem / max(self.cfg.fila_fracao_volume,
                                                 1e-9)
            if vol_usd < fila_estimada:
                fracao_fila = max(0.0, vol_usd / fila_estimada)
                if fracao_fila <= 0.01:
                    return False
                if not self.cfg.permitir_fill_parcial:
                    return False
            else:
                fracao_fila = 1.0
            preco = limite

        size = ordem.restante * fracao_fila
        if size <= 0:
            return False
        self._registrar_fill(ordem, size, preco, candle.ts, taker=False,
                             nota="limitada atravessada" if atravessou
                             else f"limitada na fila ({fracao_fila:.0%})")
        return True

    def _registrar_fill(self, ordem: Ordem, size: float, preco: float,
                        ts: int, *, taker: bool, nota: str) -> None:
        taxa_pct = (self.cfg.taxa_taker_pct if taker
                    else self.cfg.taxa_maker_pct)
        taxa = size * preco * taxa_pct / 100.0

        total_anterior = ordem.size_executada * ordem.preco_medio
        ordem.size_executada += size
        ordem.preco_medio = ((total_anterior + size * preco)
                             / ordem.size_executada)
        ordem.taxas_usd += taxa
        ordem.fills.append({
            "ts": ts, "size": round(size, 10), "preco": preco,
            "taxa_usd": round(taxa, 6),
            "tipo_taxa": "taker" if taker else "maker", "nota": nota,
        })
        ordem.status = (StatusOrdem.EXECUTADA
                        if ordem.restante <= 1e-12 else StatusOrdem.PARCIAL)

    # ------------------------------------------------------------- funding
    def custo_funding(self, notional_usd: float, side: Side,
                      abertura_ms: int, agora_ms: int,
                      taxa_funding: float) -> float:
        periodos = max(0, int((agora_ms - abertura_ms)
                              // self.cfg.funding_intervalo_ms))
        if periodos == 0:
            return 0.0
        direcao = 1.0 if side is Side.LONG else -1.0
        return notional_usd * taxa_funding * periodos * direcao

    def resumo(self) -> dict[str, Any]:
        por_status: dict[str, int] = {}
        for o in self.ordens.values():
            por_status[o.status.value] = por_status.get(o.status.value, 0) + 1
        return {
            "total_ordens": len(self.ordens),
            "por_status": por_status,
            "taxas_totais_usd": round(
                sum(o.taxas_usd for o in self.ordens.values()), 4),
            "config": self.cfg.to_dict(),
            "modelo_de_livro": self.livro.to_dict(),
        }
