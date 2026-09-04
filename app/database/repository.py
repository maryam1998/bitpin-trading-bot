import sqlite3
import json
from datetime import datetime

class Repository:
    def __init__(self, db_path: str = "trading_bot.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    market TEXT,
                    action TEXT,
                    reason TEXT,
                    price REAL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    total_value REAL,
                    available_usdt REAL
                )
            """)
            conn.commit()

    def log_signal(self, signal):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO signals (timestamp, market, action, reason, price)
                VALUES (?, ?, ?, ?, ?)
            """, (datetime.now().isoformat(), signal.market, signal.action.value, signal.reason, signal.current_price))
            conn.commit()

    def save_portfolio_snapshot(self, total_value: float, available_usdt: float, exposure: dict):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO portfolio_snapshots (timestamp, total_value, available_usdt)
                VALUES (?, ?, ?)
            """, (datetime.now().isoformat(), total_value, available_usdt))
            conn.commit()

    def log_system_event(self, event_type: str, message: str):
        # ساده شده
        pass

    def log_risk_event(self, event_type: str, message: str):
        # ساده شده
        pass

    def log_market_data(self, symbol: str, last: float, bid: float, ask: float, volume: float):
        # ساده شده
        pass

    def get_recent_closes(self, symbol: str, limit: int = 60):
        return []
