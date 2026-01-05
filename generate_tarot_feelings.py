"""
Генератор текстов для всех комбинаций двух карт таро.

Скрипт проходит по изображениям в каталоге tarot, использует краткие
значения карт и создаёт ответы на вопрос «что чувствует загаданный
человек к вам» для каждой пары. Результат сохраняется в
`tarot/feelings_combinations.json`.
"""

import json
from itertools import combinations
from pathlib import Path
from typing import Dict, List

from tarot_data import get_tarot_short_prediction

TAROT_DIR = Path("tarot")
OUTPUT_FILE = TAROT_DIR / "feelings_combinations.json"
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def load_tarot_cards() -> List[Path]:
    cards = [
        path
        for path in TAROT_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    return sorted(cards, key=lambda p: p.stem)


def build_feelings_answer(first: str, second: str) -> str:
    first_short = get_tarot_short_prediction(first)
    second_short = get_tarot_short_prediction(second)

    return (
        f"{first} + {second}: в чувствах к вам соединяются энергии обоих арканов. "
        f"{first_short} Это окрашивает эмоции в тональность {first}. "
        f"Вторая карта дополняет картину: {second_short} "
        "Вместе это даёт ощущение, что к вам человек испытывает смесь этих состояний — "
        "они одновременно притягивают, тревожат или вдохновляют, и именно так "
        "проявляются его эмоции."
    )


def generate_combinations(cards: List[Path]) -> List[Dict[str, object]]:
    pairs = []
    for first, second in combinations(cards, 2):
        first_name, second_name = first.stem, second.stem
        pairs.append(
            {
                "cards": [first_name, second_name],
                "answer": build_feelings_answer(first_name, second_name),
            }
        )
    return pairs


def main() -> None:
    cards = load_tarot_cards()
    if not cards:
        raise SystemExit("В каталоге tarot не найдены изображения карт.")

    combinations_payload = generate_combinations(cards)
    OUTPUT_FILE.write_text(
        json.dumps(combinations_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Сохранено {len(combinations_payload)} комбинаций в {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
