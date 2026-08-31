from app.strategies.initial_strategy import InitialStrategy
from app.strategies.base import Action


def test_wait_with_no_momentum():
    strat = InitialStrategy(min_net_edge_percent=0.3)
    snapshot = {
        "asset": "BTC", "symbol": "BTC_USDT",
        "ticker": {"last": 100, "bid": 99.95, "ask": 100.05, "volume": 100000},
        "orderbook": {"bids": [[99.95, 1]], "asks": [[100.05, 1]]},
        "recent_change_percent": 0.05,
    }
    sig = strat.evaluate(snapshot)
    assert sig.action == Action.WAIT


def test_do_not_trade_below_min_edge():
    strat = InitialStrategy(min_net_edge_percent=5.0)
    snapshot = {
        "asset": "BTC", "symbol": "BTC_USDT",
        "ticker": {"last": 100, "bid": 99.9, "ask": 100.1, "volume": 100000},
        "orderbook": {"bids": [[99.9, 10]], "asks": [[100.1, 2]]},
        "recent_change_percent": 1.0,
    }
    sig = strat.evaluate(snapshot)
    assert sig.action == Action.DO_NOT_TRADE


def test_net_edge_formula():
    from app.strategies.base import Signal
    sig = Signal(
        asset="BTC", market="BTC_USDT", action=Action.BUY, current_price=100,
        entry_price=100, gross_edge_percent=2.0, fee_percent=0.35,
        spread_cost_percent=0.1, slippage_percent=0.1, safety_margin_percent=0.1,
        reason="",
    )
    assert abs(sig.net_edge_percent - 1.35) < 1e-9
