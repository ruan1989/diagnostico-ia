"""Fontes de dados de FII.

Aviso importante sobre dados
----------------------------
Indicadores fundamentais de FII (P/VP, vacância, número de imóveis, PL) NÃO
estão disponíveis de forma confiável e gratuita em uma única API. O sistema
trabalha com duas fontes:

1. `BrapiFiiProvider` — busca preço e, quando disponível, dividend yield na
   brapi.dev. Serve para manter PREÇO atualizado.
2. `SnapshotFiiProvider` — lê um arquivo JSON local com os fundamentais.

O arquivo de snapshot que acompanha o projeto é um MODELO com valores de
referência para você testar o sistema. Ele carrega o campo
`atualizado_em` e, se estiver desatualizado além de `max_dias`, o provider
marca cada fundo com a origem "snapshot_desatualizado" e a API devolve
aviso. Antes de qualquer aporte real, atualize os fundamentais na fonte
primária: o relatório gerencial mensal do fundo e os informes na B3
(fundos.net). Nenhum número deste arquivo deve ser tratado como cotação ou
demonstrativo oficial.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from ..models import FiiOpportunity

log = logging.getLogger("investai.fii")

CAMPOS_OBRIGATORIOS = {"ticker", "nome", "segmento", "preco", "dy_12m", "p_vp"}


class FiiProvider(Protocol):
    def fundos(self, tickers: list[str] | None = None) -> list[FiiOpportunity]: ...


class SnapshotFiiProvider:
    """Lê fundamentais de um JSON local mantido pelo usuário."""

    def __init__(self, caminho: str | Path, max_dias: int = 45):
        self.caminho = Path(caminho)
        self.max_dias = max_dias
        self._meta: dict[str, Any] = {}

    @property
    def metadados(self) -> dict[str, Any]:
        return dict(self._meta)

    def _idade_dias(self, atualizado_em: str) -> int | None:
        try:
            d = datetime.strptime(atualizado_em, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
        return (date.today() - d).days

    def fundos(self, tickers: list[str] | None = None) -> list[FiiOpportunity]:
        if not self.caminho.exists():
            log.warning("snapshot de FII não encontrado: %s", self.caminho)
            self._meta = {"erro": f"arquivo não encontrado: {self.caminho}"}
            return []
        try:
            payload = json.loads(self.caminho.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self._meta = {"erro": f"JSON inválido em {self.caminho}: {exc}"}
            return []

        atualizado_em = payload.get("atualizado_em", "")
        idade = self._idade_dias(atualizado_em)
        desatualizado = idade is None or idade > self.max_dias
        self._meta = {
            "fonte": payload.get("fonte", "snapshot local"),
            "atualizado_em": atualizado_em,
            "idade_dias": idade,
            "desatualizado": desatualizado,
            "observacao": payload.get("observacao", ""),
        }
        if desatualizado:
            log.warning("snapshot de FII com %s dias — atualize os fundamentais",
                        idade if idade is not None else "?")

        alvos = {t.strip().upper() for t in tickers} if tickers else None
        origem = "snapshot_desatualizado" if desatualizado else "snapshot"
        out: list[FiiOpportunity] = []
        for item in payload.get("fundos", []):
            faltando = CAMPOS_OBRIGATORIOS - set(item)
            if faltando:
                log.warning("fundo ignorado (campos faltando: %s): %s",
                            sorted(faltando), item.get("ticker", "?"))
                continue
            ticker = str(item["ticker"]).upper()
            if alvos and ticker not in alvos:
                continue
            out.append(FiiOpportunity(
                ticker=ticker,
                nome=str(item["nome"]),
                segmento=str(item["segmento"]),
                preco=float(item["preco"]),
                dy_12m=float(item["dy_12m"]),
                p_vp=float(item["p_vp"]),
                vacancia_pct=(None if item.get("vacancia_pct") is None
                              else float(item["vacancia_pct"])),
                liquidez_diaria=float(item.get("liquidez_diaria", 0.0)),
                num_imoveis=(None if item.get("num_imoveis") is None
                             else int(item["num_imoveis"])),
                patrimonio_liquido=(None if item.get("patrimonio_liquido") is None
                                    else float(item["patrimonio_liquido"])),
                fonte=f"{origem} ({atualizado_em or 'sem data'})",
            ))
        return out


class BrapiFiiProvider:
    """Atualiza PREÇO (e DY quando exposto) via brapi.dev.

    Usa um provider de fundamentais como base e sobrescreve apenas o que
    consegue confirmar na API — nunca inventa o que não vem.
    """

    BASE = "https://brapi.dev/api"

    def __init__(self, base: FiiProvider, token: str = "", timeout: float = 12.0,
                 client: Any = None):
        self.base_provider = base
        self.token = token
        self.timeout = timeout
        self._client = client
        self.ultimo_erro = ""

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        import httpx
        if self.token:
            params = {**params, "token": self.token}
        cliente = self._client or httpx.Client(timeout=self.timeout)
        try:
            resp = cliente.get(f"{self.BASE}{path}", params=params)
            if resp.status_code != 200:
                self.ultimo_erro = f"HTTP {resp.status_code} em {path}"
                return None
            return resp.json()
        except Exception as exc:                        # noqa: BLE001
            self.ultimo_erro = f"{type(exc).__name__}: {exc}"
            return None
        finally:
            if self._client is None:
                cliente.close()

    def fundos(self, tickers: list[str] | None = None) -> list[FiiOpportunity]:
        fundos = self.base_provider.fundos(tickers)
        if not fundos:
            return fundos
        lote = ",".join(f.ticker for f in fundos)
        dados = self._get(f"/quote/{lote}", {"range": "1d", "interval": "1d"})
        if not dados:
            log.warning("preços da brapi indisponíveis (%s); mantendo snapshot",
                        self.ultimo_erro)
            return fundos

        por_ticker = {
            str(r.get("symbol", "")).upper(): r
            for r in dados.get("results", []) if r.get("symbol")
        }
        for f in fundos:
            r = por_ticker.get(f.ticker)
            if not r:
                continue
            preco = r.get("regularMarketPrice")
            if isinstance(preco, (int, float)) and preco > 0:
                antigo = f.preco
                f.preco = float(preco)
                # DY e P/VP são derivados do preço: reescala para manter
                # coerência em vez de misturar preço novo com múltiplo velho.
                if antigo > 0:
                    fator = antigo / f.preco
                    f.dy_12m = round(f.dy_12m * fator, 3)
                    f.p_vp = round(f.p_vp / fator, 4)
                f.fonte = f"{f.fonte} + preço brapi"
            vol = r.get("regularMarketVolume")
            if isinstance(vol, (int, float)) and vol > 0 and f.preco > 0:
                f.liquidez_diaria = float(vol) * f.preco
        return fundos


def provider_padrao(data_dir: str | Path, token_brapi: str = "",
                    usar_rede: bool = True) -> FiiProvider:
    """Snapshot local, com atualização de preço pela rede quando possível."""
    snapshot = SnapshotFiiProvider(Path(data_dir) / "fiis_snapshot.json")
    if usar_rede:
        return BrapiFiiProvider(snapshot, token=token_brapi)
    return snapshot
