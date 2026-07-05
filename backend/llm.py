"""
llm.py — Anthropic API 呼び出しの薄いヘルパー
=============================================
AI概況 (ai.py) と カレンダーAI注釈 (macro.py) の共通口。
キーはバックエンドの .env のみに置き、フロントには絶対に渡さない (SPEC §9)。
新規依存は追加せず、httpx で REST を直接叩く。
"""

from __future__ import annotations

import os

import httpx

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"


def enabled() -> bool:
    return bool(api_key())


async def complete(http: httpx.AsyncClient, prompt: str, max_tokens: int = 1000, timeout: float = 20.0) -> str:
    """1メッセージのテキスト補完。キー未設定や失敗は例外を送出 (呼び出し側でフォールバック)。"""
    key = api_key()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 未設定")
    r = await http.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": model(),
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    return "".join(
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    ).strip()
