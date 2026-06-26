"""
core/trade_db.py — SQLite trade recorder for TraderBot v4

Records every trade event (order placed, SL hit, TP hit, risk-free win,
balance TP) in a local SQLite database.

Schema
──────
sessions    — one row per bot session (start/stop time, start balance)
trades      — one row per closed position (ticket, side, lot, entry/exit,
              result, pnl, reason, session_id)

The DB file lives at %APPDATA%/TraderBotV4/trades.db (Windows) or
~/.traderbotv4/trades.db on other platforms, so it survives reinstalls.

Usage
─────
    from core.trade_db import db
    db.start_session(symbol, balance)   # call when bot starts
    db.record_trade(...)                # call on every close
    db.end_session(balance)             # call when bot stops
    rows = db.query_trades(...)         # for Report tab
"""

import sqlite3
import os
import logging
import threading
from datetime import datetime, date
from typing import List, Optional, Dict, Any

log = logging.getLogger("trade_db")


def _db_path() -> str:
    import platform
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        d = os.path.join(base, "TraderBotV4")
    else:
        d = os.path.join(os.path.expanduser("~"), ".traderbotv4")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "trades.db")


_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,
    started_at    TEXT    NOT NULL,
    ended_at      TEXT,
    start_balance REAL,
    end_balance   REAL,
    lot_mode      INTEGER DEFAULT 1,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER REFERENCES sessions(id),
    symbol        TEXT    NOT NULL,
    ticket        INTEGER,
    side          TEXT,       -- 'buy' | 'sell'
    lot           REAL,
    entry_price   REAL,
    exit_price    REAL,
    sl_price      REAL,
    tp_price      REAL,
    open_time     TEXT,
    close_time    TEXT,
    pnl           REAL,       -- actual P&L in account currency
    pips          REAL,       -- pips gained/lost
    result        TEXT,       -- 'tp' | 'sl' | 'risk_free' | 'loss_free' | 'manual' | 'balance_tp'
    rect_name     TEXT,
    round_num     INTEGER,
    cumulative_loss REAL,
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);
CREATE INDEX IF NOT EXISTS idx_trades_close   ON trades(close_time);
CREATE INDEX IF NOT EXISTS idx_trades_result  ON trades(result);
"""


class TradeDB:
    def __init__(self):
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._session_id: Optional[int] = None
        self._path = _db_path()
        self._ensure_connected()

    def _ensure_connected(self):
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(
                    self._path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.executescript(_DDL)
                self._conn.commit()
            except Exception as e:
                log.error("TradeDB connect failed: %s", e)
                self._conn = None

    def _exec(self, sql: str, params=()):
        with self._lock:
            self._ensure_connected()
            if self._conn is None:
                return None
            try:
                cur = self._conn.execute(sql, params)
                self._conn.commit()
                return cur
            except Exception as e:
                log.error("TradeDB exec error: %s | sql=%s", e, sql[:80])
                return None

    # ── Session ───────────────────────────────────────────────────

    def start_session(self, symbol: str, start_balance: float,
                      lot_mode: int = 1) -> int:
        """Call when bot starts. Returns new session_id."""
        cur = self._exec(
            "INSERT INTO sessions(symbol,started_at,start_balance,lot_mode) "
            "VALUES(?,?,?,?)",
            (symbol, datetime.now().isoformat(timespec="seconds"),
             start_balance, lot_mode)
        )
        if cur:
            self._session_id = cur.lastrowid
            log.info("TradeDB: session %d started (%s)",
                     self._session_id, symbol)
        return self._session_id or 0

    def end_session(self, end_balance: float):
        """Call when bot stops."""
        if not self._session_id:
            return
        self._exec(
            "UPDATE sessions SET ended_at=?, end_balance=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"),
             end_balance, self._session_id)
        )
        self._session_id = None

    # ── Trade recording ───────────────────────────────────────────

    def record_trade(self,
                     symbol:    str,
                     ticket:    int,
                     side:      str,
                     lot:       float,
                     entry_price:  float,
                     exit_price:   float,
                     sl_price:     float = 0.0,
                     tp_price:     float = 0.0,
                     open_time:    str = "",
                     close_time:   str = "",
                     pnl:          float = 0.0,
                     pips:         float = 0.0,
                     result:       str = "sl",
                     rect_name:    str = "",
                     round_num:    int = 0,
                     cumulative_loss: float = 0.0,
                     notes:        str = ""):
        close_time = close_time or datetime.now().isoformat(timespec="seconds")
        self._exec(
            """INSERT INTO trades(session_id,symbol,ticket,side,lot,
               entry_price,exit_price,sl_price,tp_price,
               open_time,close_time,pnl,pips,result,
               rect_name,round_num,cumulative_loss,notes)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (self._session_id, symbol, ticket, side, lot,
             entry_price, exit_price, sl_price, tp_price,
             open_time, close_time, pnl, pips, result,
             rect_name, round_num, cumulative_loss, notes)
        )

    # ── Queries for Report tab ────────────────────────────────────

    def query_trades(self,
                     symbol:     Optional[str] = None,
                     date_from:  Optional[str] = None,
                     date_to:    Optional[str] = None,
                     result:     Optional[str] = None,
                     session_id: Optional[int] = None,
                     limit:      int = 2000) -> List[Dict]:
        """Flexible query for Report tab. Returns list of dicts."""
        where = []
        params = []
        if symbol:
            where.append("symbol=?")
            params.append(symbol)
        if date_from:
            where.append("close_time>=?")
            params.append(date_from)
        if date_to:
            where.append("close_time<=?")
            params.append(date_to + "T23:59:59")
        if result:
            where.append("result=?")
            params.append(result)
        if session_id:
            where.append("session_id=?")
            params.append(session_id)
        sql = "SELECT * FROM trades"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY close_time DESC LIMIT ?"
        params.append(limit)
        cur = self._exec(sql, params)
        if cur is None:
            return []
        return [dict(row) for row in cur.fetchall()]

    def query_sessions(self, limit: int = 100) -> List[Dict]:
        cur = self._exec(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,))
        if cur is None:
            return []
        return [dict(row) for row in cur.fetchall()]

    def summary_by_day(self,
                       symbol: Optional[str] = None,
                       days:   int = 30) -> List[Dict]:
        """Return daily PnL aggregates for charting."""
        where = "WHERE close_time IS NOT NULL"
        params: list = []
        if symbol:
            where += " AND symbol=?"
            params.append(symbol)
        params.append(days)
        cur = self._exec(f"""
            SELECT
                substr(close_time,1,10) AS day,
                COUNT(*)                AS trades,
                SUM(CASE WHEN result='tp' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN result='sl' THEN 1 ELSE 0 END) AS losses,
                ROUND(SUM(pnl),2)       AS pnl,
                ROUND(SUM(pips),1)      AS pips
            FROM trades {where}
              AND close_time >= date('now', '-' || ? || ' days')
            GROUP BY day ORDER BY day
        """, params)
        if cur is None:
            return []
        return [dict(row) for row in cur.fetchall()]

    def summary_stats(self, symbol: Optional[str] = None) -> Dict:
        """Overall session statistics."""
        where = "WHERE 1=1"
        params: list = []
        if symbol:
            where += " AND symbol=?"
            params.append(symbol)
        cur = self._exec(f"""
            SELECT
                COUNT(*)                                    AS total,
                SUM(CASE WHEN result='tp' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN result='sl' THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN result IN ('risk_free','loss_free')
                              THEN 1 ELSE 0 END)            AS locked,
                ROUND(SUM(pnl),2)                           AS total_pnl,
                ROUND(AVG(pnl),2)                           AS avg_pnl,
                ROUND(MAX(pnl),2)                           AS best_trade,
                ROUND(MIN(pnl),2)                           AS worst_trade,
                ROUND(SUM(pips),1)                          AS total_pips
            FROM trades {where}
        """, params)
        if cur is None:
            return {}
        row = cur.fetchone()
        return dict(row) if row else {}


# Module-level singleton
db = TradeDB()
