"""
Optional AI/LLM analysis layer.

STRICT BOUNDARY: this module may only read data and produce text summaries.
It must never be given the BitpinClient, RiskManager mutation methods, or
any function that can place/cancel orders, change risk limits, or enable
LIVE mode. If you wire this up to an LLM API, only pass it already-computed,
read-only summaries — never let it call back into execution code.
"""
import logging

log = logging.getLogger("ai.analyzer")


class AIAnalyzer:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def summarize_market_conditions(self, snapshots: list) -> str:
        if not self.enabled:
            return ""
        # Intentionally left as a stub: wire up your preferred LLM call here,
        # passing only the read-only `snapshots` data — nothing that could
        # let it act on the account.
        return "(AI analysis not configured)"
