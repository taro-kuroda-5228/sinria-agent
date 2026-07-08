#!/usr/bin/env python3
"""Build a chat-format SFT dataset from captured verified trajectories (P2).

Converts ``SINRIA_HOME/training/trajectories/**.json`` snapshots (see
``agent/training_capture.py``) into SFT JSONL where assistant tool calls use
the runtime's text-mode ` ```tool_call ` contract (``agent/text_tool_calls.py``)
and tool results become explicit ``[tool result]`` user turns — training the
local model on exactly the contract Sinria's architecture guarantees.

Fail-closed: examples whose text still trips ``contains_sensitive_text`` are
refused (capture already rejects them; this is defense in depth). Local-only.

Usage:
    SINRIA_HOME=~/.sinria python scripts/build_sft_dataset.py \
        --out ~/.sinria/training/sft/dataset.jsonl [--max-examples N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.context_share.safety import contains_sensitive_text  # noqa: E402


def _tool_call_block(call: dict[str, Any]) -> str:
    try:
        arguments = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        arguments = {}
    body = json.dumps({"name": call.get("name", ""), "arguments": arguments}, ensure_ascii=False)
    return f"```tool_call\n{body}\n```"


def trajectory_to_example(trajectory: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Convert one trajectory snapshot into a chat-format SFT example."""
    messages: list[dict[str, str]] = []
    for turn in trajectory.get("turns", []):
        role = turn.get("role")
        content = str(turn.get("content") or "")
        if role == "assistant" and turn.get("tool_calls"):
            blocks = [_tool_call_block(tc) for tc in turn["tool_calls"]]
            text = "\n".join(part for part in [content.strip(), *blocks] if part)
            messages.append({"role": "assistant", "content": text})
        elif role == "tool":
            name = turn.get("name", "tool")
            messages.append({"role": "user", "content": f"[tool result: {name}]\n{content}"})
        elif role in ("user", "assistant"):
            messages.append({"role": role, "content": content})
    messages = [m for m in messages if m["content"].strip()]
    if len(messages) < 2 or messages[-1]["role"] != "assistant":
        return None
    if any(contains_sensitive_text(m["content"]) for m in messages):
        return None
    return {"messages": messages}


def build_examples(trajectories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert + dedup by content digest, preserving order."""
    seen: set[str] = set()
    examples: list[dict[str, Any]] = []
    for trajectory in trajectories:
        example = trajectory_to_example(trajectory)
        if example is None:
            continue
        digest = hashlib.sha1(
            json.dumps(example, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        examples.append(example)
    return examples


def run(*, trajectories_dir: Path, out_path: Path, max_examples: int = 0) -> int:
    trajectories: list[dict[str, Any]] = []
    for path in sorted(trajectories_dir.rglob("*.json")):
        if path.name == "index.jsonl":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("turns"):
            trajectories.append(data)
    examples = build_examples(trajectories)
    if max_examples:
        examples = examples[:max_examples]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example, ensure_ascii=False) + "\n")
    print(
        f"trajectories={len(trajectories)} examples={len(examples)} -> {out_path}",
        file=sys.stderr,
    )
    return len(examples)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--trajectories-dir", type=Path, default=None)
    parser.add_argument("--max-examples", type=int, default=0)
    args = parser.parse_args()

    trajectories_dir = args.trajectories_dir
    if trajectories_dir is None:
        from hermes_constants import get_sinria_home

        trajectories_dir = get_sinria_home() / "training" / "trajectories"
    run(trajectories_dir=trajectories_dir, out_path=args.out, max_examples=args.max_examples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
