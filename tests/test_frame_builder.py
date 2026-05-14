from app.frame_builder import FrameBuilder
from app.order_book import OrderBook


def test_build_frame_returns_uint8_column_with_expected_height():
    book = OrderBook()
    book.load_snapshot(
        bids=[("99", "2.0"), ("98", "1.0")],
        asks=[("100", "3.0"), ("101", "1.5")],
    )
    builder = FrameBuilder(
        height=8,
        tick_size=1.0,
        aggregation=1,
        visible_levels=8,
        buffer_levels=0,
    )

    frame = builder.build(book, trades=[])

    assert len(frame.column) == 8
    assert all(0 <= value <= 255 for value in frame.column)


def test_compression_two_merges_two_adjacent_ticks_into_one_bucket():
    book = OrderBook()
    book.load_snapshot(
        bids=[("99", "1.0"), ("98", "2.0")],
        asks=[("100", "3.0"), ("101", "4.0")],
    )

    frame_default = FrameBuilder(
        height=8,
        tick_size=1.0,
        aggregation=1,
        visible_levels=8,
        buffer_levels=0,
    ).build(book, trades=[])
    frame_compressed = FrameBuilder(
        height=8,
        tick_size=1.0,
        aggregation=2,
        visible_levels=8,
        buffer_levels=0,
    ).build(book, trades=[])

    assert sum(1 for value in frame_default.column if value > 0) == 4
    assert sum(1 for value in frame_compressed.column if value > 0) == 2


def test_small_mid_movement_inside_buffer_reuses_grid_anchor():
    first_book = OrderBook()
    first_book.load_snapshot(
        bids=[("99", "1.0")],
        asks=[("100", "1.0")],
    )
    second_book = OrderBook()
    second_book.load_snapshot(
        bids=[("100", "1.0")],
        asks=[("101", "1.0")],
    )
    builder = FrameBuilder(
        height=6,
        tick_size=1.0,
        aggregation=1,
        visible_levels=6,
        buffer_levels=2,
        recenter_margin_levels=1,
    )

    builder.build(first_book, trades=[])
    first_origin = builder._origin_price

    builder.build(second_book, trades=[])

    assert builder._origin_price == first_origin


def test_large_mid_movement_past_buffer_recenters_grid():
    first_book = OrderBook()
    first_book.load_snapshot(
        bids=[("99", "1.0")],
        asks=[("100", "1.0")],
    )
    second_book = OrderBook()
    second_book.load_snapshot(
        bids=[("105", "1.0")],
        asks=[("106", "1.0")],
    )
    builder = FrameBuilder(
        height=6,
        tick_size=1.0,
        aggregation=1,
        visible_levels=6,
        buffer_levels=2,
        recenter_margin_levels=1,
    )

    builder.build(first_book, trades=[])
    first_origin = builder._origin_price

    builder.build(second_book, trades=[])

    assert builder._origin_price != first_origin


def test_mid_price_in_top_quarter_recenters_back_to_visible_center():
    initial_book = OrderBook()
    initial_book.load_snapshot(
        bids=[("99", "1.0")],
        asks=[("100", "1.0")],
    )
    shifted_book = OrderBook()
    shifted_book.load_snapshot(
        bids=[("102", "1.0")],
        asks=[("103", "1.0")],
    )
    builder = FrameBuilder(
        height=8,
        tick_size=1.0,
        aggregation=1,
        visible_levels=8,
        buffer_levels=4,
        recenter_margin_levels=1,
    )

    builder.build(initial_book, trades=[])
    origin_before = builder._origin_price
    mid_before = (shifted_book.best_bid() + shifted_book.best_ask()) / 2
    visible_index_before = builder._visible_index_for_price(mid_before)

    builder.build(shifted_book, trades=[])
    origin_after = builder._origin_price
    visible_index_after = builder._visible_index_for_price(mid_before)

    assert visible_index_before is not None
    assert visible_index_before >= 6
    assert origin_after != origin_before
    assert visible_index_after == 4


def test_trade_mapping_uses_same_grid_as_aggregated_book():
    book = OrderBook()
    book.load_snapshot(
        bids=[("99", "1.0")],
        asks=[("100", "2.0"), ("101", "3.0")],
    )
    builder = FrameBuilder(
        height=8,
        tick_size=1.0,
        aggregation=2,
        visible_levels=8,
        buffer_levels=0,
    )

    frame = builder.build(book, trades=[{"price": 100.0, "qty": 1.0}])

    assert frame.trades
    assert 0 <= frame.trades[0].y < 8
    assert frame.column[frame.trades[0].y] > 0


def test_trade_mapping_preserves_aggressor_side_for_overlay():
    book = OrderBook()
    book.load_snapshot(
        bids=[("99", "1.0")],
        asks=[("100", "2.0")],
    )
    builder = FrameBuilder(
        height=8,
        tick_size=1.0,
        aggregation=1,
        visible_levels=8,
        buffer_levels=0,
    )

    frame = builder.build(
        book,
        trades=[{"price": 100.0, "qty": 1.0, "is_buyer_maker": True}],
    )

    assert frame.trades[0].is_buyer_maker is True
