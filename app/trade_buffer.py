class TradeBuffer:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def add(self, trade: dict) -> None:
        self._items.append(trade)

    def drain(self) -> list[dict]:
        items = self._items
        self._items = []
        return items
