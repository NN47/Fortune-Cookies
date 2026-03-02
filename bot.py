import logging
import os
import random
import json
from pathlib import Path
from typing import Dict, List
from urllib.parse import unquote

from dotenv import load_dotenv
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from tarot_data import (
    get_person_spread_answer,
    get_tarot_prediction,
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
        request_path = unquote(self.path)

        # 1) HEALTHCHECK endpoint
        if request_path.startswith("/health"):
            self._send_headers()
            self.wfile.write(b"OK")
            return

        # 2) STATIC IMAGES: /images/... → отдаём файлы печенек
        if request_path.startswith("/images/"):
            local_path = request_path.lstrip("/")  # "images/xxx.jpg"
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

        if request_path.startswith("/assets/"):
            local_path = request_path.lstrip("/")
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

            self._send_headers(404)
            self.wfile.write(b"Not Found")
            return

        if request_path.startswith("/ball/"):
            file_name = request_path.removeprefix("/ball/")
            local_path = FORTUNE_BALL_DIR / file_name
            if local_path.exists() and local_path.is_file():
                mime = "image/jpeg"
                if local_path.suffix.lower() == ".png":
                    mime = "image/png"
                elif local_path.suffix.lower() == ".webp":
                    mime = "image/webp"

                self._send_headers(200, mime)
                self.wfile.write(local_path.read_bytes())
                return

            self._send_headers(404)
            self.wfile.write(b"Not Found")
            return

        if request_path.startswith("/tarot/"):
            file_name = request_path.removeprefix("/tarot/")
            local_path = TAROT_DIR / file_name
            if local_path.exists() and local_path.is_file():
                mime = "image/jpeg"
                if local_path.suffix.lower() == ".png":
                    mime = "image/png"
                elif local_path.suffix.lower() == ".webp":
                    mime = "image/webp"

                self._send_headers(200, mime)
                self.wfile.write(local_path.read_bytes())
                return

            self._send_headers(404)
            self.wfile.write(b"Not Found")
            return

        if request_path == "/tarot":
            supported_tarot = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            tarot_cards = [
                path for path in TAROT_DIR.iterdir()
                if path.is_file() and path.suffix.lower() in supported_tarot
            ]

            tarot_payload = []
            for card_path in tarot_cards:
                person_answers = [
                    get_person_spread_answer(index, question, card_path.name)
                    for index, question in enumerate(PERSON_SPREAD_QUESTIONS)
                ]
                tarot_payload.append(
                    {
                        "name": card_path.stem,
                        "path": f"/tarot/{card_path.name}",
                        "prediction": get_tarot_prediction(card_path.name),
                        "person_answers": person_answers,
                    }
                )

            tarot_cards_js = json.dumps(tarot_payload, ensure_ascii=False)
            person_questions_js = json.dumps(PERSON_SPREAD_QUESTIONS, ensure_ascii=False)

            html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>Tarot</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body {{ margin: 0; background: #0d0d18; color: #eee; font-family: system-ui; }}
    .wrap {{ max-width: 480px; margin: 0 auto; padding: 16px; }}
    .card {{ background: rgba(255,255,255,0.06); border-radius: 18px; padding: 16px; }}
    .title {{ text-align: center; margin: 0 0 12px; white-space: pre-line; }}
    .cover {{ width: 100%; border-radius: 12px; margin-bottom: 12px; }}
    .actions {{ display: grid; gap: 8px; margin-bottom: 12px; }}
    button {{ background: #f8d57a; border: none; border-radius: 10px; padding: 11px; font-size: 15px; cursor: pointer; }}
    .hidden {{ display: none; }}
    .grid {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 8px; margin-bottom: 12px; }}
    .result img {{ width: 100%; border-radius: 12px; margin-bottom: 8px; }}
    .result {{ white-space: pre-line; line-height: 1.4; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div id="intro-screen">
        <h3 class="title">Открой двери в мир таро и получи своё предсказание ✨</h3>
        <img class="cover" src="/assets/Title Card.png" alt="Tarot" />
        <div class="actions">
          <button id="year-mode">Получить предсказание на год</button>
          <button id="person-mode">Расклад на человека</button>
          <button onclick="location.href='/'">Главное меню</button>
        </div>
      </div>

      <div id="year-screen" class="hidden">
        <h3 class="title">Выбери карту и получи свое предсказание.</h3>
        <img class="cover" src="/assets/Splash Screen.png" alt="Pick" />
        <div class="grid" id="year-buttons"></div>
        <div class="result" id="year-result"></div>
        <div class="actions">
          <button id="year-back">Назад</button>
        </div>
      </div>

      <div id="person-screen" class="hidden">
        <h3 class="title">Загадай человека и начни расклад.</h3>
        <img class="cover" src="/assets/Card Back.png" alt="Person spread" />
        <div class="result" id="person-result">Нажми «Начать расклад», чтобы получить первый ответ.</div>
        <div class="actions">
          <button id="person-start">Начать расклад</button>
          <button id="person-prev" class="hidden">Назад</button>
          <button id="person-next" class="hidden">Следующий вопрос</button>
          <button id="person-home">Главное меню</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    const tarotCards = {tarot_cards_js};
    const personQuestions = {person_questions_js};
    const introScreen = document.getElementById('intro-screen');
    const yearScreen = document.getElementById('year-screen');
    const personScreen = document.getElementById('person-screen');
    const yearButtons = document.getElementById('year-buttons');
    const yearResult = document.getElementById('year-result');
    const personResult = document.getElementById('person-result');
    const personStart = document.getElementById('person-start');
    const personPrev = document.getElementById('person-prev');
    const personNext = document.getElementById('person-next');
    let personCards = [];
    let personStep = 0;

    const setScreen = (name) => {{
      introScreen.classList.toggle('hidden', name !== 'intro');
      yearScreen.classList.toggle('hidden', name !== 'year');
      personScreen.classList.toggle('hidden', name !== 'person');
    }};

    const pickRandomCard = () => tarotCards[Math.floor(Math.random() * tarotCards.length)];

    for (let i = 0; i < 8; i += 1) {{
      const button = document.createElement('button');
      button.textContent = String(i + 1);
      button.addEventListener('click', () => {{
        const card = pickRandomCard();
        yearResult.innerHTML = `<img src="${{card.path}}" alt="${{card.name}}" /><strong>${{card.name}}</strong>\n\n${{card.prediction}}`;
      }});
      yearButtons.appendChild(button);
    }}

    const renderPersonStep = () => {{
      const card = personCards[personStep];
      personResult.innerHTML = `<img src="${{card.path}}" alt="${{card.name}}" /><strong>Вопрос ${{personStep + 1}}/${{personQuestions.length}}</strong>\n🃏 Карта: ${{card.name}}\n\n${{card.person_answers[personStep]}}`;
      personStart.classList.add('hidden');
      personPrev.classList.toggle('hidden', personStep === 0);
      personNext.textContent = personStep === personQuestions.length - 1 ? 'Начать сначала' : 'Следующий вопрос';
      personNext.classList.remove('hidden');
    }};

    const resetPersonSpread = () => {{
      personCards = [];
      personStep = 0;
      personResult.textContent = 'Нажми «Начать расклад», чтобы получить первый ответ.';
      personStart.classList.remove('hidden');
      personPrev.classList.add('hidden');
      personNext.classList.add('hidden');
    }};

    document.getElementById('year-mode').addEventListener('click', () => setScreen('year'));
    document.getElementById('person-mode').addEventListener('click', () => {{
      resetPersonSpread();
      setScreen('person');
    }});
    document.getElementById('year-back').addEventListener('click', () => setScreen('intro'));
    document.getElementById('person-home').addEventListener('click', () => {{
      resetPersonSpread();
      setScreen('intro');
    }});
    personStart.addEventListener('click', () => {{
      personCards = [...tarotCards].sort(() => Math.random() - 0.5).slice(0, personQuestions.length);
      personStep = 0;
      renderPersonStep();
    }});
    personPrev.addEventListener('click', () => {{
      if (personStep > 0) {{
        personStep -= 1;
        renderPersonStep();
      }}
    }});
    personNext.addEventListener('click', () => {{
      if (personStep >= personQuestions.length - 1) {{
        personStep = 0;
      }} else {{
        personStep += 1;
      }}
      renderPersonStep();
    }});
  </script>
</body>
</html>
"""
            self._send_headers()
            self.wfile.write(html.encode("utf-8"))
            return

        # 3) MAIN PAGE — HTML with cookie preview
        # Берём 8 случайных картинок
        supported = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        all_images = [
            str(p) for p in FORTUNE_COLLECTION_DIR.iterdir()
            if p.suffix.lower() in supported
        ]
        chosen = random.sample(all_images, min(8, len(all_images)))
        supported_ball = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        ball_images = []
        if FORTUNE_BALL_DIR.exists():
            ball_images = [
                str(p) for p in FORTUNE_BALL_DIR.iterdir()
                if p.suffix.lower() in supported_ball
            ]

        # Превращаем в пути для браузера — "/images/filename.jpg"
        browser_paths = [
            "/images/" + Path(p).name for p in chosen
        ]
        ball_browser_paths = [
            "/ball/" + Path(p).name for p in ball_images
        ]

        images_js = json.dumps(browser_paths, ensure_ascii=False)
        ball_images_js = json.dumps(ball_browser_paths, ensure_ascii=False)

        # HTML + JS
        html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>Magical Spirit</title>
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
      width: min(460px, calc(100vw - 24px));
      backdrop-filter: blur(12px);
      box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    }}
    .hero-image {{
      width: 100%;
      border-radius: 14px;
      margin-bottom: 14px;
    }}
    .screen.hidden {{
      display: none;
    }}
    .fortune-title {{
      text-align: center;
      margin-bottom: 14px;
      line-height: 1.5;
      white-space: pre-line;
    }}
    .fortune-menu-image {{
      width: 100%;
      border-radius: 14px;
      margin-bottom: 14px;
      box-shadow: 0 4px 18px rgba(0,0,0,0.5);
    }}
    .caption {{
      white-space: pre-line;
      text-align: center;
      margin-bottom: 14px;
      line-height: 1.5;
    }}
    .menu-buttons,
    .grid {{
      display: grid;
      gap: 10px;
      margin-bottom: 20px;
    }}
    .menu-buttons {{
      grid-template-columns: 1fr;
    }}
    .grid {{
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
    .hidden {{
      display: none;
    }}
    #fortune-result {{
      text-align: center;
      min-height: 80px;
    }}
    #fortune-result img {{
      max-width: 100%;
      border-radius: 14px;
      box-shadow: 0 4px 18px rgba(0,0,0,0.5);
    }}
    #ball-caption {{
      text-align: center;
      margin-bottom: 12px;
      line-height: 1.5;
    }}
    #ball-preview {{
      max-width: 100%;
      border-radius: 14px;
      box-shadow: 0 4px 18px rgba(0,0,0,0.5);
      margin-bottom: 12px;
    }}
    .ball-actions {{
      display: grid;
      gap: 10px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="screen" id="main-screen">
      <img class="hero-image" src="/assets/main.png" alt="Главное меню" />
      <div class="caption">✨ Добро пожаловать ✨

Здесь ты можешь выбрать один из трёх способов
получить знак или предсказание:

🃏 Карты Таро
🥠 Печенье с предсказанием
🎱 Шар предсказаний

Выбирай то, что откликается сейчас.</div>

      <div class="menu-buttons">
        <button id="btn-tarot">🃏 Карты Таро</button>
        <button id="btn-fortune">🥠 Печенье с предсказанием</button>
        <button id="btn-ball">🎱 Шар предсказаний</button>
      </div>

      <div id="main-result">Нажми на один из разделов выше</div>
    </div>

    <div class="screen hidden" id="fortune-screen">
      <p class="fortune-title">Выбери печенье с предсказанием 🥠</p>
      <img class="fortune-menu-image" src="/assets/menu.jpg" alt="Печенье с предсказаниями" />
      <div class="grid" id="fortune-buttons">
        <button data-i="0">1</button>
        <button data-i="1">2</button>
        <button data-i="2">3</button>
        <button data-i="3">4</button>
        <button data-i="4">5</button>
        <button data-i="5">6</button>
        <button data-i="6">7</button>
        <button data-i="7">8</button>
      </div>
      <div id="fortune-result">Нажми на номер печенья, чтобы открыть предсказание ✨</div>
      <button id="btn-fortune-home">Главное меню</button>
    </div>

    <div class="screen hidden" id="ball-screen">
      <p id="ball-caption">Загадай про себя свой вопрос, и шар даст волшебный ответ ✨🔮</p>
      <img id="ball-preview" src="/assets/ball.png" alt="magic ball" />
      <div class="ball-actions">
        <button id="btn-ball-answer">Получить ответ</button>
        <button id="btn-ball-again" class="hidden">Спросить еще</button>
        <button id="btn-ball-home">Главное меню</button>
      </div>
    </div>
  </div>

  <script>
    const images = {images_js};
    const ballImages = {ball_images_js};
    const mainScreen = document.getElementById("main-screen");
    const fortuneScreen = document.getElementById("fortune-screen");
    const ballScreen = document.getElementById("ball-screen");
    const mainResult = document.getElementById("main-result");
    const fortuneResult = document.getElementById("fortune-result");
    const fortuneButtons = document.getElementById("fortune-buttons");
    const ballCaption = document.getElementById("ball-caption");
    const ballPreview = document.getElementById("ball-preview");
    const ballAgainButton = document.getElementById("btn-ball-again");

    const showMainScreen = () => {{
      mainScreen.classList.remove("hidden");
      fortuneScreen.classList.add("hidden");
      ballScreen.classList.add("hidden");
    }};

    const showFortuneScreen = () => {{
      mainScreen.classList.add("hidden");
      fortuneScreen.classList.remove("hidden");
      ballScreen.classList.add("hidden");
      fortuneResult.innerHTML = "Нажми на номер печенья, чтобы открыть предсказание ✨";
    }};

    const showBallScreen = () => {{
      mainScreen.classList.add("hidden");
      fortuneScreen.classList.add("hidden");
      ballScreen.classList.remove("hidden");
      ballCaption.textContent = "Загадай про себя свой вопрос, и шар даст волшебный ответ ✨🔮";
      ballPreview.src = "/assets/ball.png";
      ballAgainButton.classList.add("hidden");
    }};

    const showBallAnswer = () => {{
      if (!ballImages.length) {{
        ballCaption.textContent = "Изображения шара пока недоступны.";
        ballPreview.removeAttribute("src");
        ballAgainButton.classList.add("hidden");
        return;
      }}

      const randomImage = ballImages[Math.floor(Math.random() * ballImages.length)];
      ballCaption.textContent = "Ответ шара ✨";
      ballPreview.src = randomImage;
      ballAgainButton.classList.remove("hidden");
    }};

    document.getElementById("btn-tarot").addEventListener("click", () => {{
      window.location.href = "/tarot";
    }});

    document.getElementById("btn-fortune").addEventListener("click", () => {{
      showFortuneScreen();
    }});

    document.getElementById("btn-ball").addEventListener("click", () => {{
      showBallScreen();
    }});

    document.getElementById("btn-ball-answer").addEventListener("click", showBallAnswer);
    ballAgainButton.addEventListener("click", showBallAnswer);
    document.getElementById("btn-ball-home").addEventListener("click", showMainScreen);
    document.getElementById("btn-fortune-home").addEventListener("click", showMainScreen);

    document.querySelectorAll("#fortune-buttons button").forEach(btn => {{
      btn.addEventListener("click", () => {{
        const i = btn.dataset.i;
        const url = images[i];
        fortuneResult.innerHTML =
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

PERSON_SPREAD_QUESTIONS: List[str] = [
    "Какой он, загаданный человек, по отношению к тебе?",
    "Какие мысли о тебе у загаданного человека?",
    "Скучает ли по тебе?",
    "Что у загаданного человека на душе по отношению к тебе?",
    "Что хотел бы тебе сказать загаданный человек?",
    "Какие чувства у загаданного человека?",
    "Хотел бы встречи с тобой загаданный человек?",
    "О чем жалеет загаданный человек по отношению к тебе?",
    "Какой/каким загаданный человек видит тебя?",
    "Какие перспективы видит загаданный человек с тобой?",
]


def clear_tarot_person_spread(user_data: Dict) -> None:
    user_data.pop("tarot_person_cards", None)
    user_data.pop("tarot_person_step", None)


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
                "🔮 Открыть Magical Spirit",
                web_app=WebAppInfo(
                    url="https://fortune-cookie-bot-to2u.onrender.com"
                ),
            )
        ],
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
        [InlineKeyboardButton("Расклад на человека", callback_data="tarot_person")],
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


def build_tarot_person_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Начать расклад", callback_data="tarot_person_start")],
        [
            InlineKeyboardButton("Назад", callback_data="tarot_intro"),
            InlineKeyboardButton("Главное меню", callback_data="menu_main"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_person_step_keyboard(is_last_question: bool) -> InlineKeyboardMarkup:
    keyboard_layout = []

    if is_last_question:
        keyboard_layout.append(
            [InlineKeyboardButton("Начать сначала", callback_data="tarot_person_start")]
        )
    else:
        keyboard_layout.append(
            [InlineKeyboardButton("Следующий вопрос", callback_data="tarot_person_next")]
        )

    keyboard_layout.append(
        [
            InlineKeyboardButton("Назад", callback_data="tarot_person_back"),
            InlineKeyboardButton("Главное меню", callback_data="menu_main"),
        ]
    )
    return InlineKeyboardMarkup(keyboard_layout)


def build_tarot_result_keyboard() -> InlineKeyboardMarkup:
    keyboard_layout = [
        [InlineKeyboardButton("Назад", callback_data="tarot_back")],
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
    clear_tarot_person_spread(context.user_data)

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
    clear_tarot_person_spread(user_data)
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
    clear_tarot_person_spread(context.user_data)

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
    clear_tarot_person_spread(context.user_data)

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
    clear_tarot_person_spread(context.user_data)

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


async def send_tarot_person(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_tarot_person_spread(context.user_data)

    caption = (
        "Загадай про себя человека, представь его, назови про себя его имя и "
        "начинай расклад."
    )
    message = update.effective_message

    if TAROT_CARD_BACK_IMAGE_PATH.exists():
        await message.reply_photo(
            photo=TAROT_CARD_BACK_IMAGE_PATH.read_bytes(),
            caption=caption,
            reply_markup=build_tarot_person_keyboard(),
        )
    else:
        await message.reply_text(
            text=caption,
            reply_markup=build_tarot_person_keyboard(),
        )


async def send_tarot_person_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, action: str
) -> None:
    query = update.callback_query
    user_data = context.user_data

    try:
        cards = load_tarot_cards()
    except RuntimeError as exc:
        LOGGER.error("Tarot cards error: %s", exc)
        await query.answer("Карты недоступны. Попробуй позже.", show_alert=True)
        return

    if len(cards) < len(PERSON_SPREAD_QUESTIONS):
        await query.answer("Недостаточно карт для расклада.", show_alert=True)
        return

    if action == "restart" or "tarot_person_cards" not in user_data:
        selected_cards = random.sample(cards, len(PERSON_SPREAD_QUESTIONS))
        user_data["tarot_person_cards"] = [str(card) for card in selected_cards]
        user_data["tarot_person_step"] = 0
    elif action == "next":
        user_data["tarot_person_step"] = user_data.get("tarot_person_step", 0) + 1
    elif action == "back":
        user_data["tarot_person_step"] = user_data.get("tarot_person_step", 0) - 1
    else:
        await query.answer("Неизвестная команда расклада.", show_alert=True)
        return

    step = user_data.get("tarot_person_step", 0)
    card_paths = user_data.get("tarot_person_cards", [])

    if step < 0:
        await send_tarot_person(update, context)
        return

    if step >= len(PERSON_SPREAD_QUESTIONS) or step >= len(card_paths):
        await query.answer("Расклад завершён. Нажми «Начать сначала».", show_alert=True)
        return

    question = PERSON_SPREAD_QUESTIONS[step]
    card_path = Path(card_paths[step])
    answer_text = get_person_spread_answer(step, question, card_path.name)
    is_last_question = step == len(PERSON_SPREAD_QUESTIONS) - 1

    caption = (
        f"Вопрос {step + 1}/{len(PERSON_SPREAD_QUESTIONS)}\n"
        f"🃏 Карта: {card_path.stem}\n\n"
        f"{answer_text}"
    )

    await query.message.reply_photo(
        photo=card_path.read_bytes(),
        caption=caption,
        reply_markup=build_tarot_person_step_keyboard(is_last_question),
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

    if query.data == "tarot_person":
        await send_tarot_person(update, context)
        return

    if query.data == "tarot_person_start":
        await send_tarot_person_step(update, context, action="restart")
        return

    if query.data == "tarot_person_next":
        await send_tarot_person_step(update, context, action="next")
        return

    if query.data == "tarot_person_back":
        await send_tarot_person_step(update, context, action="back")
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
