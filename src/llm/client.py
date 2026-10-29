"""Claude API 호출 공통 설정.

키는 .env의 ANTHROPIC_API_KEY에서 읽는다 (없으면 SDK가 다른 인증 수단을 찾는다).
안전 분류기가 요청을 거절하면 서버가 다른 모델로 다시 돌리도록 fallbacks를 켠다.
"""
from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

T = TypeVar("T", bound=BaseModel)


class LLMRefused(RuntimeError):
    pass


def get_client() -> anthropic.Anthropic:
    load_dotenv(ROOT / ".env")
    return anthropic.Anthropic()


def parse(client, schema: type[T], system: str, user: str, effort: str = "high") -> T:
    """구조화 출력으로 한 번 부르고, 스키마로 검증된 결과를 돌려준다."""
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": effort},
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
    )
    if response.stop_reason == "refusal":
        raise LLMRefused(str(getattr(response, "stop_details", "")))
    if response.parsed_output is None:
        raise ValueError(f"구조화 출력을 읽지 못했다 (stop_reason={response.stop_reason})")
    return response.parsed_output
