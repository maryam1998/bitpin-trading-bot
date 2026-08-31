from app.risk.manager import RiskManager
from app.strategies.base import Signal, Action


class FakeSettings:
    mode = "PAPER"
    max_position_percent = 5.0
    max_asset_exposure_percent = 20.0
    max_total_exposure_percent = 60.0
    max_daily_loss_percent = 3.0
    max_open_positions = 5
    max_slippage_percent = 0.5
    min_liquidity = 1000.0
    min_net_edge = 0.3
    max_consecutive_losses = 4
    stale_data_seconds = 60


def make_signal(net_edge=1.0, action=Action.BUY):
    return Signal(
        asset="BTC", market="BTC_USDT", action=action, current_price=100,
        entry_price=100, gross_edge_percent=net_edge + 0.85, fee_percent=0.35,
        spread_cost_percent=0.1, slippage_percent=0.1, safety_margin_percent=0.1,
        reason="test",
    )


def test_approves_good_trade():
    rm = RiskManager(FakeSettings())
    sig = make_signal(net_edge=1.0)
    decision = rm.approve(
        sig, portfolio_value_usdt=10000, available_usdt=5000,
        current_asset_exposure_usdt=0, total_exposure_usdt=0,
        orderbook_liquidity_usdt=5000, estimated_slippage_percent=0.1,
        price_age_seconds=1,
    )
    assert decision.approved


def test_rejects_stale_price():
    rm = RiskManager(FakeSettings())
    sig = make_signal(net_edge=1.0)
    decision = rm.approve(
        sig, portfolio_value_usdt=10000, available_usdt=5000,
        current_asset_exposure_usdt=0, total_exposure_usdt=0,
        orderbook_liquidity_usdt=5000, estimated_slippage_percent=0.1,
        price_age_seconds=999,
    )
    assert not decision.approved


def test_rejects_low_liquidity():
    rm = RiskManager(FakeSettings())
    sig = make_signal(net_edge=1.0)
    decision = rm.approve(
        sig, portfolio_value_usdt=10000, available_usdt=5000,
        current_asset_exposure_usdt=0, total_exposure_usdt=0,
        orderbook_liquidity_usdt=10, estimated_slippage_percent=0.1,
        price_age_seconds=1,
    )
    assert not decision.approved


def test_rejects_exceeding_asset_exposure():
    rm = RiskManager(FakeSettings())
    sig = make_signal(net_edge=1.0)
    decision = rm.approve(
        sig, portfolio_value_usdt=10000, available_usdt=5000,
        current_asset_exposure_usdt=1950, total_exposure_usdt=1950,
        orderbook_liquidity_usdt=5000, estimated_slippage_percent=0.1,
        price_age_seconds=1,
    )
    assert not decision.approved


def test_kill_switch_blocks_everything():
    rm = RiskManager(FakeSettings())
    rm.trip_kill_switch("test")
    sig = make_signal(net_edge=1.0)
    decision = rm.approve(
        sig, portfolio_value_usdt=10000, available_usdt=5000,
        current_asset_exposure_usdt=0, total_exposure_usdt=0,
        orderbook_liquidity_usdt=5000, estimated_slippage_percent=0.1,
        price_age_seconds=1,
    )
    assert not decision.approved
