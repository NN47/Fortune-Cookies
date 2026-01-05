import logging
import os
import random
from pathlib import Path
from typing import Dict, List
import json
import aiohttp

from dotenv import load_dotenv
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tarot_data import (
    build_feelings_answer,
    get_tarot_prediction,
    get_tarot_ru_name,
    get_tarot_short_prediction,
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
    port = int(os.environ.get("HEALTHCHECK_PORT", os.environ.get("PORT", "10000")))
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
DISCLAIMER_TEXT = "<i>Интерпретации носят символический и развлекательный характер.</i>"
TAROT_DIR = Path(os.environ.get("TAROT_DIR", "tarot"))
TAROT_TITLE_IMAGE_PATH = Path(
    os.environ.get("TAROT_TITLE_IMAGE", "assets/Title Card.png")
)
TAROT_SPLASH_IMAGE_PATH = Path(
    os.environ.get("TAROT_SPLASH_IMAGE", "assets/Splash Screen.png")
)
TAROT_CARD_BACK_IMAGE_PATH = Path(
    os.environ.get("TAROT_CARD_BACK_IMAGE", "assets/Card Back.png")
)
TAROT_FEELINGS_FILE = Path(
    os.environ.get("TAROT_FEELINGS_FILE", TAROT_DIR / "feelings_combinations.json")
)
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-beta")
GROK_API_URL = os.environ.get("GROK_API_URL", "https://api.x.ai/v1/chat/completions")
GROK_TIMEOUT_SECONDS = int(os.environ.get("GROK_TIMEOUT_SECONDS", "30"))

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
    "0. The Fool": (
        "0. The Fool — Шут (Дурак). Год проб и ошибок с новыми возможностями: "
        "работа, переезд или учёба. Поддерживай любопытство и экспериментируй шаг "
        "за шагом, но контролируй импульсивность в финансах и отношениях."
    ),
    "I. The Magician": (
        "I. The Magician — Маг. Время проявить инициативу и превращать идеи в "
        "действия: рост навыков, стартапов, творчества. Успех придёт через фокус, "
        "чёткую презентацию себя и регулярные шаги. Избегай манипуляций и пустых "
        "обещаний."
    ),
    "II. The High Priestess": (
        "II. The High Priestess — Верховная Жрица. Год внутренней работы: "
        "дневник, медитация и доверие интуиции. Возможны скрытые факты — не "
        "торопись с выводами, собирай данные. Береги баланс эмоций и времени в "
        "одиночестве."
    ),
    "III. The Empress": (
        "III. The Empress — Императрица. Период роста всех «садов»: карьера, "
        "семья, творчество. Благоприятно для проектов заботы и красоты, улучшений "
        "дома, расширения семьи. Стабильный режим, забота о теле и тёплые связи "
        "дадут поддержку."
    ),
    "IV. The Emperor": (
        "IV. The Emperor — Император. Год структуры и управления: формализуй "
        "процессы, наведи порядок в финансах и обязанностях. Возможен рост "
        "статуса и поддержка авторитетов, но оставляй место гибкости и диалога."
    ),
    "V. The Hierophant": (
        "V. The Hierophant — Иерофант (Верховный Жрец). Время учиться и делиться "
        "опытом. Польза от наставников, сертификатов, институций. Этика и "
        "договорённости важны; хорошо идут проекты образования, консультаций и "
        "духовности."
    ),
    "VI. The Lovers": (
        "VI. The Lovers — Влюблённые. Ключевой год выбора и партнёрства. В "
        "отношениях и работе важны общие ценности и прозрачная коммуникация. "
        "Возможны союзы, сделки, брак или честное решение разойтись, если "
        "приоритеты расходятся."
    ),
    "VII. The Chariot": (
        "VII. The Chariot — Колесница. Год продвижения через волю и «держать курс». "
        "Хорошо для карьерных рывков, переездов, активных поездок. Контролируй "
        "темперамент, не дави на близких; спорт и дедлайны помогут направить "
        "энергию."
    ),
    "VIII. Strength": (
        "VIII. Strength — Сила. Год мягкой настойчивости: терпение, эмпатия, "
        "уверенное «нет» без агрессии. Подходит для терапии, лечения страхов и "
        "укрепления здоровья. Отношения улучшаются через заботу и честный диалог."
    ),
    "IX. The Hermit": (
        "IX. The Hermit — Отшельник. Период углубления и самостоятельной работы: "
        "исследования, код, наука, духовные практики. Возможна смена окружения и "
        "временная изоляция. Сохраняй контакт с поддерживающим минимумом людей и "
        "не забывай про тело и сон."
    ),
    "X. Wheel of Fortune": (
        "X. Wheel of Fortune — Колесо Фортуны. Год перемен и резких поворотов: "
        "смена работы, места, финансов. Не всё под контролем — адаптируйся, "
        "лови окна возможностей, имей запасной план и подушку безопасности."
    ),
    "XI. Justice": (
        "XI. Justice — Справедливость. В центре договоры, юридические вопросы и "
        "баланс обязанностей. Пересмотри контракты и границы, решения принимай на "
        "фактах и последствиях. Воздаяние за прошлые действия приходит быстрее."
    ),
    "XII. The Hanged Man": (
        "XII. The Hanged Man — Повешенный. Время паузы и смены взгляда. Проекты "
        "могут подвиснуть, чтобы ты увидел новые варианты. Полезны пересборка "
        "планов и отказ от лишнего; это период перезагрузки, а не спринта."
    ),
    "XIII. Death": (
        "XIII. Death — Смерть. Год завершений и переходов: уходят работа, "
        "отношения, привычки. Освободившееся место быстро заполнится новым. "
        "Поддерживай себя ритуалами завершения, терапией и планированием «новой "
        "главы»."
    ),
    "XIV. Temperance": (
        "XIV. Temperance — Умеренность. Период баланса и дозирования: восстановление "
        "здоровья, финансовая дисциплина, гармоничный график. Двигайся постепенно "
        "— устойчивый результат важнее скорости. Учись смешивать подходы и искать "
        "золотую середину."
    ),
    "XV. The Devil": (
        "XV. The Devil — Дьявол. Фокус на искушениях и зависимостях: переработки, "
        "лишние траты, токсичные связи. Год показывает, где ты связан страхом или "
        "выгодой. Освобождение приходит через честность с собой и детокс — "
        "цифровой, финансовый или эмоциональный."
    ),
    "XVI. The Tower": (
        "XVI. The Tower — Башня. Возможны резкие изменения, ломающие старые схемы: "
        "неожиданности в работе или быту. Шок — сигнал укрепить базу и оставить "
        "нежизнеспособное. Создай план Б, следи за документами и страховками."
    ),
    "XVII. The Star": (
        "XVII. The Star — Звезда. Год надежды, восстановления и вдохновения. После "
        "сложностей приходит облегчение и ясный ориентир. Благоприятно для "
        "творчества, медиа, волонтёрства. Верь в длинный горизонт и не требуй "
        "мгновенной отдачи."
    ),
    "XVIII. The Moon": (
        "XVIII. The Moon — Луна. Время тумана и подсознательных процессов: "
        "усиливаются сны, интуиция, тревожность. Проверяй факты, работай со "
        "страхами через психолога, медитацию, дневник. Не принимай крупные "
        "решения в тумане — ищи больше информации."
    ),
    "XIX. The Sun": (
        "XIX. The Sun — Солнце. Пик энергии и признания: проекты получают внимание, "
        "отношения светлеют. Хорошо для выступлений, обучения, творчества, "
        "путешествий. Делись успехами, но держи баланс отдыха и ясные договорённости."
    ),
    "XX. Judgement": (
        "XX. Judgement — Суд. Год итогов и пробуждения: шансы исправить ошибки и "
        "вернуться к идеям на новом уровне. Возможны важные решения о миссии, "
        "карьере, месте жизни. Прислушивайся к «зову» и закрывай незавершённое."
    ),
    "XXI. The World": (
        "XXI. The World — Мир. Завершение крупного цикла и выход на новый уровень: "
        "выпуск, релокация, масштабирование бизнеса, международные связи. Результаты "
        "становятся видимыми; интегрируй опыт, празднуй достижения и ставь следующую "
        "цель."
    ),
}

SECOND_HALF_QUESTIONS: List[str] = [
    "Какой он этот человек по отношению к тебе?",
    "Какие мысли у этого человека по отношению к тебе?",
    "Скучает ли этот человек по тебе?",
    "Что на душе у этого человека по отношению к тебе?",
    "Что хотел бы сказать?",
    "Какие чувства?",
    "Хотел бы встречи?",
    "О чем жалеет этот человек по отношению к тебе?",
    "Каким видит тебя?",
    "Какие видит перспективы этот человек по отношению к тебе?",
]


def get_user_second_half_questions(user_data: Dict) -> List[str]:
    saved = [q.strip() for q in user_data.get("second_half_questions", []) if q.strip()]
    if saved:
        return saved

    return SECOND_HALF_QUESTIONS


def stop_collecting_second_half(user_data: Dict) -> None:
    user_data.pop("collecting_second_half", None)


def load_tarot_feelings_map() -> Dict[frozenset[str], str]:
    if not TAROT_FEELINGS_FILE.exists():
        raise RuntimeError(
            "Файл с ответами о чувствах не найден. Сгенерируй feelings_combinations.json"
            " или укажи TAROT_FEELINGS_FILE."
        )

    try:
        entries = json.loads(TAROT_FEELINGS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # noqa: B904
        raise RuntimeError(
            f"Не удалось прочитать {TAROT_FEELINGS_FILE}: {exc}"
        ) from exc

    mapping: Dict[frozenset[str], str] = {}
    for entry in entries:
        cards = entry.get("cards")
        answer = entry.get("answer")
        if not isinstance(cards, list) or len(cards) != 2 or not isinstance(answer, str):
            continue
        mapping[frozenset(cards)] = answer

    if not mapping:
        raise RuntimeError("В файле чувств нет корректных записей.")

    return mapping



def get_tarot_feelings_answer(first_name: str, second_name: str) -> str:
    try:
        mapping = load_tarot_feelings_map()
    except RuntimeError:
        return build_feelings_answer(first_name, second_name)

    key = frozenset({first_name, second_name})
    return mapping.get(key) or build_feelings_answer(first_name, second_name)


async def ask_grok_about_second_half(questions: List[str]) -> str:
    api_key = os.environ.get("GROK_API_KEY")
    if not api_key:
        return (
            "GROK_API_KEY не настроен. Добавь ключ окружения, чтобы получать ответы"
            " от Grok."
        )

    formatted_questions = "\n".join(
        f"{idx + 1}. {question}" for idx, question in enumerate(questions)
    )
    prompt = (
        "Ответь на вопросы пользователя лаконично."
        " Каждый пункт начинай с номера, повторяй вопрос и давай ответ в 1-2"
        " предложения без лишних преамбул.\n\n"
        f"Вопросы:\n{formatted_questions}"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        timeout = aiohttp.ClientTimeout(total=GROK_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                GROK_API_URL, headers=headers, json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    LOGGER.error(
                        "Grok API returned %s: %s", response.status, error_text
                    )
                    return "Не удалось получить ответ от Grok. Попробуй позже."

                data = await response.json()
                choices = data.get("choices", [])
                if not choices:
                    LOGGER.error("Grok API returned no choices: %s", data)
                    return "Grok вернул пустой ответ. Попробуй еще раз."

                content = choices[0]["message"].get("content", "").strip()
                return content or "Grok не смог сформулировать ответ."
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Failed to contact Grok: %s", exc)
        return "Произошла ошибка при обращении к Grok. Попробуй еще раз позже."


def build_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(str(i), callback_data=f"fortune_{i}")
        for i in range(1, FORTUNES_PER_SESSION + 1)
    ]
    keyboard_layout = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    keyboard_layout.append(
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")]
    )
    return InlineKeyboardMarkup(keyboard_layout)


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [
            InlineKeyboardButton(
                "🃏 Карты Таро", callback_data="menu_tarot"
            )
        ],
        [InlineKeyboardButton("🥠 Печенье с предсказанием", callback_data="menu_fortune")],
        [InlineKeyboardButton("🎱 Шар предсказаний", callback_data="menu_ball")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_ball_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Получить ответ", callback_data="ball_answer")],
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_intro_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Получить предсказание на год", callback_data="tarot_pick")],
        [InlineKeyboardButton("Расклад на вторую половину", callback_data="tarot_second_half")],
        [
            InlineKeyboardButton(
                "Что он/она чувствует к вам", callback_data="tarot_feelings"
            )
        ],
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_pick_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(str(i), callback_data=f"tarot_draw_{i}")
        for i in range(1, 7)
    ]
    keyboard_layout = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    keyboard_layout.append([InlineKeyboardButton("Назад", callback_data="tarot_intro")])
    keyboard_layout.append([InlineKeyboardButton("Главное меню", callback_data="menu_main")])
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_second_half_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Сделать расклад", callback_data="tarot_second_half_run")],
        [InlineKeyboardButton("Назад", callback_data="tarot_intro")],
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_second_half_result_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Сделать расклад", callback_data="tarot_second_half_run")],
        [InlineKeyboardButton("Назад", callback_data="tarot_intro")],
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_result_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Назад", callback_data="tarot_back")],
        [InlineKeyboardButton("Выбрать другую", callback_data="tarot_pick")],
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_feelings_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Вытянуть 2 карты", callback_data="tarot_feelings_run")],
        [InlineKeyboardButton("Назад", callback_data="tarot_intro")],
        [InlineKeyboardButton("Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_feelings_result_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Ещё один расклад", callback_data="tarot_feelings_run")],
        [InlineKeyboardButton("Назад", callback_data="tarot_intro")],
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
    stop_collecting_second_half(context.user_data)

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
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, show_disclaimer: bool = False
) -> None:
    user_data = context.user_data
    stop_collecting_second_half(user_data)
    should_add_disclaimer = show_disclaimer and not user_data.get("disclaimer_shown")

    caption_parts = [
        "✨ Добро пожаловать ✨",
        "",
        "Здесь ты можешь выбрать один из трёх способов",
        "получить знак или предсказание:",
        "",
        "🃏 Карты Таро",
        "🥠 Печенье с предсказанием",
        "🎱 Шар предсказаний",
        "",
        "Выбирай то, что откликается сейчас.",
    ]

    if should_add_disclaimer:
        caption_parts.extend(["", DISCLAIMER_TEXT])
        user_data["disclaimer_shown"] = True

    caption = "\n".join(caption_parts)
    message = update.effective_message

    if MAIN_MENU_IMAGE_PATH.exists():
        await message.reply_photo(
            photo=MAIN_MENU_IMAGE_PATH.read_bytes(),
            caption=caption,
            reply_markup=build_main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            text=caption,
            reply_markup=build_main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )


async def send_ball_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stop_collecting_second_half(context.user_data)

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
    stop_collecting_second_half(context.user_data)

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
    stop_collecting_second_half(context.user_data)

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


async def send_tarot_second_half(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_data = context.user_data
    user_data["collecting_second_half"] = True

    saved_questions = len(user_data.get("second_half_questions", []))
    caption = (
        "Загадай имя человека, введи свои вопросы одним или несколькими сообщениями"
        " и нажми кнопку ниже ✨\n\n"
        "Мы используем уже сохранённые вопросы"
    )
    if saved_questions:
        caption += f" (сейчас их {saved_questions})."
    else:
        caption += ", либо подставим стандартные."
    message = update.effective_message

    if TAROT_CARD_BACK_IMAGE_PATH.exists():
        await message.reply_photo(
            photo=TAROT_CARD_BACK_IMAGE_PATH.read_bytes(),
            caption=caption,
            reply_markup=build_tarot_second_half_keyboard(),
        )
    else:
        await message.reply_text(
            text=caption,
            reply_markup=build_tarot_second_half_keyboard(),
        )


async def send_tarot_feelings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stop_collecting_second_half(context.user_data)

    caption = (
        "🃏 Колода уже перетасована — тяни две карты и узнай, что чувствует"
        " загаданный человек к тебе. Нажми кнопку ниже, чтобы увидеть, какие"
        " арканы откроются. ✨🔮"
    )
    message = update.effective_message

    if TAROT_CARD_BACK_IMAGE_PATH.exists():
        await message.reply_photo(
            photo=TAROT_CARD_BACK_IMAGE_PATH.read_bytes(),
            caption=caption,
            reply_markup=build_tarot_feelings_keyboard(),
        )
    else:
        await message.reply_text(
            text=caption,
            reply_markup=build_tarot_feelings_keyboard(),
        )


async def handle_second_half_question_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if not message or not message.text:
        return

    user_data = context.user_data
    if not user_data.get("collecting_second_half"):
        return

    questions = user_data.setdefault("second_half_questions", [])
    max_questions = len(SECOND_HALF_QUESTIONS)

    if len(questions) >= max_questions:
        await message.reply_text(
            "Уже сохранено максимум вопросов. Нажми «Сделать расклад»,"
            " чтобы получить ответы."
        )
        return

    questions.append(message.text.strip())
    await message.reply_text(
        f"Вопрос №{len(questions)} сохранён. Можешь добавить ещё или нажми"
        " «Сделать расклад»."
    )


def compose_second_half_spread(cards: List[Path]) -> str:
    required_cards = len(SECOND_HALF_QUESTIONS) * 2
    selected_cards = random.sample(cards, required_cards)

    lines = []
    for idx, question in enumerate(SECOND_HALF_QUESTIONS):
        card_pair = selected_cards[idx * 2 : idx * 2 + 2]
        card_names = " + ".join(card.stem for card in card_pair)
        short_predictions = " / ".join(
            get_tarot_short_prediction(card.name) for card in card_pair
        )
        lines.append(
            f"{idx + 1}. {question}\n"
            f"🃏 Карты: {card_names}\n"
            f"💬 Значение: {short_predictions}"
        )

    spread_text = "Расклад на вторую половину года:\n\n" + "\n\n".join(lines)
    spread_text += (
        "\n\n✨ Ответы рождаются из сочетания выбранных арканов."
        " Смотри на пары в контексте вопроса."
    )
    return spread_text


async def send_tarot_second_half_result(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    questions = get_user_second_half_questions(context.user_data)
    answer_text = await ask_grok_about_second_half(questions)

    spread_text = (
        "Расклад на вторую половину года ✨\n\n"
        f"{answer_text}\n\n"
        "Отправь новые вопросы, чтобы обновить расклад, и нажми «Сделать"
        " расклад» ещё раз."
    )

    await query.message.reply_text(
        text=spread_text,
        reply_markup=build_tarot_second_half_result_keyboard(),
    )


async def send_tarot_feelings_result(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query

    try:
        cards = load_tarot_cards()
    except RuntimeError as exc:
        LOGGER.error("Tarot cards error: %s", exc)
        await query.answer("Карты недоступны. Попробуй позже.", show_alert=True)
        return

    if len(cards) < 2:
        await query.answer("Недостаточно карт для расклада.", show_alert=True)
        return

    first_card, second_card = random.sample(cards, 2)
    first_name, second_name = first_card.stem, second_card.stem
    first_ru_name, second_ru_name = (
        get_tarot_ru_name(first_name),
        get_tarot_ru_name(second_name),
    )
    answer_text = get_tarot_feelings_answer(first_name, second_name)
    first_display = f"{first_name} — {first_ru_name}"
    second_display = f"{second_name} — {second_ru_name}"

    await query.message.reply_media_group(
        [
            InputMediaPhoto(media=first_card.read_bytes(), caption=first_display),
            InputMediaPhoto(media=second_card.read_bytes(), caption=second_display),
        ]
    )

    caption = (
        "Что он/она чувствует к вам? 💞\n\n"
        f"{answer_text}\n\n"
        f"🃏 Карты: {first_display} + {second_display}\n"
    )

    await query.message.reply_text(
        text=caption,
        reply_markup=build_tarot_feelings_result_keyboard(),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_main_menu(update, context, show_disclaimer=True)


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

    if query.data == "tarot_back":
        await send_tarot_pick(update, context)
        return

    if query.data == "tarot_intro":
        await send_tarot_intro(update, context)
        return

    if query.data == "tarot_second_half":
        await send_tarot_second_half(update, context)
        return

    if query.data == "tarot_second_half_run":
        await send_tarot_second_half_result(update, context)
        return

    if query.data == "tarot_feelings":
        await send_tarot_feelings(update, context)
        return

    if query.data == "tarot_feelings_run":
        await send_tarot_feelings_result(update, context)
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

    webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL")
    webhook_path = os.environ.get("TELEGRAM_WEBHOOK_PATH", "")
    webhook_port = int(os.environ.get("PORT", "10000"))

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
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_second_half_question_input)
    )

    if webhook_url:
        full_webhook_url = webhook_url.rstrip("/")
        if webhook_path:
            full_webhook_url = f"{full_webhook_url}/{webhook_path.lstrip('/')}"

        LOGGER.info("Bot started in webhook mode at %s", full_webhook_url)
        application.run_webhook(
            listen="0.0.0.0",
            port=webhook_port,
            webhook_url=full_webhook_url,
            url_path=webhook_path,
            drop_pending_updates=True,
        )
    else:
        LOGGER.info("Bot started. Waiting for updates via polling…")
        application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL")

    # Healthcheck нужен только в режиме polling. Webhook уже слушает HTTP-порт.
    if not webhook_url:
        Thread(target=start_health_server, daemon=True).start()

    main()
