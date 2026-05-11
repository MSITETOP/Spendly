"""Receipt parsing via OpenAI vision."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from openai import OpenAI

SYSTEM = """Ты извлекаешь данные с фото или скана чека покупки.
Верни строго один JSON-объект без markdown по схеме ниже. Числа — десятичные, разделитель точка.
Если дата/время неясны, оцени по чеку; если совсем нет — используй null для purchased_at.

Для КАЖДОЙ товарной позиции обязательно укажи поле category: выбери одну категорию из списка,
который будет передан в пользовательском сообщении. Если ни одна не подходит — используй
последнюю категорию из списка (обычно «Другое») или буквально «Другое», если она есть в списке.

Схема:
{
  "store_name": string,
  "purchased_at": string | null (ISO 8601, например 2024-05-11T14:32:00),
  "currency": string, код валюты (RUB, USD, EUR),
  "total_amount": number,
  "lines": [
    {
      "product_name": string,
      "quantity": number,
      "unit_price": number | null,
      "line_total": number,
      "category": string
    }
  ]
}
"""


def _client() -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Не задан OPENAI_API_KEY в окружении или .env")
    return OpenAI(api_key=key)


def parse_receipt_image(
    image_bytes: bytes,
    mime_type: str,
    model: str = "gpt-4o-mini",
    category_names: list[str] | None = None,
) -> dict[str, Any]:
    """Parse receipt from image bytes (JPEG/PNG/WebP).

    category_names: допустимые категории расходов; для каждой строки чека модель вернёт поле
    ``category`` с одним из этих названий (или ближайшим смысловым вариантом из списка).
    """
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"

    if category_names:
        cats_block = (
            "Допустимые категории для поля category у каждой позиции (ровно как написано):\n"
            + "\n".join(f"- {n}" for n in category_names)
        )
    else:
        cats_block = (
            "Категории: для каждой позиции укажи поле category — краткое русское название "
            "типа расхода (продукты, бытовая химия, аптека и т.д.)."
        )

    user_text = (
        "Извлеки структуру чека из изображения. Только валидный JSON.\n\n" + cats_block
    )

    client = _client()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_text,
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    text = resp.choices[0].message.content or "{}"
    return json.loads(text)
