# Задание: деплой прототипа в облако (Coolify) с входом через Telegram

Это задание для кодового агента/разработчика. Цель — выкатить уже существующий
прототип (`api/` + `web/`) в Coolify как отдельный сервис рядом с ботом, закрыть
доступ Telegram-логином и подключить его к **той же базе, что и бот**, чтобы данные
приложения и бота были общими.

Контекст и текущее состояние читать в `RUN_LOCAL.md`, `api/main.py`, `web/`,
`Dockerfile.api`, `docker-compose.app.yml`, `app/modules/ops/coolify.py`.

---

## 0. Решения (зафиксированы)

- Хостинг: **существующий Coolify** (как у бота), сборка из этого репозитория по `Dockerfile.api`.
- Авторизация: **Telegram Login Widget**. После входа `user_id` = настоящий Telegram-ID.
- База: **общая с ботом** (тот же Postgres). Схема создаётся идемпотентно (`CREATE IF NOT EXISTS`), это безопасно.
- Один разрешённый пользователь (твой Telegram-ID), список расширяемый через env.

---

## 1. Бэкенд: аутентификация (новый модуль `api/auth.py`)

Реализовать вход по Telegram Login Widget.

**1.1. Проверка подписи Telegram.** Виджет отдаёт поля `id, first_name, last_name,
username, photo_url, auth_date, hash`. Проверка:

```
secret_key   = SHA256(<TELEGRAM_BOT_TOKEN>)            # bytes
data_check   = "\n".join(f"{k}={v}" for k,v in sorted(fields_without_hash.items()))
expected     = HMAC_SHA256(secret_key, data_check).hexdigest()
assert hmac.compare_digest(expected, hash)
assert now - int(auth_date) < 86400                    # подпись не старше суток
assert str(id) in ALLOWED_TELEGRAM_USER_IDS            # allowlist
```

**1.2. Сессия.** Выдать подписанный токен сессии (JWT через `pyjwt` или
`itsdangerous.TimestampSigner`), payload = `{uid: <telegram_id>}`. Положить в
**httpOnly + Secure + SameSite=Lax** cookie `session` (срок ~30 дней).

**1.3. Эндпоинты:**
- `POST /api/auth/telegram` — принимает JSON полей виджета, проверяет, ставит cookie, возвращает `{ok:true, user_id}`.
- `GET  /api/auth/me` — возвращает текущего пользователя или `401`.
- `POST /api/auth/logout` — удаляет cookie.
- `GET  /api/config` — публичный, возвращает `{ "bot_username": "<TELEGRAM_BOT_USERNAME>" }` (нужно фронту для виджета).

**1.4. Защита API.** Заменить нынешний `auth_mw` (статический `API_TOKEN`) на проверку
cookie-сессии для всех `/api/*`, **кроме** `/api/auth/telegram`, `/api/config` и
статики. При отсутствии/неверной сессии → `401`.

**1.5. user_id из сессии.** Убрать глобальную константу `USER` из env. `user_id` для
всех запросов брать из сессии (прокидывать через `request.state.uid` в middleware и
читать в хендлерах, либо зависимость FastAPI `Depends(current_uid)`). Это и даёт общую
с ботом базу: ID совпадает с тем, что бот пишет в `user_id`.

> Нужно отрефакторить хендлеры `api/main.py`, которые сейчас используют модульный `USER`,
> на получение `uid` из запроса.

---

## 2. Фронтенд: экран входа (`web/`)

- Добавить экран логина: если `GET /api/auth/me` вернул `401`, показать страницу с
  **Telegram Login Widget**:
  ```html
  <script async src="https://telegram.org/js/telegram-widget.js?22"
    data-telegram-login="ВZЯТЬ_ИЗ /api/config" data-size="large"
    data-onauth="onTelegramAuth(user)" data-request-access="write"></script>
  ```
  `bot_username` подставлять динамически из `GET /api/config` (создавать тег скрипта в JS).
- `onTelegramAuth(user)` → `POST /api/auth/telegram` телом `user` → при `200` перезагрузить приложение.
- В `api()`-хелпере (`web/app.js`) добавить `credentials: 'include'` и обработку `401`
  (показывать экран входа). Кнопку «Выйти» в настройках → `POST /api/auth/logout`.

---

## 3. Прод-готовность (правки в `api/main.py`)

- **Отключить авто-сид** в проде: `maybe_seed()` вызывать только если `APP_ENV != "production"`
  (или env `SEED=1`). На общей с ботом базе сид не нужен.
- **CORS**: убрать `allow_origins=["*"]`; в проде разрешить только свой домен (env `WEB_ORIGIN`).
- **/healthz** (публичный): возвращает `200` + проверку соединения с БД (`SELECT 1`) — для healthcheck Coolify.
- Запуск под нагрузкой: оставить `uvicorn` (одного воркера достаточно для личного исп.);
  при желании `--workers 2`.

---

## 4. Coolify

1. **Приложение**: New Resource → Application → из этого Git-репозитория.
   - Build Pack: **Dockerfile**, путь `Dockerfile.api`.
   - Exposed port: `8000`. Healthcheck path: `/healthz`.
2. **База**: использовать **существующий Postgres бота**. Прописать его connection string
   в `DATABASE_URL` (драйвер `postgresql+asyncpg://`). Если бот и app в одном проекте Coolify —
   подключить тот же ресурс БД по внутреннему хосту.
3. **Домен**: задать поддомен (напр. `app.<твойдомен>`), Coolify/Traefik выдаст HTTPS автоматически.
4. **Переменные окружения** (env):
   ```
   APP_ENV=production
   SEED=0
   DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>:5432/<db>   # БД бота
   REDIS_URL=redis://unused:6379/0
   TELEGRAM_BOT_TOKEN=<реальный токен бота>          # для проверки подписи логина
   TELEGRAM_BOT_USERNAME=<имя_бота_без_@>
   ALLOWED_TELEGRAM_USER_IDS=<твой_telegram_id>      # можно список через запятую
   SESSION_SECRET=<случайная_строка_32+>
   WEB_ORIGIN=https://app.<твойдомен>
   ANTHROPIC_API_KEY=<если нужен ИИ позже, иначе placeholder>
   OPENAI_API_KEY=<placeholder>
   ```
5. **BotFather**: `/setdomain` → указать домен приложения (`app.<твойдомен>`). Без этого
   Telegram Login Widget не отрендерится.
6. Deploy. Можно использовать уже имеющийся механизм (`app/modules/ops/coolify.py`,
   webhook/API) для последующих редеплоев.

---

## 5. Безопасность (обязательно)

- Доступ только по cookie-сессии; allowlist по Telegram-ID. Никаких открытых `/api/*`.
- Cookie: `HttpOnly`, `Secure`, `SameSite=Lax`. `SESSION_SECRET` — длинный и секретный.
- Проверять свежесть `auth_date` (≤ 24 ч) и `hmac.compare_digest` (защита от тайминга).
- Не логировать токен/секреты.

---

## 6. Критерии приёмки

- Открытие `https://app.<домен>` без входа показывает экран Telegram-логина; `/api/*` отдаёт `401`.
- Вход через Telegram под разрешённым ID → попадаешь в приложение; чужой ID → отказ.
- На дашборде видны **реальные данные из базы бота** (сегодняшний план/история/замеры совпадают с ботом).
- Запись подхода/замера из приложения видна боту, и наоборот (общая БД).
- `/healthz` отвечает `200`; Coolify healthcheck зелёный.
- `SEED` не затронул прод-базу (демо-данные не появились).
- Перезаход (новая вкладка) не требует повторного логина (сессия живёт), «Выйти» работает.

---

## 7. Объём, которого пока нет (не блокирует деплой)

Фото, PDF-отчёты, ИИ-генерация планов и ИИ-резюме — вне прототипа. Резюме тренировки
сейчас считается без ИИ. Это ок для первого облачного релиза; добавляется позже.
