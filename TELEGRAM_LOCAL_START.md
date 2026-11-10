# ЮЗЫ — Telegram Mini App на локальном ПК (без Railway)

## 1. Бот

В @BotFather выполните `/newbot` и сохраните токен. Публичный `@username` бота можно сообщать другим; токен — секрет.

## 2. Настройка проекта (PowerShell, Windows)

Откройте папку с `app.py` в VS Code.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Откройте `.env` и задайте настоящий `BOT_TOKEN` из BotFather и случайный `SESSION_SECRET`:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Вставьте результат в `SESSION_SECRET`. `LOCAL_DEMO=0`. Не сохраняйте `.env` в GitHub.

## 3. Запуск Flask

В **первом терминале**:

```powershell
.\.venv\Scripts\waitress-serve.exe --host=127.0.0.1 --port=5000 app:app
```

`http://127.0.0.1:5000/health` вернёт `{"status":"ok"}`. Открытие обычного сайта без Telegram не авторизует игровой API — это ожидаемо.

## 4. Временный HTTPS через Cloudflare

Установите cloudflared с https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
или проверьте официальный способ установки для вашей ОС.

Во **втором терминале**:

```powershell
cloudflared tunnel --url http://127.0.0.1:5000
```

Появится случайный адрес `https://...trycloudflare.com`. Сохраните его — это временная ссылка на ваш ПК.
Не закрывайте ни первый, ни второй терминал, и не выключайте ПК.

## 5. Подключение Mini App

BotFather → `/mybots` → бот → Bot Settings → Configure Mini App → Enable Mini App.
Укажите точный `https://...trycloudflare.com` из второго терминала.
При необходимости через `/setmenubutton` задайте текст «Играть» и тот же HTTPS URL.

Откройте Mini App **из Telegram**, а не по прямой ссылке в браузере.
Telegram передаёт подписанное `initData`, backend проверяет его по токену и создаёт игрока `tg:<telegram_id>` в SQLite.
Игра покажет `@username` или имя, а если фото доступно — аватар.

## 6. Проверка привязки аккаунта

1. Откройте бота под аккаунтом Telegram A: профиль должен показать имя A. Выполните крутку, закройте приложение, откройте снова — прогресс сохранится.
2. Откройте бота под аккаунтом Telegram B — баланс и коллекция будут отдельными, имя B в профиле.
3. В обычном браузере через публичный URL без Telegram API ответит 401.

Когда туннель перезапустится, адрес поменяется — обновите ссылку в BotFather. Это тестовый, а не постоянный хостинг.

## Безопасность

- `LOCAL_DEMO=0`: через туннель **нельзя** оставлять демонстрационный доступ.
- `python app.py` теперь запускается без debug, но для туннеля лучше использовать waitress.
- Не отправляйте токен бота, `.env` и БД никому, не размещайте их в GitHub.
- Публичная ссылка доступна извне; используйте туннель только для тестов. Не принимайте платежи и не запускайте массовую игру с ПК.
- Данные демо-игрока `demo:...` отдельно от настоящего Telegram-аккаунта `tg:...`, они автоматически не переносятся.
