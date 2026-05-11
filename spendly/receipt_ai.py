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
      "category_hint": string | null
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
) -> dict[str, Any]:
    """Parse receipt from image bytes (JPEG/PNG/WebP)."""
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"

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
                        "text": "Извлеки структуру чека из изображения. Только валидный JSON.",
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
