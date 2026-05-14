import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.ws_session import LiveHeatmapService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("app").setLevel(logging.INFO)


def create_app() -> FastAPI:
    app = FastAPI(title="Binance Live Heatmap")
    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.include_router(router)
    app.state.live_heatmap = LiveHeatmapService()

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        service: LiveHeatmapService = app.state.live_heatmap
        await websocket.accept()
        await service.register(websocket)
        try:
            while True:
                message = await websocket.receive_text()
                command = json.loads(message)
                await service.handle_websocket_command(command)
        except (WebSocketDisconnect, RuntimeError):
            await service.unregister(websocket)

    return app


app = create_app()
