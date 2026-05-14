from app.order_book import OrderBook
from app.binance_client import apply_depth_event


def test_load_snapshot_sets_best_bid_and_ask():
    book = OrderBook()
    book.load_snapshot(bids=[("100.0", "2.0")], asks=[("101.0", "3.0")])

    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0


def test_apply_delta_updates_and_removes_levels():
    book = OrderBook()
    book.load_snapshot(bids=[("100.0", "2.0")], asks=[("101.0", "3.0")])

    book.apply_delta(bids=[("100.0", "0")], asks=[("102.0", "4.0")])

    assert book.best_bid() is None
    assert book.best_ask() == 101.0


def test_apply_binance_depth_event_updates_book():
    book = OrderBook()
    book.load_snapshot(bids=[("100.0", "1.0")], asks=[("101.0", "1.0")])

    apply_depth_event(book, {"b": [["100.0", "2.0"]], "a": [["101.0", "0"]]})

    assert book.bids[100.0] == 2.0
    assert 101.0 not in book.asks
