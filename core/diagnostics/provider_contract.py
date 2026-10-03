"""Check deterministic semantic parity across MO's provider wire adapters."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.provider.provider import ChatCompletionsProvider, CodexOAuthProvider


@dataclass(frozen=True)
class ProviderContractProblem:
    adapter: str
    reason: str
    detail: str = ""

    def render(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"{self.adapter}: {self.reason}{suffix}"


def _expect(
    problems: list[ProviderContractProblem],
    adapter: str,
    condition: bool,
    reason: str,
    detail: str = "",
) -> None:
    if not condition:
        problems.append(ProviderContractProblem(adapter, reason, detail))


def check_provider_contract(_root: Path | None = None) -> list[ProviderContractProblem]:
    """Return provider conversion problems without credentials or live calls."""
    problems: list[ProviderContractProblem] = []
    tool = {
        "type": "function",
        "function": {
            "name": "read_fact",
            "description": "Read one current fact.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }
    messages = [
        {"role": "system", "content": "shared policy"},
        {"role": "system", "content": "current surface"},
        {"role": "user", "content": "Read the fact."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_fact", "arguments": '{"name":"status"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"status":"ok"}'},
        {"role": "assistant", "content": "The status is verified."},
    ]

    chat_messages = ChatCompletionsProvider._normalize_messages(
        messages,
        accepts_image_input=True,
    )
    _expect(problems, "chat_completions", chat_messages == messages, "text/tool message semantics changed")
    _expect(
        problems,
        "chat_completions",
        (chat_messages[3].get("tool_calls") or [{}])[0].get("id") == "call-1"
        and chat_messages[4].get("tool_call_id") == "call-1",
        "tool call/result linkage was not preserved",
    )

    instructions, response_input = CodexOAuthProvider._to_instructions_and_input(messages)
    _expect(
        problems,
        "codex_responses",
        instructions == "shared policy\n\ncurrent surface",
        "system instruction order changed",
        instructions,
    )
    response_types = [item.get("type") or item.get("role") for item in response_input]
    _expect(
        problems,
        "codex_responses",
        response_types == ["user", "function_call", "function_call_output", "assistant"],
        "conversation/tool event order changed",
        repr(response_types),
    )
    calls = [item for item in response_input if item.get("type") == "function_call"]
    outputs = [item for item in response_input if item.get("type") == "function_call_output"]
    _expect(
        problems,
        "codex_responses",
        calls == [{
            "type": "function_call",
            "call_id": "call-1",
            "name": "read_fact",
            "arguments": '{"name":"status"}',
        }],
        "native function call was not preserved",
        repr(calls),
    )
    _expect(
        problems,
        "codex_responses",
        outputs == [{
            "type": "function_call_output",
            "call_id": "call-1",
            "output": '{"status":"ok"}',
        }],
        "native function output linkage was not preserved",
        repr(outputs),
    )

    responses_tools = CodexOAuthProvider._to_responses_tools([tool])
    expected_tool = {
        "type": "function",
        "name": "read_fact",
        "description": "Read one current fact.",
        "parameters": tool["function"]["parameters"],
        "strict": True,
    }
    _expect(
        problems,
        "codex_responses",
        responses_tools == [expected_tool],
        "tool schema changed across adapters",
        repr(responses_tools),
    )

    image_messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-image",
                    "type": "function",
                    "function": {
                        "name": "computer_observe",
                        "arguments": '{"kind":"screen","operation":"capture"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-image",
            "content": [
                {"type": "text", "text": "screen"},
                {"type": "image", "image_url": "data:image/png;base64,AA=="},
            ],
        },
    ]
    _image_instructions, image_input = CodexOAuthProvider._to_instructions_and_input(image_messages)
    image_output = next(
        (item for item in image_input if item.get("type") == "function_call_output"),
        {},
    )
    _expect(
        problems,
        "codex_responses",
        image_output.get("call_id") == "call-image"
        and image_output.get("output") == [
            {"type": "input_text", "text": "screen"},
            {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
        ],
        "multimodal tool output lost content or call linkage",
        repr(image_output),
    )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    problems = check_provider_contract(args.root)
    if problems:
        print(f"[providers] {len(problems)} semantic contract problem(s)")
        for problem in problems:
            print(problem.render())
        return 1
    print("[providers] chat and Responses message/tool semantics conform")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
