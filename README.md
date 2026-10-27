# ozon-morkovsk

Telegram-бот для игры «Морковск» на Ozon. Выполняет задания-визиты, забирает ежедневный бонус, крутит бесплатное лукошко и напоминает о лотерее. Поддерживает несколько пользователей и аккаунтов, у каждого аккаунта свой прокси.

## Возможности

- задания «посетите раздел» — `collectVisitReward` → `inAppPushAction`;
- задания с корзиной — по переключателю на аккаунте, кладут в корзину реальный товар;
- ежедневный бонус и бесплатное лукошко, платное не трогается;
- напоминания о конце продажи билетов лотереи за 24, 6 и 1 час;
- вход по QR-коду, номеру телефона или cookie;
- автообновление сессии: новый access-token сохраняется, мёртвая сессия поднимается через fingerprint-chromium;
- статистика баланса за день, неделю, месяц и всё время;
- выдача и отзыв доступа владельцем бота.

## Как работает

Игра опрашивается через `curl_cffi` с TLS-отпечатком Safari. Без мобильных маркеров (`MOBILE_APP_TYPE=ozonapp_ios`, `x-o3-*`) `/gameapp` отдаёт пустую страницу, поэтому они добавляются в cookie.

Браузер нужен только для входа и восстановления сессии. Используется [fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium) в окне под Xvfb: в headless-режиме Ozon выдаёт анонимный токен.

Ошибка сети, 403 от антибота или недоступный прокси не считаются протуханием сессии — прогон пропускается. Сессия считается мёртвой, только когда Ozon выдал анонимный токен (`userid=0`).

Прогоны идут раз в 30–45 минут, задания перемешиваются, между действиями случайные паузы.

## Установка

Нужны Linux, Python 3.12+, Xvfb и fingerprint-chromium.

```bash
git clone https://github.com/schmdtt/ozon-morkovsk.git
cd ozon-morkovsk
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/playwright install-deps chromium
cp .env.example .env
```

Переменные `.env`:

| Переменная | Обязательна | Описание |
|---|---|---|
| `BOT_TOKEN` | да | токен бота |
| `OWNER_ID` | да | Telegram ID владельца |
| `FP_CHROMIUM` | да | путь к `chrome` из fingerprint-chromium |
| `DATA_DIR` | нет | папка для базы, по умолчанию `./data` |
| `TELEGRAM_API_URL` | нет | свой Bot API сервер |
| `FP_HEADLESS` | нет | `1` — восстановление сессии без окна |

## Запуск

```bash
Xvfb :99 -screen 0 1320x900x24 -nolisten tcp &
export DISPLAY=:99
set -a; . ./.env; set +a
venv/bin/python -m morkovsk
```

Сервис systemd:

```ini
[Unit]
Description=ozon-morkovsk
After=network.target

[Service]
WorkingDirectory=/opt/ozon-morkovsk
EnvironmentFile=/opt/ozon-morkovsk/.env
Environment=DISPLAY=:99 PYTHONUNBUFFERED=1
ExecStart=/opt/ozon-morkovsk/venv/bin/python -m morkovsk
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Утилиты

Запускаются из корня репозитория:

```bash
venv/bin/python -m tools.show_tasks cookie.txt [proxy]
venv/bin/python -m tools.refresh_session cookie.txt [proxy]
venv/bin/python -m tools.check_auth_state
```

- `show_tasks` — состояние сессии, баланс, билеты и список заданий;
- `refresh_session` — восстанавливает сессию и перезаписывает файл с cookie;
- `check_auth_state` — проверяет на первом аккаунте из базы, что сбой сети не принимается за мёртвую сессию.

## Структура

```
morkovsk/
├── __main__.py       точка входа
├── config.py         настройки из окружения
├── storage.py        SQLite: пользователи, аккаунты, история баланса
├── farming.py        прогоны, планировщик, напоминания о лотерее
├── bot/
│   ├── client.py     экземпляр бота
│   ├── handlers.py   хендлеры
│   ├── onboarding.py добавление аккаунта: cookie, QR, телефон
│   └── ui.py         тексты и клавиатуры
└── ozon/
    ├── session.py    cookie, токены, прокси
    ├── game.py       клиент игры и прогон
    ├── browser.py    fingerprint-chromium, восстановление сессии
    └── login.py      вход через браузер, запускается отдельным процессом
tools/                служебные скрипты
```

Проект неофициальный и не связан с Ozon. Автоматизация может нарушать правила площадки.

## Лицензия

MIT
