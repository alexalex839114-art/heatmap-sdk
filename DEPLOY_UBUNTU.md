# Деплой на Ubuntu 24.04 VPS

Простая пошаговая инструкция: загрузить архив → распаковать → один раз поставить → запускать/останавливать одной командой.

Сервер слушает **только `127.0.0.1:8000`** — наружу ничего не торчит. К UI ты подключаешься через SSH-туннель: один дополнительный флаг в команде `ssh` и UI открывается у тебя на ноутбуке как `http://localhost:8000`. Это самый безопасный вариант — никаких публичных портов, никакого firewall настраивать не надо.

Все команды на VPS — это обычный bash. Скрипты идемпотентны, безопасно запускать повторно.

---

## Шаг 1. Скачать архив проекта

На GitHub:

1. Открой https://github.com/alexalex839114-art/heatmap-sdk/tree/devin/1778830830-okx-replace-coinbase-kraken
2. Зелёная кнопка **«Code»** → **«Download ZIP»**.
3. Сохрани файл — он будет называться примерно `heatmap-sdk-devin-1778830830-okx-replace-coinbase-kraken.zip`.

> Можно вместо ZIP клонировать через git — см. блок «Альтернатива» в самом низу.

---

## Шаг 2. Загрузить архив на VPS

На своём ноутбуке (Windows PowerShell или macOS/Linux Terminal):

```bash
# Замени user@vps.example.com на свои данные
scp ~/Downloads/heatmap-sdk-devin-*.zip user@vps.example.com:~/
```

`scp` — это копирование через SSH. Файл попадёт в твой home-каталог на сервере.

---

## Шаг 3. Распаковать на сервере

Подключись по SSH:

```bash
ssh user@vps.example.com
```

Распакуй и переименуй (для удобства):

```bash
sudo apt-get update && sudo apt-get install -y unzip
unzip ~/heatmap-sdk-devin-*.zip
mv ~/heatmap-sdk-devin-* ~/heatmap-sdk
cd ~/heatmap-sdk
```

Теперь все команды ниже выполняй из `~/heatmap-sdk`.

---

## Шаг 4. Установить зависимости (одноразово)

```bash
bash deploy/install.sh
```

Этот скрипт сделает:

1. Поставит `python3`, `python3-venv`, `python3-pip` через `apt` (если их нет). Для этого один раз спросит твой `sudo`-пароль.
2. Создаст виртуальное окружение в `.venv/`.
3. Поставит зависимости из `requirements.txt` (`fastapi`, `uvicorn`, `numpy`, и т.д.).
4. Скопирует `.env.example` → `.env`, если `.env` не существует.

Дальше в `.env` нужно вписать твои Binance API-ключи:

```bash
nano .env
```

Заполни:

```
BINANCE_API_KEY=твой_ключ
BINANCE_API_SECRET=твой_секрет
```

Остальные поля можно оставить как есть. Сохрани (Ctrl+O, Enter), выйди (Ctrl+X).

> **Безопасность.** Файл `.env` остаётся только на сервере и в репозиторий не коммитится (`.gitignore` его игнорирует). API-ключи в логи и в код никуда не пишутся.

---

## Шаг 5. Запустить сервер

```bash
bash deploy/start.sh
```

Что произойдёт:

- Сервер стартанёт в **фоне** (можно безопасно отключиться от SSH — продолжит работать).
- Пишет PID в `.server.pid`.
- Логи пишутся в `logs/server.log`.
- Сервер слушает `127.0.0.1:8000`.

В терминале появится подсказка вида:

```
[start] To open the UI from your laptop, run this on your LAPTOP
[start] (NOT on the server), keeping the SSH session alive:

    ssh -N -L 8000:127.0.0.1:8000 user@vps.example.com

[start] Then open http://localhost:8000 in your browser.
```

---

## Шаг 6. Открыть UI с ноутбука

На **своём ноутбуке** (НЕ на сервере) открой **новый** терминал и запусти:

```bash
ssh -N -L 8000:127.0.0.1:8000 user@vps.example.com
```

Этот терминал нужно держать открытым всё время, пока ты работаешь с UI.

- `-N` — не запускать shell, только туннель.
- `-L 8000:127.0.0.1:8000` — прокинуть локальный порт 8000 на VPS-овский 127.0.0.1:8000.

Теперь открой в браузере на ноутбуке:

**http://localhost:8000**

Должна загрузиться страница heatmap-приложения. Введи символ (например, `BTCUSDT`), нажми **Connect WS** — поедут данные с 4 бирж.

> **Если порт 8000 у тебя на ноутбуке уже занят** — поменяй левую часть, например `-L 8123:127.0.0.1:8000`, и открой `http://localhost:8123`.

---

## Шаг 7. Остановить сервер

На VPS (через SSH):

```bash
cd ~/heatmap-sdk
bash deploy/stop.sh
```

Скрипт пошлёт SIGTERM, подождёт 5 секунд, при необходимости — SIGKILL. PID-файл удалится.

После этого можно закрывать оба SSH-окна.

---

## Дополнительно

### Посмотреть статус и логи

```bash
bash deploy/status.sh         # запущен ли, какой PID, последние 5 строк лога
tail -f logs/server.log       # потоковые логи в реальном времени (Ctrl+C для выхода)
```

### Запустить на другом порту

```bash
bash deploy/start.sh 8123
```

Не забудь поменять правую часть `-L` в команде ssh-туннеля: `-L 8000:127.0.0.1:8123`.

### Обновление до новой версии

```bash
# 1. На VPS — останови
cd ~/heatmap-sdk
bash deploy/stop.sh

# 2. На ноутбуке — скачай новый ZIP, залей scp'ом (Шаги 1-2)

# 3. На VPS — распакуй поверх и подтяни зависимости
cd ~
unzip -o ~/heatmap-sdk-devin-*.zip   # -o = overwrite without prompting
# (или mv -f, в зависимости от того, как ты называл папки)
cd heatmap-sdk
bash deploy/install.sh   # обновит зависимости из новой requirements.txt
bash deploy/start.sh
```

Файл `.env` сохраняется между обновлениями — переписывать ключи заново не надо.

### Альтернатива: git вместо ZIP

Если на VPS есть `git`:

```bash
sudo apt-get install -y git
git clone https://github.com/alexalex839114-art/heatmap-sdk.git ~/heatmap-sdk
cd ~/heatmap-sdk
git checkout devin/1778830830-okx-replace-coinbase-kraken
bash deploy/install.sh
```

Обновляться будет так:

```bash
cd ~/heatmap-sdk
bash deploy/stop.sh
git pull
bash deploy/install.sh
bash deploy/start.sh
```

---

## Troubleshooting

| Симптом | Что делать |
|---|---|
| `deploy/install.sh: Permission denied` | `chmod +x deploy/*.sh` |
| `bash: deploy/install.sh: No such file or directory` | Не та папка. `cd ~/heatmap-sdk` и попробуй снова. |
| `[start] virtual environment not found` | Не сделал шаг 4. Запусти `bash deploy/install.sh`. |
| `[start] server already running (pid ...)` | Уже запущено. `bash deploy/stop.sh` сначала. |
| `http://localhost:8000` не открывается на ноутбуке | Проверь, что SSH-туннель в шаге 6 открыт **и не закрыт** (терминал с `ssh -N -L ...` должен висеть). На VPS: `bash deploy/status.sh` — сервер запущен? |
| `address already in use` в `logs/server.log` | Порт 8000 на VPS занят другим процессом. Стартуй на другом порту: `bash deploy/start.sh 8123`. |
| Ошибки про `BINANCE_API_KEY` | Не заполнил `.env`. `nano .env`, впиши ключи, `bash deploy/stop.sh && bash deploy/start.sh`. |

---

## Структура файлов

```
~/heatmap-sdk/
├── .env                  # твои ключи (не в git'е, остаётся между обновлениями)
├── .server.pid           # PID запущенного процесса (создаётся start.sh)
├── .venv/                # виртуальное окружение Python
├── deploy/
│   ├── install.sh        # одноразовая установка
│   ├── start.sh          # запуск в фоне
│   ├── stop.sh           # остановка
│   └── status.sh         # статус
├── logs/
│   └── server.log        # все логи uvicorn
├── app/                  # бэкенд
├── static/               # фронтенд (HTML/JS/CSS)
└── ... остальной код
```
