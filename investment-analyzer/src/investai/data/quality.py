"""Procedência e qualidade de dados.

Regra central deste módulo: **nenhum número entra no sistema sem dizer de onde
veio e quando**. Um preço sem timestamp e sem fonte é indistinguível de um
número inventado, e é exatamente assim que sistemas de investimento produzem
conclusões confiantes sobre dados velhos.

O que é verificado
------------------
* **Idade** — dado com 40 minutos não pode ser apresentado como tempo real.
* **Feed congelado** — preço idêntico por N leituras consecutivas costuma ser
  conexão morta, não mercado parado.
* **Divergência entre fontes** — se duas fontes independentes discordam além do
  tolerado, nenhuma das duas é confiável até se saber qual está errada.
* **Latência** — tempo entre o timestamp do dado e o momento em que chegou.
* **Completude** — buracos na série.

Quando a qualidade cai abaixo do mínimo, o resultado não é "usar com
ressalva": é **bloquear a geração de sinais**. Dado ruim não gera decisão
cautelosa, gera decisão errada com aparência de cautela.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence


class QualityStatus(str, Enum):
    OK = "ok"
    DEGRADADO = "degradado"          # usável com ressalva explícita
    INADEQUADO = "inadequado"        # não gera sinal
    NAO_CONFIGURADO = "nao_configurado"   # fonte nunca foi conectada


# Texto exibido quando uma fonte não foi configurada. Existe como constante
# para que a interface nunca preencha o espaço com um número plausível.
FONTE_NAO_CONFIGURADA = "FONTE NÃO CONFIGURADA"


@dataclass(frozen=True, slots=True)
class DataProvenance:
    """Procedência de um dado. Acompanha o dado por todo o pipeline."""

    provider: str                  # ex.: "bitget", "brapi", "sintetico"
    venue: str                     # ex.: "BITGET", "B3"
    symbol: str                    # símbolo canônico
    timeframe: str                 # ex.: "1H", "n/a"
    timestamp_ms: int              # momento a que o dado se refere
    received_at_ms: int            # momento em que chegou ao sistema
    quality: QualityStatus = QualityStatus.OK
    motivos: tuple[str, ...] = ()
    amostras: int = 0

    @property
    def latencia_ms(self) -> int:
        """Atraso entre o dado e o recebimento. Negativo = relógio divergente."""
        return self.received_at_ms - self.timestamp_ms

    def idade_ms(self, agora_ms: int | None = None) -> int:
        agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
        return agora - self.timestamp_ms

    @property
    def confiavel(self) -> bool:
        return self.quality is QualityStatus.OK

    @property
    def gera_sinal(self) -> bool:
        """Só OK e DEGRADADO podem alimentar análise; degradado com ressalva."""
        return self.quality in (QualityStatus.OK, QualityStatus.DEGRADADO)

    def to_dict(self, agora_ms: int | None = None) -> dict[str, Any]:
        return {
            "provider": self.provider, "venue": self.venue,
            "symbol": self.symbol, "timeframe": self.timeframe,
            "timestamp_ms": self.timestamp_ms,
            "received_at_ms": self.received_at_ms,
            "latencia_ms": self.latencia_ms,
            "idade_ms": self.idade_ms(agora_ms),
            "quality": self.quality.value,
            "motivos": list(self.motivos),
            "amostras": self.amostras,
            "confiavel": self.confiavel,
            "gera_sinal": self.gera_sinal,
        }


@dataclass(slots=True)
class QualityPolicy:
    """Limiares de aceitação. Conservadores por padrão."""

    # Idade máxima em múltiplos da duração da barra. 2.5 significa: um candle
    # de 1H pode ter até 2h30 antes de virar inadequado.
    idade_max_multiplo_tf: float = 2.5
    idade_degradado_multiplo_tf: float = 1.5
    latencia_max_ms: int = 30_000
    latencia_degradado_ms: int = 5_000
    # Preço repetido por este número de leituras sugere feed congelado.
    leituras_congelado: int = 8
    # Divergência tolerada entre duas fontes independentes, em %.
    divergencia_max_pct: float = 0.5
    divergencia_degradado_pct: float = 0.15
    # Fração mínima de barras presentes na série esperada.
    completude_min: float = 0.95
    # Relógio adiantado além disso indica timestamp errado, não latência.
    relogio_futuro_max_ms: int = 5_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "idade_max_multiplo_tf": self.idade_max_multiplo_tf,
            "latencia_max_ms": self.latencia_max_ms,
            "leituras_congelado": self.leituras_congelado,
            "divergencia_max_pct": self.divergencia_max_pct,
            "completude_min": self.completude_min,
        }


@dataclass(slots=True)
class QualityReport:
    """Resultado da avaliação. `pode_gerar_sinal` é o que o motor consulta."""

    status: QualityStatus
    motivos: list[str] = field(default_factory=list)
    detalhes: dict[str, Any] = field(default_factory=dict)

    @property
    def pode_gerar_sinal(self) -> bool:
        return self.status in (QualityStatus.OK, QualityStatus.DEGRADADO)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "motivos": self.motivos,
                "pode_gerar_sinal": self.pode_gerar_sinal,
                "detalhes": self.detalhes}


def _pior(a: QualityStatus, b: QualityStatus) -> QualityStatus:
    ordem = {QualityStatus.OK: 0, QualityStatus.DEGRADADO: 1,
             QualityStatus.INADEQUADO: 2, QualityStatus.NAO_CONFIGURADO: 3}
    return a if ordem[a] >= ordem[b] else b


def avaliar_serie(candles: Sequence[Any], timeframe_ms: int, *,
                  agora_ms: int | None = None,
                  politica: QualityPolicy | None = None,
                  recebido_em_ms: int | None = None) -> QualityReport:
    """Avalia uma série de candles: idade, buracos, congelamento e ordem."""
    pol = politica or QualityPolicy()
    agora = agora_ms if agora_ms is not None else int(time.time() * 1000)
    rep = QualityReport(QualityStatus.OK)

    if not candles:
        return QualityReport(QualityStatus.INADEQUADO, ["série vazia"])

    ts = [c.ts for c in candles]
    closes = [c.close for c in candles]

    # ------------------------------------------------------------ ordenação
    if any(ts[i] >= ts[i + 1] for i in range(len(ts) - 1)):
        rep.status = QualityStatus.INADEQUADO
        rep.motivos.append("timestamps fora de ordem ou duplicados")

    # ----------------------------------------------------------- relógio
    futuro = ts[-1] - agora
    if futuro > pol.relogio_futuro_max_ms:
        rep.status = QualityStatus.INADEQUADO
        rep.motivos.append(
            f"último candle está {futuro / 1000:.0f}s no futuro — relógio ou "
            f"timestamp do provedor está errado")

    # --------------------------------------------------------------- idade
    # O candle mais recente pode estar em formação: o normal é a última barra
    # FECHADA ter até ~1 duração de atraso. Só acima disso é problema.
    idade = max(0, agora - ts[-1])
    rep.detalhes["idade_ms"] = idade
    rep.detalhes["idade_barras"] = round(idade / timeframe_ms, 2)
    if idade > timeframe_ms * pol.idade_max_multiplo_tf:
        rep.status = QualityStatus.INADEQUADO
        rep.motivos.append(
            f"dado com {idade / 60000:.0f} min ({idade / timeframe_ms:.1f} "
            f"barras) — acima do limite de {pol.idade_max_multiplo_tf} barras; "
            f"NÃO é tempo real")
    elif idade > timeframe_ms * pol.idade_degradado_multiplo_tf:
        rep.status = _pior(rep.status, QualityStatus.DEGRADADO)
        rep.motivos.append(
            f"dado com atraso de {idade / timeframe_ms:.1f} barras")

    # ----------------------------------------------------------- completude
    esperadas = ((ts[-1] - ts[0]) // timeframe_ms) + 1 if len(ts) > 1 else 1
    completude = len(ts) / esperadas if esperadas else 1.0
    rep.detalhes["completude"] = round(completude, 4)
    rep.detalhes["barras"] = len(ts)
    rep.detalhes["barras_esperadas"] = int(esperadas)
    if completude < pol.completude_min:
        faltando = int(esperadas) - len(ts)
        rep.status = _pior(rep.status, QualityStatus.DEGRADADO)
        rep.motivos.append(
            f"{faltando} barras faltando na série ({completude:.1%} de "
            f"completude)")

    # ---------------------------------------------------------- congelado
    repetidos = 1
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] == closes[i - 1]:
            repetidos += 1
        else:
            break
    rep.detalhes["closes_repetidos"] = repetidos
    if repetidos >= pol.leituras_congelado:
        rep.status = QualityStatus.INADEQUADO
        rep.motivos.append(
            f"fechamento idêntico nas últimas {repetidos} barras — feed "
            f"provavelmente congelado")

    # ------------------------------------------------------------- latência
    if recebido_em_ms is not None:
        lat = recebido_em_ms - ts[-1]
        rep.detalhes["latencia_ms"] = lat
        if lat > pol.latencia_max_ms:
            rep.status = _pior(rep.status, QualityStatus.INADEQUADO)
            rep.motivos.append(f"latência de {lat / 1000:.1f}s acima do limite")
        elif lat > pol.latencia_degradado_ms:
            rep.status = _pior(rep.status, QualityStatus.DEGRADADO)
            rep.motivos.append(f"latência de {lat / 1000:.1f}s")

    # -------------------------------------------------- valores impossíveis
    invalidos = [
        i for i, c in enumerate(candles)
        if c.low > c.high or c.low <= 0 or c.close <= 0
        or c.close > c.high or c.close < c.low
        or c.open > c.high or c.open < c.low
    ]
    if invalidos:
        rep.status = QualityStatus.INADEQUADO
        rep.motivos.append(
            f"{len(invalidos)} candles com OHLC inconsistente "
            f"(ex.: índice {invalidos[0]})")

    return rep


def comparar_fontes(precos: dict[str, float], *,
                    politica: QualityPolicy | None = None) -> QualityReport:
    """Compara o mesmo preço vindo de fontes independentes.

    Se duas fontes discordam além do tolerado, o problema não é escolher a
    "melhor": é que não se sabe qual está errada. O status vira INADEQUADO.
    """
    pol = politica or QualityPolicy()
    validos = {k: v for k, v in precos.items() if v and v > 0}

    if not validos:
        return QualityReport(QualityStatus.NAO_CONFIGURADO,
                             [FONTE_NAO_CONFIGURADA])
    if len(validos) == 1:
        fonte = next(iter(validos))
        return QualityReport(
            QualityStatus.DEGRADADO,
            [f"apenas uma fonte disponível ({fonte}) — sem confirmação cruzada"],
            {"fontes": validos, "mediana": next(iter(validos.values()))})

    mediana = statistics.median(validos.values())
    # A divergência é medida pela AMPLITUDE entre as fontes, não pelo desvio
    # contra a mediana. Com duas fontes, a mediana é o ponto médio e cada uma
    # desviaria só metade da discordância real — o que subestimaria o problema
    # pela metade exatamente no caso em que ele é mais difícil de resolver.
    fonte_min = min(validos, key=lambda k: validos[k])
    fonte_max = max(validos, key=lambda k: validos[k])
    amplitude_pct = (validos[fonte_max] - validos[fonte_min]) / mediana * 100

    desvios = {k: (v - mediana) / mediana * 100 for k, v in validos.items()}
    detalhes = {
        "fontes": validos, "mediana": mediana,
        "desvios_pct": {k: round(v, 4) for k, v in desvios.items()},
        "amplitude_pct": round(amplitude_pct, 4),
        "fonte_minima": fonte_min, "fonte_maxima": fonte_max,
    }

    if amplitude_pct > pol.divergencia_max_pct:
        return QualityReport(
            QualityStatus.INADEQUADO,
            [f"fontes divergem {amplitude_pct:.2f}% (limite "
             f"{pol.divergencia_max_pct}%): {fonte_min} em "
             f"{validos[fonte_min]:.6g} contra {fonte_max} em "
             f"{validos[fonte_max]:.6g} — não é possível saber qual está "
             f"correta"], detalhes)
    if amplitude_pct > pol.divergencia_degradado_pct:
        return QualityReport(
            QualityStatus.DEGRADADO,
            [f"fontes divergem {amplitude_pct:.2f}% ({fonte_min} vs "
             f"{fonte_max})"], detalhes)
    return QualityReport(QualityStatus.OK, [], detalhes)


class FreezeDetector:
    """Detecta feed congelado observando leituras sucessivas em tempo real.

    Complementa `avaliar_serie`: um candle histórico pode legitimamente repetir
    o fechamento, mas o *ticker* repetir o mesmo preço dezenas de vezes
    seguidas em mercado aberto é conexão morta.
    """

    def __init__(self, limite: int = 8):
        self.limite = limite
        self._ultimo: dict[str, tuple[float, int]] = {}

    def observar(self, symbol: str, preco: float) -> QualityReport:
        anterior = self._ultimo.get(symbol)
        if anterior and anterior[0] == preco:
            contagem = anterior[1] + 1
        else:
            contagem = 1
        self._ultimo[symbol] = (preco, contagem)

        if contagem >= self.limite:
            return QualityReport(
                QualityStatus.INADEQUADO,
                [f"preço de {symbol} idêntico em {contagem} leituras "
                 f"consecutivas — feed congelado"],
                {"leituras_repetidas": contagem})
        if contagem >= max(2, self.limite // 2):
            return QualityReport(
                QualityStatus.DEGRADADO,
                [f"preço de {symbol} repetido em {contagem} leituras"],
                {"leituras_repetidas": contagem})
        return QualityReport(QualityStatus.OK, [],
                             {"leituras_repetidas": contagem})

    def reset(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._ultimo.clear()
        else:
            self._ultimo.pop(symbol, None)
