"""Claude API 호출 공통 설정.

키는 .env의 ANTHROPIC_API_KEY에서 읽는다 (없으면 SDK가 다른 인증 수단을 찾고, 그것도 없으면 MissingAPIKey).
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
TIMEOUT_SECONDS = 30.0  # SDK 기본값은 10분이라 멈춘 호출이 서버 작업 스레드를 오래 붙잡는다. 평가에서 질문 하나, 고시 하나가 30초를 넘긴 적이 없다

T = TypeVar("T", bound=BaseModel)


class LLMRefused(RuntimeError):
    pass


class MissingAPIKey(RuntimeError):
    """키도 다른 인증 수단도 찾지 못했다."""


def get_client() -> anthropic.Anthropic:
    load_dotenv(ROOT / ".env")
    client = anthropic.Anthropic(timeout=TIMEOUT_SECONDS)
    if client.api_key is None and client.auth_token is None and getattr(client, "credentials", None) is None:
        raise MissingAPIKey("ANTHROPIC_API_KEY가 없다")
    return client


def parse(client, schema: type[T], system: str, user: str | list, effort: str | None = "high", model: str = MODEL) -> T:
    """구조화 출력으로 한 번 부르고, 스키마로 검증된 결과를 돌려준다.

    system은 매번 같은 글이라 캐시해 둔다. user에 대화 목록(list)을 주면 그대로 messages로 쓴다.
    """
    messages = user if isinstance(user, list) else [{"role": "user", "content": user}]
    # 대체 모델과 노력 설정은 기본 모델에만 쓴다. 입력 가드의 작은 모델은 한 번만 부른다
    extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if model == MODEL else {"betas": []}
    if effort:
        extra["output_config"] = {"effort": effort}
    response = client.beta.messages.parse(
        model=model,
        max_tokens=16000,
        **extra,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_format=schema,
    )
    if response.stop_reason == "refusal":
        raise LLMRefused(str(getattr(response, "stop_details", "")))
    if response.parsed_output is None:
        raise ValueError(f"구조화 출력을 읽지 못했다 (stop_reason={response.stop_reason})")
    return response.parsed_output
