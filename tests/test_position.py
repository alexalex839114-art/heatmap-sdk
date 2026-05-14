from app.position import PositionState, parse_account_update_positions


def test_parse_one_way_long_position():
    event = {
        "e": "ACCOUNT_UPDATE",
        "E": 1700000000123,
        "a": {
            "P": [
                {
                    "s": "BTCUSDT",
                    "pa": "0.012",
                    "ep": "65000.0",
                    "bep": "65010.0",
                    "up": "3.5",
                    "ps": "BOTH",
                }
            ]
        },
    }

    positions = parse_account_update_positions(event)

    assert positions["BTCUSDT"].side == "LONG"
    assert positions["BTCUSDT"].quantity == 0.012
    assert positions["BTCUSDT"].amount == 0.012
    assert positions["BTCUSDT"].entry_price == 65000.0
    assert positions["BTCUSDT"].break_even_price == 65010.0
    assert positions["BTCUSDT"].unrealized_pnl == 3.5
    assert positions["BTCUSDT"].update_time_ms == 1700000000123


def test_parse_one_way_short_position():
    event = {
        "e": "ACCOUNT_UPDATE",
        "a": {
            "P": [
                {
                    "s": "ETHUSDT",
                    "pa": "-0.5",
                    "ep": "3200.0",
                    "bep": "3198.0",
                    "up": "-2.1",
                    "ps": "BOTH",
                }
            ]
        },
    }

    positions = parse_account_update_positions(event)

    assert positions["ETHUSDT"].side == "SHORT"
    assert positions["ETHUSDT"].quantity == 0.5
    assert positions["ETHUSDT"].amount == -0.5
    assert positions["ETHUSDT"].unrealized_pnl == -2.1


def test_zero_position_is_flat():
    pos = PositionState(symbol="BTCUSDT", amount=0.0, entry_price=0.0)

    assert pos.side == "FLAT"
    assert pos.quantity == 0.0
    assert not pos.is_open


def test_position_estimates_mark_price_from_signed_pnl():
    long_pos = PositionState(
        symbol="BTCUSDT",
        amount=0.1,
        entry_price=100.0,
        unrealized_pnl=1.0,
    )
    short_pos = PositionState(
        symbol="BTCUSDT",
        amount=-0.1,
        entry_price=100.0,
        unrealized_pnl=1.0,
    )

    assert long_pos.estimated_mark_price == 110.0
    assert short_pos.estimated_mark_price == 90.0


def test_parser_ignores_hedge_mode_rows_for_v1():
    event = {
        "a": {
            "P": [
                {
                    "s": "BTCUSDT",
                    "pa": "1.0",
                    "ep": "65000.0",
                    "ps": "LONG",
                }
            ]
        }
    }

    assert parse_account_update_positions(event) == {}


def test_parser_skips_malformed_rows():
    event = {
        "a": {
            "P": [
                {"s": "BTCUSDT", "pa": "not-a-number", "ep": "65000.0", "ps": "BOTH"},
                {"pa": "1.0", "ep": "65000.0", "ps": "BOTH"},
            ]
        }
    }

    assert parse_account_update_positions(event) == {}
