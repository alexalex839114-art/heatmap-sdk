# Локальный запуск (Windows)

Эта сборка содержит:
- **PR #2** — позиция через Binance user-data WebSocket, REST-поллинг снижен до 10 с (fallback), race-condition закрыт watermark-проверкой.
- **PR #3** — Coinbase и Kraken отключены (код не удалён, методы остались для будущей реактивации), вместо них подключены **OKX перпы (SWAP)** — `BTC-USDT-SWAP` и т.п., линейный USDT-маржинальный perpetual, прямой аналог Binance USDⓈ-M Futures, и **Gate.io USDT-маржинальные перпы** — `BTC_USDT`, `ETH_USDT` и т.п. UI показывает четыре половинки: `BIN / BYB / OKX / GATE`.
- **Graceful fallback для отсутствующих perp'ов:** если конкретный символ не листится на OKX (например `IRYSUSDT` → OKX не имеет `IRYS-USDT-SWAP`) или на Gate, соответствующий индикатор уходит в состояние `unavailable` без падения. Confluence продолжает считаться по оставшимся биржам. (Часто `IRYSUSDT` есть на Gate, но нет на OKX — тогда работает BIN+BYB+GATE.)

Все 212 Python-тестов и 27 JS-тестов зелёные локально.

## Требования

- Windows 10/11 с PowerShell 5+ (стандартный).
- **Python 3.10+** (тестировано на 3.12). Проверь: `python --version`.
- Доступ в интернет (порт 443 — для Binance / Bybit / OKX / Gate.io WebSocket).
- Свободный TCP-порт 8000 на `127.0.0.1` (скрипт сам найдёт следующий свободный, если занят).

## 1. Получить код

```powershell
git clone https://github.com/alexalex839114-art/heatmap-sdk.git
cd heatmap-sdk
git checkout devin/1778830830-okx-replace-coinbase-kraken
```

Это финальная ветка, в которой сидят оба PR (#2 и #3). Если уже клонировал — `git fetch && git checkout devin/1778830830-okx-replace-coinbase-kraken && git pull`.

## 2. Создать `.env`

```powershell
copy .env.example .env
notepad .env
```

Заполни:

```env
BINANCE_API_KEY=твой_ключ
BINANCE_API_SECRET=твой_секрет
BINANCE_FAPI_BASE_URL=https://fapi.binance.com
AUTO_TRADE_ENABLED=false
AUTO_EXIT_ENABLED=false
```

- Ключи нужны **только** для трекинга позиции и market-close. Тепловая карта и сигналы работают и без ключей — но тогда без `position`-эвентов от user-data WS.
- `AUTO_TRADE_ENABLED=false` и `AUTO_EXIT_ENABLED=false` — безопасные дефолты. Авто-выход включается из UI после `Connect WS`.
- На ключе **отключи withdrawal**, оставь только USD-M Futures trade.

## 3. Запустить

Через PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Или двойным кликом по `start.bat`.

Что произойдёт:
1. Создастся `.venv` (если ещё нет).
2. Установятся зависимости из `requirements.txt` (одноразово).
3. Стартанёт `uvicorn app.main:app` на `http://127.0.0.1:8000`. Если 8000 занят — скрипт возьмёт следующий свободный и напишет, какой.

Открой в браузере: **http://127.0.0.1:8000**

Логи остаются в консоли — там видно подключения к Binance/Bybit/OKX/Gate, listen key Binance, и поток `position`-эвентов.

## 4. Смоук-чек (что должно работать)

1. В поле `Symbol` ввести `BTCUSDT`, `Compression = 1`, нажать **Connect WS**.
2. В строке статуса (внизу слева) должна появиться надпись `live_ready` (в пределах ~10–20 сек).
3. Нажать **Start Heatmap** — справа налево пойдёт прокрутка тепловой карты.
4. **Signal-lights** (под кнопкой) — три ряда (BUY / WAIT / SELL), в каждом ряду четыре половинки с подписями **BIN / BYB / OKX / GATE**. Никаких CB или KR быть не должно.
   - Для популярных символов (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`) все четыре половинки зажигаются через секунды.
   - Для редких альтов, у которых нет перпа на конкретной бирже (напр. `IRYSUSDT` отсутствует на OKX, но есть на Gate), эта половинка остаётся неактивной, а в детальной строке индикатора будет `OKX: unavailable IRYS-USDT-SWAP (OKX does not list ...; confluence uses Binance + Bybit only)`. Confluence считается по оставшимся биржам. Это **ожидаемое поведение**, не ошибка.
5. Половинки начинают подсвечиваться по мере того, как каждая биржа доходит до `ready`. Если какая-то долго в `warming` — нормально, ассистент ждёт прогрева VPIN.
6. В правой панели "Indicators" пойдут блоки от четырёх бирж: `binance`, `bybit`, `okx`, `gate`. **Никаких `coinbase` / `kraken`** в списке быть не должно.

### Проверка PR #2 (user-data WS)

- Открой DevTools → Network → WS, посмотри сообщения по `/ws`. После того как ассистент получит первое `ACCOUNT_UPDATE` (открой/закрой минимальную позицию на Binance), в UI поле **Position** должно обновиться **в течение секунды**, а не по 10-секундному циклу.
- В консоли uvicorn должна появиться запись `account update received` (или похожая) практически сразу после изменения позиции на бирже.
- Можно намеренно "уронить" сеть на пару секунд — REST-fallback подхватит через ~10 сек.

### Проверка PR #3 (OKX и Gate вместо Coinbase/Kraken)

- В консоли uvicorn не должно быть строк, начинающихся с `coinbase` / `kraken` (старт индикаторов).
- В DevTools → Network → WS видно события `indicator_status` от `okx` и `gate`:
  - `state: "ready"` для популярных символов (`BTCUSDT`, `ETHUSDT`).
  - `state: "unavailable"` для редких альтов, которых на этой бирже нет (например `IRYSUSDT` сейчас отсутствует на OKX, но есть на Gate).
  - `state: "error"` — только если действительно проблема с подключением (firewall, сеть).
- В UI правая колонка "Indicators" показывает blocks для `okx` с теми же полями, что и для `binance` / `bybit` (state, vpin, conf и т.п.) — но только когда OKX в `ready` и начал считать VPIN.

## 5. Остановить

`Ctrl+C` в окне `uvicorn`. Сессия закрыта, venv остаётся на диске для следующего запуска.

## 6. Прогнать тесты (необязательно)

```powershell
.\.venv\Scripts\Activate.ps1
pytest
node --test tests\test_assistant_view.mjs tests\test_renderer_palette.mjs tests\test_signal_css.mjs
```

Ожидаемо: **200 Python-тестов + 26 JS** — все зелёные.

## 7. Если что-то не так

| Симптом | Что проверить |
|---|---|
| `python` не найден | Поставить Python 3.10+, перезайти в PowerShell |
| Скрипт жалуется на ExecutionPolicy | Запускай `start.bat`, либо `Set-ExecutionPolicy -Scope CurrentUser Bypass` |
| UI грузится, но `live_ready` не появляется | Проверь firewall — нужны исходящие `wss://fstream.binance.com`, `wss://stream.bybit.com`, `wss://ws.okx.com:8443` |
| `position` молчит | Проверь `BINANCE_API_KEY/SECRET` в `.env`, что у ключа есть права на USD-M Futures, и что время на машине не плывёт (Binance строг по `recvWindow`) |
| Порт 8000 занят | Скрипт сам возьмёт 8001/8002… — смотри строку `Starting server at http://127.0.0.1:XXXX` |
| Тепловая карта пустая | Жди ~5–10 сек после Connect WS — нужны несколько снимков стакана |

## 8. Что новое относительно того, что у тебя было

| Файл | Что изменилось |
|---|---|
| `app/ws_session.py` | Подключён `BinanceUserDataClient`, добавлен `_on_user_data_account_update`, REST-fallback каждые 10 с вместо 100 мс, watermark `_last_ws_position_update_ms` для защиты от race. OKX-индикатор стартует, Coinbase/Kraken — нет. |
| `app/binance_user_data.py` | Новый клиент: listen key + 30-мин keepalive + WS на `wss://fstream.binance.com/ws/<key>` + reconnect с backoff. |
| `app/okx_client.py` | Новый клиент OKX (v5 public WS, `books` + `trades`). Отдельная exception `OkxUnsupportedInstrumentError` — для случая когда конкретный SWAP не листится; ловится в `_start_okx_indicator` и переводит OKX в `state: unavailable` вместо `state: error`. |
| `app/exchange_symbols.py` | Добавлена `to_okx_inst_id('BTCUSDT') -> 'BTC-USDT-SWAP'` (перп OKX, не спот). |
| `app/settings.py` | Константы `BINANCE_FAPI_USER_WS_URL`, `OKX_PUBLIC_WS_V5_URL`, `POSITION_REST_FALLBACK_INTERVAL_MS=10_000`, `LISTEN_KEY_KEEPALIVE_INTERVAL_MS=30*60_000`. |
| `static/index.html`, `static/app.js`, `static/assistant_view.js` | Третий слот — `okx` вместо `coinbase`+`kraken`. Confluence `BUY x2` теперь "2 из 3". |
| `tests/` | +3 теста на race-condition (`_refresh_position`), +11 тестов на `OkxMarketClient` (включая 3 новых на unsupported-instrument), JS-тесты обновлены под `BUY x3`. |
