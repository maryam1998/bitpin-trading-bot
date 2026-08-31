"""
SQLite persistence layer. API secrets are never stored here — only market
data, signals, orders, fills, positions, PnL and event logs.
"""
import sqlite3
import time
import json
import logging

log = logging.getLogger("database.repository")

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    symbol TEXT PRIMARY KEY, base TEXT, quote TEXT, active INTEGER, updated_at REAL
);
CREATE TABLE IF NOT EXISTS market_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, last_price REAL, bid REAL,
    ask REAL, volume REAL, ts REAL
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, market TEXT, action TEXT,
    net_edge_percent REAL, reason TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, market TEXT, side TEXT,
    order_type TEXT, price REAL, amount REAL, status TEXT, exchange_order_id TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, price REAL, amount REAL,
    fee REAL, ts REAL
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, asset TEXT, market TEXT, side TEXT,
    entry_price REAL, size_usdt REAL, status TEXT, opened_at REAL, closed_at REAL
);
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, total_value_usdt REAL, available_usdt REAL,
    breakdown_json TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS pnl (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, market TEXT, gross_pnl_usdt REAL,
    fees_usdt REAL, net_pnl_usdt REAL, ts REAL
);
CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, details TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, details TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT, message TEXT, ts REAL
);
"""


class Repository:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def log_signal(self, signal):
        self.conn.execute(
            "INSERT INTO signals (asset, market, action, net_edge_percent, reason, ts) VALUES (?,?,?,?,?,?)",
            (signal.asset, signal.market, signal.action.value, signal.net_edge_percent, signal.reason, time.time()),
        )
        self.conn.commit()

    def log_risk_event(self, event_type: str, details: str):
        self.conn.execute(
            "INSERT INTO risk_events (event_type, details, ts) VALUES (?,?,?)",
            (event_type, details, time.time()),
        )
        self.conn.commit()

    def log_system_event(self, event_type: str, details: str):
        self.conn.execute(
            "INSERT INTO system_events (event_type, details, ts) VALUES (?,?,?)",
            (event_type, details, time.time()),
        )
        self.conn.commit()

    def save_portfolio_snapshot(self, total_value_usdt: float, available_usdt: float, breakdown: dict):
        self.conn.execute(
            "INSERT INTO portfolio_snapshots (total_value_usdt, available_usdt, breakdown_json, ts) VALUES (?,?,?,?)",
            (total_value_usdt, available_usdt, json.dumps(breakdown), time.time()),
        )
        self.conn.commit()
