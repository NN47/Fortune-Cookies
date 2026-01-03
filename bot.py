import logging
import os
import random
from pathlib import Path
from typing import Dict, List
import json

from dotenv import load_dotenv
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

# -------------------------------------------------
# ЛОГИРОВАНИЕ
# -------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)

# Глушим подробные HTTP-логи, чтобы не светился токен в URL
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.request").setLevel(logging.WARNING)


# -------------------------------------------------
# HEALTHCHECK HTTP-СЕРВЕР ДЛЯ RENDER / UPTIMEROBOT
# -------------------------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def _send_headers(self, status=200, content_type="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()

    def do_HEAD(self):
        self._send_headers()

    def do_GET(self):
        # 1) HEALTHCHECK endpoint
        if self.path.startswith("/health"):
            self._send_headers()
            self.wfile.write(b"OK")
            return

        # 2) STATIC IMAGES: /images/... → отдаём файлы печенек
        if self.path.startswith("/images/"):
            local_path = self.path.lstrip("/")  # "images/xxx.jpg"
            if os.path.exists(local_path):
                mime = "image/jpeg"
                if local_path.endswith(".png"):
                    mime = "image/png"
                elif local_path.endswith(".webp"):
                    mime = "image/webp"

                with open(local_path, "rb") as f:
                    data = f.read()

                self._send_headers(200, mime)
                self.wfile.write(data)
                return
            else:
                self._send_headers(404)
                self.wfile.write(b"Not Found")
                return

        # 3) MAIN PAGE — HTML with cookie preview
        # Берём 8 случайных картинок
        supported = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        all_images = [
            str(p) for p in FORTUNE_COLLECTION_DIR.iterdir()
            if p.suffix.lower() in supported
        ]
        chosen = random.sample(all_images, min(8, len(all_images)))

        # Превращаем в пути для браузера — "/images/filename.jpg"
        browser_paths = [
            "/images/" + Path(p).name for p in chosen
        ]

        images_js = json.dumps(browser_paths, ensure_ascii=False)

        # HTML + JS
        html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>Fortune Cookies</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body {{
      background: #0d0d18;
      font-family: system-ui;
      color: #eee;
      display: flex;
      justify-content: center;
      align-items: center;
      height: 100vh;
      margin: 0;
    }}
    .card {{
      background: rgba(255,255,255,0.05);
      border-radius: 20px;
      padding: 20px;
      width: 420px;
      backdrop-filter: blur(12px);
      box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    }}
    h1 {{
      text-align: center;
      margin-bottom: 15px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4,1fr);
      gap: 10px;
      margin-bottom: 20px;
    }}
    button {{
      background: #f8d57a;
      border: none;
      border-radius: 12px;
      padding: 12px 0;
      font-size: 16px;
      cursor: pointer;
    }}
    #result {{
      text-align: center;
      min-height: 160px;
    }}
    #result img {{
      max-width: 100%;
      border-radius: 14px;
      box-shadow: 0 4px 18px rgba(0,0,0,0.5);
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>🥠 Выбери печенье</h1>
    <div class="grid">
      <button data-i="0">1</button>
      <button data-i="1">2</button>
      <button data-i="2">3</button>
      <button data-i="3">4</button>
      <button data-i="4">5</button>
      <button data-i="5">6</button>
      <button data-i="6">7</button>
      <button data-i="7">8</button>
    </div>

    <div id="result">Нажми на кнопку, чтобы увидеть предсказание</div>
  </div>

  <script>
    const images = {images_js};

    document.querySelectorAll("button").forEach(btn => {{
      btn.addEventListener("click", () => {{
        const i = btn.dataset.i;
        const url = images[i];
        document.getElementById("result").innerHTML =
          `<img src="${{url}}" alt="fortune" />`;
      }});
    }});
  </script>
</body>
</html>
"""
        self._send_headers()
        self.wfile.write(html.encode("utf-8"))



def start_health_server():
    port = int(os.environ.get("PORT", "10000"))  # Render подставит свой PORT
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    LOGGER.info("Healthcheck server started on port %s", port)
    server.serve_forever()


# -------------------------------------------------
# ЛОГИКА БОТА
# -------------------------------------------------
FORTUNE_COLLECTION_DIR = Path(os.environ.get("FORTUNE_COLLECTION_DIR", "images"))
FORTUNE_MENU_IMAGE_PATH = Path(os.environ.get("FORTUNE_MENU_IMAGE", "assets/menu.jpg"))
MAIN_MENU_IMAGE_PATH = Path(os.environ.get("MAIN_MENU_IMAGE", "assets/main.png"))
FORTUNE_BALL_DIR = Path(os.environ.get("FORTUNE_BALL_DIR", "fortune ball"))
FORTUNES_PER_SESSION = 8
TAROT_DIR = Path(os.environ.get("TAROT_DIR", "tarot"))
TAROT_TITLE_IMAGE_PATH = Path(
    os.environ.get("TAROT_TITLE_IMAGE", "assets/Title Card.png")
)
TAROT_SPLASH_IMAGE_PATH = Path(
    os.environ.get("TAROT_SPLASH_IMAGE", "assets/Splash Screen.png")
)

# In-memory storage that keeps track of which fortune image corresponds to which button
user_sessions: Dict[int, List[Path]] = {}


def load_fortune_images() -> List[Path]:
    if not FORTUNE_COLLECTION_DIR.exists():
        raise RuntimeError(
            f"Fortune image directory '{FORTUNE_COLLECTION_DIR}' does not exist. "
            "Place your fortune images there or set FORTUNE_COLLECTION_DIR."
        )

    supported_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    images = [
        path
        for path in FORTUNE_COLLECTION_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in supported_suffixes
    ]

    if len(images) < FORTUNES_PER_SESSION:
        raise RuntimeError(
            "Not enough images in the fortune collection directory. "
            f"Need at least {FORTUNES_PER_SESSION}, found {len(images)}"
        )

    return images


def load_ball_images() -> List[Path]:
    if not FORTUNE_BALL_DIR.exists():
        raise RuntimeError(
            f"Fortune ball image directory '{FORTUNE_BALL_DIR}' does not exist. "
            "Place your ball images there or set FORTUNE_BALL_DIR."
        )

    supported_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    images = [
        path
        for path in FORTUNE_BALL_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in supported_suffixes
    ]

    if not images:
        raise RuntimeError(
            "No images found in the fortune ball directory. "
            "Add at least one image to show answers."
    )

    return images


def load_tarot_cards() -> List[Path]:
    if not TAROT_DIR.exists():
        raise RuntimeError(
            f"Tarot card directory '{TAROT_DIR}' does not exist. "
            "Place tarot cards there or set TAROT_DIR."
        )

    supported_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    images = [
        path for path in TAROT_DIR.iterdir() if path.suffix.lower() in supported_suffixes
    ]

    if not images:
        raise RuntimeError(
            "No tarot cards found. Add images to the tarot directory to continue."
        )

    return images


TAROT_PREDICTIONS: Dict[str, str] = {
    "0. The Fool": "Новые начала и смелые шаги приведут к неожиданным открытиям.",
    "I. The Magician": "Используй свои таланты — сегодня ты способен на многое.",
    "II. The High Priestess": "Доверься интуиции, ответы скрыты глубже, чем кажется.",
    "III. The Empress": "Твоя забота даст плод — прояви теплоту и получишь поддержку.",
    "IV. The Emperor": "Чёткий план и дисциплина помогут навести порядок.",
    "V. The Hierophant": "Следуй проверенным традициям — мудрость рядом.",
    "VI. The Lovers": "Важное решение о союзе — слушай сердце и будь честен.",
    "VII. The Chariot": "Время действовать решительно — уверенность принесёт успех.",
    "VIII. Strength": "Спокойная сила и терпение помогут преодолеть преграды.",
    "IX. The Hermit": "Небольшое уединение даст ясность и новые инсайты.",
    "X. Wheel of Fortune": "Колесо судьбы вращается — будь готов к приятным переменам.",
    "XI. Justice": "Справедливость восторжествует — действуй честно и прозрачно.",
    "XII. The Hanged Man": "Посмотри на ситуацию под другим углом, и решение найдётся.",
    "XIII. Death": "Закрой старую главу, чтобы освободить место новому.",
    "XIV. Temperance": "Сохраняй баланс — гармония придёт через умеренность.",
    "XV. The Devil": "Не поддавайся соблазнам — помни о своих истинных целях.",
    "XVI. The Tower": "Неожиданные перемены разрушат лишнее, открывая путь обновлению.",
    "XVII. The Star": "Надежда и вдохновение ведут тебя — верь своим мечтам.",
    "XVIII. The Moon": "Не всё ясно — прислушайся к чувствам и избегай иллюзий.",
    "XIX. The Sun": "Радость и успех рядом — делись теплом и получишь больше.",
    "XX. Judgement": "Настало время сделать выбор и двинуться вперёд без сомнений.",
    "XXI. The World": "Завершение цикла — празднуй достижение и готовься к новому пути.",
}


def get_tarot_prediction(card_name: str) -> str:
    base_name = Path(card_name).stem
    return TAROT_PREDICTIONS.get(
        base_name,
        "Карта шепчет о переменах. Прислушайся к знакам и действуй уверенно.",
    )


def build_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(str(i), callback_data=f"fortune_{i}")
        for i in range(1, FORTUNES_PER_SESSION + 1)
    ]
    keyboard_layout = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    return InlineKeyboardMarkup(keyboard_layout)


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [
            InlineKeyboardButton(
                "🥠 Печенье с предсказаниями", callback_data="menu_fortune"
            )
        ],
        [InlineKeyboardButton("🔮 Шар предсказаний", callback_data="menu_ball")],
        [InlineKeyboardButton("🃏 Таро", callback_data="menu_tarot")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_ball_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Получить ответ", callback_data="ball_answer")]
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_intro_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Выбрать свою карту", callback_data="tarot_pick")],
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_pick_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(str(i), callback_data=f"tarot_draw_{i}")
        for i in range(1, 7)
    ]
    keyboard_layout = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    keyboard_layout.append(
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")]
    )
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_result_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Выбрать другую", callback_data="tarot_pick")],
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_fortune_result_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Узнать еще", callback_data="menu_fortune")],
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_ball_result_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Спросить еще", callback_data="ball_answer")],
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def select_random_fortunes(images: List[Path]) -> List[Path]:
    return random.sample(images, FORTUNES_PER_SESSION)


async def send_fortune_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    images = load_fortune_images()
    selected_fortunes = select_random_fortunes(images)
    user_id = update.effective_user.id
    user_sessions[user_id] = selected_fortunes

    caption = "Выбери своё печенье с предсказанием!"
    message = update.effective_message

    if FORTUNE_MENU_IMAGE_PATH.exists():
        await message.reply_photo(
            photo=FORTUNE_MENU_IMAGE_PATH.read_bytes(),
            caption=caption,
            reply_markup=build_menu_keyboard(),
        )
    else:
        await message.reply_text(
            text=caption,
            reply_markup=build_menu_keyboard(),
        )


async def send_main_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    caption = "Добро пожаловать к волшебному духу магических предсказаний ✨🔮"
    message = update.effective_message

    if MAIN_MENU_IMAGE_PATH.exists():
        await message.reply_photo(
            photo=MAIN_MENU_IMAGE_PATH.read_bytes(),
            caption=caption,
            reply_markup=build_main_menu_keyboard(),
        )
    else:
        await message.reply_text(
            text=caption,
            reply_markup=build_main_menu_keyboard(),
        )


async def send_ball_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    caption = "Загадай про себя свой вопрос, и шар даст волшебный ответ ✨🔮"
    message = update.effective_message
    ball_image_path = Path("assets/ball.png")

    if ball_image_path.exists():
        await message.reply_photo(
            photo=ball_image_path.read_bytes(),
            caption=caption,
            reply_markup=build_ball_menu_keyboard(),
        )
    else:
        await message.reply_text(
            text=caption,
            reply_markup=build_ball_menu_keyboard(),
        )


async def send_tarot_intro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    caption = "Открой двери в мир таро и получи своё предсказание ✨"
    message = update.effective_message

    if TAROT_TITLE_IMAGE_PATH.exists():
        await message.reply_photo(
            photo=TAROT_TITLE_IMAGE_PATH.read_bytes(),
            caption=caption,
            reply_markup=build_tarot_intro_keyboard(),
        )
    else:
        await message.reply_text(
            text=caption,
            reply_markup=build_tarot_intro_keyboard(),
        )


async def send_tarot_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    caption = "Выбери карту и получи свое предсказание."
    message = update.effective_message

    if TAROT_SPLASH_IMAGE_PATH.exists():
        await message.reply_photo(
            photo=TAROT_SPLASH_IMAGE_PATH.read_bytes(),
            caption=caption,
            reply_markup=build_tarot_pick_keyboard(),
        )
    else:
        await message.reply_text(
            text=caption,
            reply_markup=build_tarot_pick_keyboard(),
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_main_menu(update, context)


async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_fortune_menu(update, context)


async def handle_fortune_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    fortunes = user_sessions.get(user_id)

    if not fortunes:
        await query.edit_message_caption(
            caption="Сессия устарела. Отправь /start, чтобы получить новые печенья."
        )
        return

    try:
        index = int(query.data.split("_")[1]) - 1
    except (IndexError, ValueError):
        LOGGER.warning("Invalid callback data received: %s", query.data)
        await query.answer("Что-то пошло не так, попробуй снова.", show_alert=True)
        return

    if not 0 <= index < len(fortunes):
        await query.answer("Это печенье недоступно. Попробуй другое.", show_alert=True)
        return

    fortune_path = fortunes[index]
    with fortune_path.open("rb") as fortune_file:
        await query.message.reply_photo(
            photo=fortune_file,
            reply_markup=build_fortune_result_keyboard(),
        )


async def handle_menu_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "menu_fortune":
        await send_fortune_menu(update, context)
    elif query.data == "menu_ball":
        await send_ball_menu(update, context)
    elif query.data == "menu_tarot":
        await send_tarot_intro(update, context)
    elif query.data == "menu_main":
        await send_main_menu(update, context)


async def handle_tarot_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "tarot_pick":
        await send_tarot_pick(update, context)
        return

    if not query.data.startswith("tarot_draw_"):
        await query.answer("Неизвестная команда таро.", show_alert=True)
        return

    try:
        cards = load_tarot_cards()
    except RuntimeError as exc:
        LOGGER.error("Tarot cards error: %s", exc)
        await query.answer("Карты недоступны. Попробуй позже.", show_alert=True)
        return

    card_path = random.choice(cards)
    caption = f"{card_path.stem}\n\n{get_tarot_prediction(card_path.name)}"

    with card_path.open("rb") as card_file:
        await query.message.reply_photo(
            photo=card_file,
            caption=caption,
            reply_markup=build_tarot_result_keyboard(),
        )


async def handle_ball_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        images = load_ball_images()
    except RuntimeError as exc:
        LOGGER.error("Fortune ball images error: %s", exc)
        await query.answer("Шар пока молчит. Попробуй позже.", show_alert=True)
        return

    ball_image = random.choice(images)
    with ball_image.open("rb") as ball_file:
        await query.message.reply_photo(
            photo=ball_file,
            reply_markup=build_ball_result_keyboard(),
        )


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")

    application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("refresh", refresh))
    application.add_handler(CallbackQueryHandler(handle_menu_action, pattern="^menu_"))
    application.add_handler(
        CallbackQueryHandler(handle_ball_answer, pattern="^ball_answer$")
    )
    application.add_handler(CallbackQueryHandler(handle_tarot_action, pattern="^tarot_"))
    application.add_handler(
        CallbackQueryHandler(handle_fortune_selection, pattern="^fortune_")
    )

    LOGGER.info("Bot started. Waiting for updates...")
    application.run_polling()


if __name__ == "__main__":
    # Сначала поднимаем HTTP-сервер для Render/UptimeRobot
    Thread(target=start_health_server, daemon=True).start()
    # Потом запускаем бота
    main()
