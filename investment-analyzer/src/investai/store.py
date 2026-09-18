"""Persistência em SQLite.

Guarda sinais, operações, posições em papel e eventos de auditoria. Auditoria
não é enfeite: sem registro de "por que o robô entrou aqui" é impossível
descobrir se um prejuízo veio de erro de lógica ou de mercado.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .models import Position, Side, Signal, Trade

ESQUEMA = """
CREATE TABLE IF NOT EXISTS sinais (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    side TEXT NOT NULL,
    grade TEXT NOT NULL,
    score REAL NOT NULL,
    entry REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profits TEXT NOT NULL,
    risk_reward REAL NOT NULL,
    prob_estimada REAL NOT NULL,
    retorno_esperado_r REAL NOT NULL,
    regime TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sinais_ts ON sinais(ts DESC);
CREATE INDEX IF NOT EXISTS ix_sinais_symbol ON sinais(symbol, ts DESC);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry REAL NOT NULL,
    exit REAL NOT NULL,
    size REAL NOT NULL,
    opened_at INTEGER NOT NULL,
    closed_at INTEGER NOT NULL,
    pnl_usd REAL NOT NULL,
    pnl_r REAL NOT NULL,
    motivo_saida TEXT NOT NULL,
    fees_usd REAL NOT NULL,
    bars_held INTEGER NOT NULL,
    modo TEXT NOT NULL DEFAULT 'paper'
);
CREATE INDEX IF NOT EXISTS ix_trades_closed ON trades(closed_at DESC);

CREATE TABLE IF NOT EXISTS posicoes_papel (
    symbol TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    atualizado_em INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    nivel TEXT NOT NULL,
    origem TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    dados TEXT
);
CREATE INDEX IF NOT EXISTS ix_eventos_ts ON eventos(ts DESC);

CREATE TABLE IF NOT EXISTS estado (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL,
    atualizado_em INTEGER NOT NULL
);
"""


class Store:
    def __init__(self, caminho: str | Path):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.caminho), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL permite leitura concorrente com escrita — o painel consulta
        # enquanto o robô grava.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(ESQUEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ sinais
    def salvar_sinal(self, s: Signal) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO sinais (ts, symbol, timeframe, side, grade, score,
                       entry, stop_loss, take_profits, risk_reward, prob_estimada,
                       retorno_esperado_r, regime, payload)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s.gerado_em or int(time.time() * 1000), s.symbol, s.timeframe,
                 s.side.value, s.grade.value, s.score, s.entry, s.stop_loss,
                 json.dumps(s.take_profits), s.risk_reward,
                 s.prob_acerto_estimada, s.retorno_esperado_r, s.regime.value,
                 json.dumps(s.to_dict(), ensure_ascii=False)),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def sinais_recentes(self, limite: int = 50, symbol: str | None = None,
                        apenas_operaveis: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT payload FROM sinais WHERE 1=1"
        params: list[Any] = []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol.upper())
        if apenas_operaveis:
            sql += " AND grade IN ('A','B')"
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limite))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # ------------------------------------------------------------------ trades
    def salvar_trade(self, t: Trade, modo: str = "paper") -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO trades (symbol, side, entry, exit, size, opened_at,
                       closed_at, pnl_usd, pnl_r, motivo_saida, fees_usd,
                       bars_held, modo)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (t.symbol, t.side.value, t.entry, t.exit, t.size, t.opened_at,
                 t.closed_at, t.pnl_usd, t.pnl_r, t.motivo_saida, t.fees_usd,
                 t.bars_held, modo),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def trades(self, limite: int = 200, modo: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM trades"
        params: list[Any] = []
        if modo:
            sql += " WHERE modo = ?"
            params.append(modo)
        sql += " ORDER BY closed_at DESC LIMIT ?"
        params.append(int(limite))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def resumo_trades(self, modo: str | None = None) -> dict[str, Any]:
        """Desempenho realizado — o número que importa, não o backtest."""
        sql = ("SELECT COUNT(*) n, "
               "SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) ganhos, "
               "SUM(pnl_usd) pnl, SUM(fees_usd) taxas, AVG(pnl_r) exp_r, "
               "SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END) bruto_ganho, "
               "SUM(CASE WHEN pnl_usd <= 0 THEN -pnl_usd ELSE 0 END) bruto_perda "
               "FROM trades")
        params: list[Any] = []
        if modo:
            sql += " WHERE modo = ?"
            params.append(modo)
        with self._lock:
            r = self._conn.execute(sql, params).fetchone()
        n = r["n"] or 0
        perda = r["bruto_perda"] or 0.0
        ganho = r["bruto_ganho"] or 0.0
        return {
            "trades": n,
            "win_rate": (r["ganhos"] / n) if n else 0.0,
            "pnl_usd": round(r["pnl"] or 0.0, 2),
            "taxas_usd": round(r["taxas"] or 0.0, 2),
            "expectancy_r": round(r["exp_r"] or 0.0, 4),
            "profit_factor": round(ganho / perda, 3) if perda > 0 else (
                round(min(ganho, 99.0), 3) if ganho > 0 else 0.0),
        }

    # -------------------------------------------------------- posições (papel)
    def salvar_posicao(self, p: Position) -> None:
        payload = {
            "symbol": p.symbol, "side": p.side.value, "size": p.size,
            "entry": p.entry, "stop_loss": p.stop_loss,
            "take_profits": p.take_profits, "opened_at": p.opened_at,
            "leverage": p.leverage, "notional_usd": p.notional_usd,
            "risk_usd": p.risk_usd, "client_oid": p.client_oid, "modo": p.modo,
            "tps_atingidos": p.tps_atingidos, "trailing_ativo": p.trailing_ativo,
        }
        with self._lock:
            self._conn.execute(
                """INSERT INTO posicoes_papel (symbol, payload, atualizado_em)
                   VALUES (?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload,
                       atualizado_em=excluded.atualizado_em""",
                (p.symbol, json.dumps(payload), int(time.time() * 1000)))
            self._conn.commit()

    def remover_posicao(self, symbol: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM posicoes_papel WHERE symbol = ?", (symbol,))
            self._conn.commit()

    def posicoes(self) -> list[Position]:
        with self._lock:
            rows = self._conn.execute("SELECT payload FROM posicoes_papel").fetchall()
        out: list[Position] = []
        for r in rows:
            d = json.loads(r["payload"])
            out.append(Position(
                symbol=d["symbol"], side=Side(d["side"]), size=d["size"],
                entry=d["entry"], stop_loss=d["stop_loss"],
                take_profits=d["take_profits"], opened_at=d["opened_at"],
                leverage=d.get("leverage", 1.0),
                notional_usd=d.get("notional_usd", 0.0),
                risk_usd=d.get("risk_usd", 0.0),
                client_oid=d.get("client_oid", ""), modo=d.get("modo", "paper"),
                tps_atingidos=d.get("tps_atingidos", 0),
                trailing_ativo=d.get("trailing_ativo", False),
            ))
        return out

    # ---------------------------------------------------------------- eventos
    def registrar_evento(self, nivel: str, origem: str, mensagem: str,
                         dados: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO eventos (ts, nivel, origem, mensagem, dados) VALUES (?,?,?,?,?)",
                (int(time.time() * 1000), nivel, origem, mensagem,
                 json.dumps(dados, ensure_ascii=False, default=str) if dados else None))
            self._conn.commit()

    def eventos(self, limite: int = 100, nivel: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM eventos"
        params: list[Any] = []
        if nivel:
            sql += " WHERE nivel = ?"
            params.append(nivel)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limite))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("dados"):
                try:
                    d["dados"] = json.loads(d["dados"])
                except json.JSONDecodeError:
                    pass
            out.append(d)
        return out

    # ------------------------------------------------------------------ estado
    def set_estado(self, chave: str, valor: Any) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO estado (chave, valor, atualizado_em) VALUES (?,?,?)
                   ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor,
                       atualizado_em=excluded.atualizado_em""",
                (chave, json.dumps(valor, default=str), int(time.time() * 1000)))
            self._conn.commit()

    def get_estado(self, chave: str, default: Any = None) -> Any:
        with self._lock:
            r = self._conn.execute(
                "SELECT valor FROM estado WHERE chave = ?", (chave,)).fetchone()
        if r is None:
            return default
        try:
            return json.loads(r["valor"])
        except json.JSONDecodeError:
            return default

    def curva_capital(self, capital_inicial: float,
                      modo: str | None = None) -> list[dict[str, Any]]:
        """Reconstrói a curva de capital a partir dos trades realizados."""
        sql = "SELECT closed_at, pnl_usd FROM trades"
        params: list[Any] = []
        if modo:
            sql += " WHERE modo = ?"
            params.append(modo)
        sql += " ORDER BY closed_at ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        equity = capital_inicial
        curva = [{"ts": rows[0]["closed_at"] if rows else 0, "equity": equity}]
        for r in rows:
            equity += r["pnl_usd"]
            curva.append({"ts": r["closed_at"], "equity": round(equity, 2)})
        return curva
