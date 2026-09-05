import logging
import json
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass

log = logging.getLogger(__name__)

@dataclass
class TradeResult:
    symbol: str
    action: str
    entry_price: float
    exit_price: float
    profit_percent: float
    market_conditions: Dict[str, Any]
    ai_decision: Dict[str, Any]
    timestamp: str


class ActiveLearner:
    def __init__(self, repository, settings):
        self.repo = repository
        self.settings = settings
        self._cache = {}
        self._performance_stats = {}

    def record_trade_result(self, trade_result: TradeResult):
        """ثبت نتیجه‌ی یک معامله برای یادگیری"""
        try:
            self.repo.save_trade({
                "symbol": trade_result.symbol,
                "side": trade_result.action.lower(),
                "entry_price": trade_result.entry_price,
                "exit_price": trade_result.exit_price,
                "profit_percent": trade_result.profit_percent,
                "status": "closed",
                "notes": json.dumps(trade_result.ai_decision, ensure_ascii=False),
            })

            self._update_performance_stats(trade_result)
            log.info(f"🧠 Learned from trade: {trade_result.symbol} -> {trade_result.profit_percent:.2f}%")

        except Exception as e:
            log.error(f"Learning error: {e}")

    def close_trade(self, symbol: str, exit_price: float) -> Optional[TradeResult]:
        """بستن معامله و ثبت نتیجه نهایی"""
        try:
            # دریافت آخرین معامله باز برای این نماد
            trades = self.repo.get_trades(symbol, status="open", limit=1)
            if not trades:
                return None

            trade = trades[0]
            entry_price = trade.get("entry_price", 0)
            if entry_price <= 0:
                return None

            profit_percent = ((exit_price - entry_price) / entry_price) * 100

            # به‌روزرسانی در دیتابیس
            result = TradeResult(
                symbol=symbol,
                action=trade.get("side", "UNKNOWN"),
                entry_price=entry_price,
                exit_price=exit_price,
                profit_percent=profit_percent,
                market_conditions={},
                ai_decision={},
                timestamp=datetime.now().isoformat(),
            )

            self.record_trade_result(result)
            return result

        except Exception as e:
            log.error(f"Close trade error: {e}")
            return None

    def _update_performance_stats(self, result: TradeResult):
        symbol = result.symbol
        if symbol not in self._performance_stats:
            self._performance_stats[symbol] = {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "total_profit": 0.0,
                "avg_profit": 0.0,
                "win_rate": 0.0,
                "recent_results": [],
            }

        stats = self._performance_stats[symbol]
        stats["total_trades"] += 1
        if result.profit_percent > 0:
            stats["wins"] += 1
        else:
            stats["losses"] += 1
        stats["total_profit"] += result.profit_percent
        stats["avg_profit"] = stats["total_profit"] / stats["total_trades"] if stats["total_trades"] > 0 else 0
        stats["win_rate"] = stats["wins"] / stats["total_trades"] if stats["total_trades"] > 0 else 0
        stats["recent_results"].append(result.profit_percent)
        if len(stats["recent_results"]) > 50:
            stats["recent_results"] = stats["recent_results"][-50:]

        self.repo.save_ai_performance(
            symbol=symbol,
            decision=result.action,
            expected_profit=result.profit_percent,
            actual_profit=result.profit_percent,
            score=int(stats["win_rate"] * 10),
            feedback=f"Win rate: {stats['win_rate']:.2f}, Avg profit: {stats['avg_profit']:.2f}%",
        )

    def get_few_shot_examples(self, symbol: str, limit: int = 3) -> List[Dict[str, Any]]:
        try:
            trades = self.repo.get_trades(symbol, status="closed", limit=20)
            successful_trades = [t for t in trades if t.get("profit_percent", 0) > 0]

            if not successful_trades:
                return []

            successful_trades.sort(key=lambda x: x.get("profit_percent", 0), reverse=True)
            examples = []

            for trade in successful_trades[:limit]:
                notes = trade.get("notes", "{}")
                try:
                    ai_decision = json.loads(notes) if notes else {}
                except:
                    ai_decision = {}

                examples.append({
                    "symbol": trade.get("symbol", ""),
                    "action": trade.get("side", ""),
                    "entry_price": trade.get("entry_price", 0),
                    "exit_price": trade.get("exit_price", 0),
                    "profit_percent": trade.get("profit_percent", 0),
                    "ai_reason": ai_decision.get("reason", ""),
                })

            return examples

        except Exception as e:
            log.error(f"Error getting few-shot examples: {e}")
            return []

    def get_performance_summary(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        if symbol and symbol in self._performance_stats:
            return self._performance_stats[symbol]

        total_stats = {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_profit": 0.0,
            "symbols": {},
        }

        for sym, stats in self._performance_stats.items():
            total_stats["total_trades"] += stats["total_trades"]
            total_stats["wins"] += stats["wins"]
            total_stats["losses"] += stats["losses"]
            total_stats["total_profit"] += stats["total_profit"]
            total_stats["symbols"][sym] = stats

        total_stats["win_rate"] = total_stats["wins"] / total_stats["total_trades"] if total_stats["total_trades"] > 0 else 0
        total_stats["avg_profit"] = total_stats["total_profit"] / total_stats["total_trades"] if total_stats["total_trades"] > 0 else 0

        return total_stats

    def should_adjust_strategy(self, symbol: str) -> bool:
        if symbol not in self._performance_stats:
            return False

        stats = self._performance_stats[symbol]
        if stats["total_trades"] < 5:
            return False

        if stats["win_rate"] < 0.4 or stats["avg_profit"] < -5.0:
            return True

        return False

    def generate_feedback_prompt(self, symbol: str) -> str:
        if symbol not in self._performance_stats:
            return ""

        stats = self._performance_stats[symbol]
        examples = self.get_few_shot_examples(symbol, limit=2)

        prompt = f"""
📊 **بازخورد عملکرد برای {symbol}:**
- تعداد معاملات: {stats['total_trades']}
- نرخ موفقیت: {stats['win_rate']:.1f}%
- میانگین سود: {stats['avg_profit']:.2f}%
- برد: {stats['wins']} / باخت: {stats['losses']}

📈 **نمونه معاملات موفق:**
"""
        for ex in examples:
            prompt += f"- {ex['action']} @ {ex['entry_price']:.2f} → سود {ex['profit_percent']:.2f}%\n"

        if stats["win_rate"] < 0.4:
            prompt += "\n⚠️ **هشدار:** نرخ موفقیت پایین است. لطفاً در تصمیم‌گیری‌های آینده محافظه‌کارانه‌تر عمل کنید."
        elif stats["win_rate"] > 0.6:
            prompt += "\n✅ **عملکرد خوب:** استراتژی فعلی مؤثر است. ادامه دهید."

        return prompt
