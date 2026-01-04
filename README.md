# 🥠 Fortune Cookie Telegram Bot

Бот, который показывает меню печенек с предсказаниями и отправляет случайное изображение по нажатию кнопки.

## 🚀 Запуск локально

1. Установите зависимости:

pip install -r requirements.txt

2. Установите токен бота:

export TELEGRAM_BOT_TOKEN="твой токен"
export GROK_API_KEY="ключ от Grok"
# Вебхук-режим (опционально):
export TELEGRAM_WEBHOOK_URL="https://your-domain.com/telegram"
export TELEGRAM_WEBHOOK_PATH="telegram"

3. Запустите:

python bot.py

## 🤖 Ответы Grok для второго расклада

- Кнопка «Расклад на вторую половину» теперь использует нейросеть Grok. Заранее
  введи свои вопросы отдельными сообщениями в чат, а потом нажми кнопку — бот
  отправит ответы от модели.
- Переменные окружения:
  - `GROK_API_KEY` — обязательный ключ доступа;
  - `GROK_MODEL` и `GROK_API_URL` — опциональные, для настройки модели и
    эндпоинта (по умолчанию `grok-beta` и `https://api.x.ai/v1/chat/completions`).


## 📁 Структура проекта

- `bot.py` — основной код бота  
- `assets/menu.jpg` — картинка меню  
- `images/*` — изображения предсказаний  

## 🌐 Деплой на Render

Сделайте репозиторий публичным и подключите к Render.
Команда запуска:

python bot.py

### Polling против Webhook

- **По умолчанию** бот работает через polling и поднимает встроенный healthcheck
  сервер на порту `HEALTHCHECK_PORT` (по умолчанию совпадает с `PORT`).
- Чтобы избежать конфликтов `getUpdates` при нескольких инстансах, можно перейти
  в режим webhook: задайте `TELEGRAM_WEBHOOK_URL` и (опционально)
  `TELEGRAM_WEBHOOK_PATH`. В этом случае бот слушает HTTP-порт сам и отдельный
  healthcheck сервер не запускается.
