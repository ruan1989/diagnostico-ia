"""Normalização de símbolos entre provedores.

O mesmo ativo aparece com nomes diferentes em cada lugar: `BTCUSDT` na Bitget,
`BTC-USDT` na OKX, `XBTUSD` na BitMEX, `BTCUSD` em índices spot. Tratar esses
nomes como strings soltas produz dois erros caros:

1. **Misturar mercados diferentes** — o perpétuo de BTC e o spot de BTC têm
   preços próximos mas não iguais, e basis é exatamente essa diferença. Somar
   os dois como se fossem o mesmo ativo corrompe qualquer cálculo.
2. **Dividir o mesmo ativo em dois** — medir estatística de `BTCUSDT` e de
   `BTC-USDT` separadamente corta a amostra pela metade sem nenhum motivo.

Esta camada resolve nome → identidade canônica (base, quote, mercado, venue) e
recusa adivinhar quando não tem certeza.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class AssetClass(str, Enum):
    CRIPTO = "cripto"
    ACAO = "acao"
    FII = "fii"
    ETF = "etf"
    RENDA_FIXA = "renda_fixa"
    INDICE = "indice"
    CAMBIO = "cambio"
    COMMODITY = "commodity"
    DESCONHECIDO = "desconhecido"


class MarketKind(str, Enum):
    SPOT = "spot"
    FUTURO = "futuro"              # com vencimento
    PERPETUO = "perpetuo"          # sem vencimento (funding)
    OPCAO = "opcao"
    NAO_APLICAVEL = "nao_aplicavel"


class SymbolError(ValueError):
    """Nome de símbolo que a camada se recusa a adivinhar."""


# Aliases históricos que não seguem nenhuma regra derivável.
ALIAS_BASE: dict[str, str] = {
    "XBT": "BTC",      # BitMEX e alguns feeds europeus
    "WBTC": "BTC",     # wrapped: mesmo fator de risco para fins de correlação
    "WETH": "ETH",
    "IOTA": "MIOTA",
}

# Moedas de cotação reconhecidas, da mais longa para a mais curta — a ordem
# importa: `USDT` tem que ser testado antes de `USD`, senão `BTCUSDT` viraria
# base `BTCUSD` + quote `T`.
QUOTES = ("USDT", "USDC", "BUSD", "TUSD", "FDUSD", "BRL", "USD", "EUR",
          "BTC", "ETH", "BNB")

# Sufixos de ticker da B3.
_RE_FII = re.compile(r"^[A-Z]{4}11B?$")
_RE_ACAO = re.compile(r"^[A-Z]{4}(3|4|5|6|11)$")
_RE_ETF_BR = re.compile(r"^[A-Z]{4}11$")

# ETFs brasileiros conhecidos: têm o mesmo sufixo `11` dos FIIs, então só uma
# lista explícita distingue. Adivinhar aqui classificaria BOVA11 como FII.
ETF_BR = {
    "BOVA11", "IVVB11", "SMAL11", "DIVO11", "SPXI11", "XINA11", "HASH11",
    "BITH11", "GOLD11", "IMAB11", "B5P211", "FIXA11", "NASD11", "EURP11",
}


@dataclass(frozen=True, slots=True)
class SymbolId:
    """Identidade canônica de um instrumento."""

    canonical: str                 # ex.: "CRIPTO:BTC/USDT:PERPETUO@BITGET"
    base: str
    quote: str
    asset_class: AssetClass
    market: MarketKind
    venue: str = ""
    raw: str = ""

    @property
    def par(self) -> str:
        return f"{self.base}/{self.quote}" if self.quote else self.base

    @property
    def fator_risco(self) -> str:
        """Chave de agrupamento para correlação.

        Ignora venue e tipo de mercado de propósito: uma posição no perpétuo de
        BTC na Bitget e outra no spot de BTC em outra corretora são a MESMA
        aposta, e o controle de concentração precisa vê-las como uma só.
        """
        if self.asset_class is AssetClass.CRIPTO:
            return f"cripto:{self.base}"
        return f"{self.asset_class.value}:{self.base}"

    def to_dict(self) -> dict:
        return {
            "canonical": self.canonical, "base": self.base, "quote": self.quote,
            "asset_class": self.asset_class.value, "market": self.market.value,
            "venue": self.venue, "raw": self.raw, "par": self.par,
            "fator_risco": self.fator_risco,
        }


def _dividir_cripto(bruto: str) -> tuple[str, str]:
    """Separa base e quote de um símbolo cripto colado (ex.: BTCUSDT).

    Os aliases de base são resolvidos ANTES da divisão. Sem isso, `XBTUSD`
    quebraria em base `XB` + quote `TUSD` (porque `TUSD` é uma cotação
    válida e é testada antes de `USD`), e o alias XBT→BTC nunca se aplicaria.
    Para evitar falso positivo, o alias só vale quando o que sobra é
    exatamente uma cotação conhecida.
    """
    for alias, canonico in ALIAS_BASE.items():
        if bruto.startswith(alias):
            resto = bruto[len(alias):]
            if resto in QUOTES:
                return canonico, resto

    for q in QUOTES:
        if bruto.endswith(q) and len(bruto) > len(q):
            return bruto[: -len(q)], q

    raise SymbolError(
        f"não foi possível identificar a moeda de cotação em {bruto!r}; "
        f"cotações reconhecidas: {', '.join(QUOTES)}")


def normalizar(bruto: str, *, venue: str = "",
               asset_class: AssetClass | None = None,
               market: MarketKind | None = None) -> SymbolId:
    """Converte um nome de provedor em identidade canônica.

    `asset_class` e `market` são dicas: quando o chamador sabe (porque leu de
    um endpoint de futuros, por exemplo), a dica vence a heurística.
    """
    if not bruto or not bruto.strip():
        raise SymbolError("símbolo vazio")

    limpo = bruto.strip().upper()
    venue = venue.strip().upper()

    # Já está na forma canônica? Reconstroi sem re-adivinhar.
    if ":" in limpo and "/" in limpo:
        return _parse_canonical(limpo, bruto)

    # Sufixos explícitos de mercado usados por várias exchanges.
    sufixos_perp = ("-PERP", "_PERP", "-SWAP", "_UMCBL", "-PERPETUAL")
    for suf in sufixos_perp:
        if limpo.endswith(suf):
            limpo = limpo[: -len(suf)]
            market = market or MarketKind.PERPETUO
            break

    separado = limpo.replace("-", "").replace("_", "").replace("/", "")

    # ------------------------------------------------------------ renda fixa
    if asset_class is AssetClass.RENDA_FIXA:
        return SymbolId(
            canonical=f"RENDA_FIXA:{separado}", base=separado, quote="BRL",
            asset_class=AssetClass.RENDA_FIXA,
            market=market or MarketKind.NAO_APLICAVEL, venue=venue, raw=bruto)

    # ------------------------------------------------------------------- B3
    if asset_class in (AssetClass.FII, AssetClass.ETF, AssetClass.ACAO) or (
            asset_class is None and (_RE_FII.match(separado)
                                     or _RE_ACAO.match(separado))):
        classe = asset_class or _classificar_b3(separado)
        return SymbolId(
            canonical=f"{classe.value.upper()}:{separado}/BRL:SPOT@B3",
            base=separado, quote="BRL", asset_class=classe,
            market=market or MarketKind.SPOT, venue=venue or "B3", raw=bruto)

    # --------------------------------------------------------------- cripto
    base, quote = _dividir_cripto(separado)
    base = ALIAS_BASE.get(base, base)
    mercado = market or MarketKind.SPOT
    return SymbolId(
        canonical=(f"CRIPTO:{base}/{quote}:{mercado.value.upper()}"
                   + (f"@{venue}" if venue else "")),
        base=base, quote=quote, asset_class=AssetClass.CRIPTO,
        market=mercado, venue=venue, raw=bruto)


def _classificar_b3(ticker: str) -> AssetClass:
    if ticker in ETF_BR:
        return AssetClass.ETF
    if _RE_FII.match(ticker):
        return AssetClass.FII
    if _RE_ACAO.match(ticker):
        return AssetClass.ACAO
    return AssetClass.DESCONHECIDO


def _parse_canonical(canonical: str, bruto: str) -> SymbolId:
    corpo, _, venue = canonical.partition("@")
    partes = corpo.split(":")
    if len(partes) < 3:
        raise SymbolError(f"forma canônica inválida: {canonical!r}")
    classe_txt, par, mercado_txt = partes[0], partes[1], partes[2]
    base, _, quote = par.partition("/")
    try:
        classe = AssetClass(classe_txt.lower())
        mercado = MarketKind(mercado_txt.lower())
    except ValueError as exc:
        raise SymbolError(f"classe ou mercado desconhecido em {canonical!r}") from exc
    return SymbolId(canonical=canonical, base=base, quote=quote,
                    asset_class=classe, market=mercado, venue=venue, raw=bruto)


def mesmo_instrumento(a: str, b: str, **kw) -> bool:
    """True se os dois nomes apontam para o mesmo instrumento."""
    try:
        return normalizar(a, **kw).canonical == normalizar(b, **kw).canonical
    except SymbolError:
        return False


def mesmo_fator_risco(a: str, b: str, **kw) -> bool:
    """True se os dois nomes representam a mesma aposta de risco.

    Diferente de `mesmo_instrumento`: o perpétuo e o spot de BTC são
    instrumentos distintos, mas o mesmo fator de risco.
    """
    try:
        return normalizar(a, **kw).fator_risco == normalizar(b, **kw).fator_risco
    except SymbolError:
        return False


def para_provedor(sid: SymbolId, estilo: str = "colado") -> str:
    """Converte a identidade canônica de volta ao formato de um provedor."""
    if sid.asset_class is not AssetClass.CRIPTO:
        return sid.base
    if estilo == "colado":
        return f"{sid.base}{sid.quote}"
    if estilo == "hifen":
        return f"{sid.base}-{sid.quote}"
    if estilo == "barra":
        return f"{sid.base}/{sid.quote}"
    if estilo == "underscore":
        return f"{sid.base}_{sid.quote}"
    raise ValueError(f"estilo desconhecido: {estilo!r}")
