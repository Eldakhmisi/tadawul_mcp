"""With-MCP path: OpenAI chat completions + tadawul_mcp tools over stdio."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

from prompts import BENCH_SYSTEM

REPO = Path(__file__).resolve().parent.parent
VENV_PY = REPO / ".venv" / "bin" / "python"
SERVER = REPO / "tadawul_mcp_server.py"


def mcp_tool_to_openai(tool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
        },
    }


async def _run(question: str, model: str, temperature: float) -> dict:
    server_params = StdioServerParameters(
        command=str(VENV_PY) if VENV_PY.exists() else "python",
        args=[str(SERVER)],
        cwd=str(REPO),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_resp = await session.list_tools()
            openai_tools = [mcp_tool_to_openai(t) for t in tools_resp.tools]

            client = OpenAI()
            messages = [
                {"role": "system", "content": BENCH_SYSTEM},
                {"role": "user", "content": question},
            ]

            tool_call_log: list[dict] = []
            input_tokens = 0
            output_tokens = 0
            t_start = time.time()
            final_msg_content = ""
            max_iters = 12

            for _ in range(max_iters):
                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=openai_tools,
                    temperature=temperature,
                )
                if resp.usage:
                    input_tokens += resp.usage.prompt_tokens
                    output_tokens += resp.usage.completion_tokens

                msg = resp.choices[0].message
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [tc.model_dump() for tc in (msg.tool_calls or [])] or None,
                    }
                )

                if not msg.tool_calls:
                    final_msg_content = msg.content or ""
                    break

                for tc in msg.tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    tool_call_log.append({"name": name, "args": args})
                    try:
                        result = await session.call_tool(name, args)
                        text_blocks = [
                            c.text for c in result.content if getattr(c, "type", None) == "text"
                        ]
                        content = "\n".join(text_blocks) or json.dumps(
                            [c.model_dump() for c in result.content]
                        )
                    except Exception as e:  # noqa: BLE001
                        content = f"ERROR calling tool {name}: {e}"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": content,
                        }
                    )

            elapsed = time.time() - t_start

            return {
                "answer": final_msg_content,
                "tool_calls": tool_call_log,
                "tool_call_count": len(tool_call_log),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "elapsed_seconds": round(elapsed, 2),
                "model": model,
                "path": "mcp",
            }


def run(question: str, model: str | None = None, temperature: float = 0.2) -> dict:
    model = model or os.environ.get("OPENAI_MODEL", "gpt-5.1")
    return asyncio.run(_run(question, model, temperature))
