"""Without-MCP path: OpenAI Responses API with built-in web_search tool."""

from __future__ import annotations

import os
import time

from openai import OpenAI

from prompts import BENCH_SYSTEM


def run(question: str, model: str | None = None, temperature: float = 0.2) -> dict:
    model = model or os.environ.get("OPENAI_MODEL", "gpt-5.1")
    client = OpenAI()
    t_start = time.time()
    resp = client.responses.create(
        model=model,
        temperature=temperature,
        input=[
            {"role": "system", "content": BENCH_SYSTEM},
            {"role": "user", "content": question},
        ],
        tools=[{"type": "web_search"}],
    )
    elapsed = time.time() - t_start

    web_search_count = 0
    answer_parts: list[str] = []
    search_queries: list[str] = []

    for item in resp.output or []:
        item_type = getattr(item, "type", None)
        if item_type == "web_search_call":
            web_search_count += 1
            action = getattr(item, "action", None)
            q = getattr(action, "query", None) if action else None
            if q:
                search_queries.append(q)
        elif item_type == "message":
            for c in getattr(item, "content", []) or []:
                txt = getattr(c, "text", None)
                if txt:
                    answer_parts.append(txt)

    usage = getattr(resp, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0

    return {
        "answer": "\n".join(answer_parts),
        "search_queries": search_queries,
        "tool_call_count": web_search_count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "elapsed_seconds": round(elapsed, 2),
        "model": model,
        "path": "web",
    }
