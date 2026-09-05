import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any

class Repository:
    def __init__(self, db_path: str = "trading_bot.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # جدول سیگنال‌ها
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
            
            # جدول پرتفولیو
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    total_value REAL,
                    available_usdt REAL
                )
            """)
            
            # ===== جدول جدید: معاملات اجرا شده =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    symbol TEXT,
                    side TEXT,
                    amount REAL,
                    entry_price REAL,
                    exit_price REAL,
                    size_usdt REAL,
                    profit_usdt REAL,
                    profit_percent REAL,
                    status TEXT,
                    order_id TEXT,
                    notes TEXT
                )
            """)
            
            # ===== جدول جدید: عملکرد AI برای یادگیری =====
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    symbol TEXT,
                    decision TEXT,
                    expected_profit REAL,
                    actual_profit REAL,
                    score INTEGER,
                    feedback TEXT
                )
            """)
            
            conn.commit()

    # ===== متدهای قبلی (بدون تغییر) =====
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
        pass

    def log_risk_event(self, event_type: str, message: str):
        pass

    def log_market_data(self, symbol: str, last: float, bid: float, ask: float, volume: float):
        pass

    def get_recent_closes(self, symbol: str, limit: int = 60):
        return []

    # ===== متدهای جدید برای مرحله ۳ =====

    def save_trade(self, trade_info: Dict[str, Any]) -> int:
        """ذخیره‌سازی یک معامله اجرا شده"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades (
                    timestamp, symbol, side, amount, entry_price, exit_price,
                    size_usdt, profit_usdt, profit_percent, status, order_id, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_info.get("timestamp", datetime.now().isoformat()),
                trade_info.get("symbol", ""),
                trade_info.get("side", ""),
                trade_info.get("amount", 0.0),
                trade_info.get("entry_price", 0.0),
                trade_info.get("exit_price", 0.0),
                trade_info.get("size_usdt", 0.0),
                trade_info.get("profit_usdt", 0.0),
                trade_info.get("profit_percent", 0.0),
                trade_info.get("status", "open"),
                trade_info.get("order_id", ""),
                trade_info.get("notes", ""),
            ))
            return cursor.lastrowid

    def get_trades(self, symbol: str = None, status: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """دریافت تاریخچه معاملات"""
        query = "SELECT * FROM trades"
        params = []
        conditions = []
        
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if status:
            conditions.append("status = ?")
            params.append(status)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += f" ORDER BY timestamp DESC LIMIT {limit}"
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def save_ai_performance(self, symbol: str, decision: str, expected_profit: float, actual_profit: float, score: int, feedback: str = ""):
        """ذخیره‌سازی عملکرد AI برای یادگیری"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ai_performance (timestamp, symbol, decision, expected_profit, actual_profit, score, feedback)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                symbol,
                decision,
                expected_profit,
                actual_profit,
                score,
                feedback,
            ))
            conn.commit()

    def get_ai_performance(self, symbol: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        """دریافت عملکرد AI برای یک نماد خاص"""
        query = "SELECT * FROM ai_performance"
        params = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += f" ORDER BY timestamp DESC LIMIT {limit}"
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
