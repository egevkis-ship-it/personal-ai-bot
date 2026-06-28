# Деплой в облако — пошагово

Мультипользовательский сервис, вход через Telegram, общая база с ботом.
Код уже готов (`api/`, `web/`, `Dockerfile.api`, авторизация в `api/auth.py`).
Тебе осталось ~6 шагов; всё, что можно, я подготовил заранее.

> Почему это нельзя сделать полностью за тебя: у Telegram нет API на привязку
> домена к боту (`/setdomain` — только руками в BotFather), а создание нового
> сервиса в Coolify требует входа в твою панель. Остальное уже в коде.

---

## Шаг 0. Запушить код (1 команда)

В терминале в папке проекта:

```bash
git add api web Dockerfile.api docker-compose.app.yml *.md && git commit -m "web app + telegram auth" && git push
```

---

## Шаг 1. BotFather — привязать домен и узнать имя бота

1. Реши, на каком домене будет приложение, напр. **`app.egorkis.com`** (понадобится в шаге 3).
2. В Telegram открой **@BotFather** → `/setdomain` → выбери своего бота → пришли домен:
   `https://app.egorkis.com`
3. Там же запомни **username бота** (без `@`) — он нужен в шаге 4 (`TELEGRAM_BOT_USERNAME`).

> Без этого шага кнопка входа Telegram просто не появится.

---

## Шаг 2. Coolify — создать приложение

1. В Coolify: **New Resource → Application → Public/Private Repository**, выбери этот репозиторий, ветку `main`.
2. **Build Pack: Dockerfile**, Dockerfile path: `Dockerfile.api`.
3. **Port:** `8000`. **Health check path:** `/healthz`.
4. Пока не нажимай Deploy — сначала переменные (шаг 4) и домен (шаг 3).

---

## Шаг 3. Домен

В настройках приложения в Coolify пропиши домен `https://app.egorkis.com`
(тот же, что отдал BotFather). HTTPS Coolify/Traefik выдаст сам.

> На DNS у домена должна быть A-запись на сервер Coolify (как у бота).

---

## Шаг 4. Переменные окружения (вставить в Coolify → Environment)

```
APP_ENV=production
SEED=0
DATABASE_URL=<строка подключения к базе бота, как postgresql+asyncpg://...>
REDIS_URL=redis://unused:6379/0
TELEGRAM_BOT_TOKEN=<токен твоего бота — тот же, что у сервиса бота в Coolify>
TELEGRAM_BOT_USERNAME=Stoned_Assistant_Bot
ALLOWED_TELEGRAM_USER_IDS=
SESSION_SECRET=uFdTMrHHbzG5shKmJryeNxRSIbpWKqxJIBrPhqv3W854i6lTssUui6Rg56qm-A07
WEB_ORIGIN=https://app.egorkis.com
ANTHROPIC_API_KEY=<любой плейсхолдер, ИИ пока не используется>
OPENAI_API_KEY=<плейсхолдер>
```

Пояснения:
- **DATABASE_URL** — возьми ту же базу, что у бота (тогда данные общие). В Coolify
  можно подключить тот же ресурс Postgres по внутреннему хосту. Драйвер обязательно
  `postgresql+asyncpg://` (не просто `postgresql://`).
- **TELEGRAM_BOT_TOKEN** — нужен для проверки подписи входа; возьми из переменных
  сервиса бота в Coolify.
- **ALLOWED_TELEGRAM_USER_IDS** — оставь **пустым**, чтобы пускать любого Telegram-пользователя
  (мультипользовательский режим). Если хочешь ограничить — впиши ID через запятую.
- **SESSION_SECRET** — уже сгенерирован, можно оставить как есть.

---

## Шаг 5. Deploy

Нажми **Deploy** в Coolify. Дождись зелёного healthcheck (`/healthz`).

Проверка из терминала (необязательно):
```bash
curl https://app.egorkis.com/healthz        # {"ok":true,...}
curl -i https://app.egorkis.com/api/dashboard   # 401 без входа — так и должно быть
```

---

## Шаг 6. Войти

1. Открой `https://app.egorkis.com` на телефоне.
2. Нажми кнопку **Log in with Telegram** → подтверди.
3. Готово: увидишь свои данные (если у этого Telegram-аккаунта есть данные в боте — они уже здесь).
4. «Поделиться» → **«На экран Домой»** — иконка как у приложения.

---

## Проверка, что всё связано с ботом

- Запиши в приложении подход/замер → проверь, что бот это видит (и наоборот).
- Если у нового пользователя данных нет — это норм: они появятся по мере использования.

## Обновления в будущем

Пуш в `main` → Coolify пересоберёт (или дёрни существующий деплой-вебхук). База и
схема трогаться не будут — таблицы создаются идемпотентно.

## Если что-то не так

- **Кнопки входа нет** → не сделан `/setdomain` или неверный `TELEGRAM_BOT_USERNAME`.
- **`Bot domain invalid`** при входе → домен в BotFather ≠ домен сайта.
- **401 после входа** → проверь `SESSION_SECRET` задан и домен в `WEB_ORIGIN` совпадает.
- **Пустые экраны/ошибка БД** → неверный `DATABASE_URL` или не `+asyncpg`.
