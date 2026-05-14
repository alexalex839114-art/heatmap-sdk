from pydantic import BaseModel


class StatusEvent(BaseModel):
    type: str = "status"
    state: str
    symbol: str | None = None
    message: str | None = None


class FrameEvent(BaseModel):
    type: str = "frame"
    timestamp: int
    column: list[int]
    mid_price: float
    best_bid: float | None
    best_ask: float | None


class TradeOverlayItem(BaseModel):
    price: float
    qty: float
    y: int


class TradesEvent(BaseModel):
    type: str = "trades"
    timestamp: int
    items: list[TradeOverlayItem]
