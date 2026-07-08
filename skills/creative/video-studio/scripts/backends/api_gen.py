#!/usr/bin/env python3
"""video-studio: API generation backends (HeyGen / Synthesia / Creatomate).

These produce a clip from a service. They are the "in-product / personalized
video" path from the research (Pyne AI x HeyGen, Synthesia fromTemplate+webhook).

Safety: every real send first runs the govern.py redaction gate on the text
inputs. Without an API key (or with --dry-run) it prints the exact request
payload instead of calling out — so it is safe to inspect with no network.

Backends:
  heygen     --script TXT --avatar ID [--voice ID]
  synthesia  --template-id ID --var k=v [--var ...] [--webhook URL]
  creatomate --template-id ID --mod k=v [--mod ...]

Env keys (real mode): HEYGEN_API_KEY / SYNTHESIA_API_KEY / CREATOMATE_API_KEY
Dependencies: python3 stdlib only (urllib).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_GOVERN = _HERE.parent / "govern.py"

ENDPOINTS = {
    "heygen": "https://api.heygen.com/v2/video/generate",
    "synthesia": "https://api.synthesia.io/v2/videos/fromTemplate",
    "creatomate": "https://api.creatomate.com/v1/renders",
}
ENV_KEYS = {
    "heygen": "HEYGEN_API_KEY",
    "synthesia": "SYNTHESIA_API_KEY",
    "creatomate": "CREATOMATE_API_KEY",
}


def govern_check(text: str) -> None:
    """Run the redaction gate; abort the send if it flags anything."""
    if not text:
        return
    proc = subprocess.run([sys.executable, str(_GOVERN), "check", text],
                          capture_output=True, text=True)
    if proc.returncode == 2:
        sys.stderr.write(proc.stderr)
        print("[api_gen] aborted: redaction gate flagged the input "
              "(remove PII/PHI/secrets before sending externally).",
              file=sys.stderr)
        sys.exit(2)


def kvlist(pairs: list[str]) -> dict:
    out = {}
    for p in pairs or []:
        if "=" not in p:
            print(f"[api_gen] ignoring malformed pair: {p}", file=sys.stderr)
            continue
        k, v = p.split("=", 1)
        out[k] = v
    return out


def build_payload(backend: str, a) -> dict:
    if backend == "heygen":
        return {
            "video_inputs": [{
                "character": {"type": "avatar", "avatar_id": a.avatar},
                "voice": {"type": "text", "input_text": a.script,
                          "voice_id": a.voice} if a.voice else
                         {"type": "text", "input_text": a.script},
            }],
            "dimension": {"width": 1280, "height": 720},
        }
    if backend == "synthesia":
        payload = {"templateId": a.template_id, "templateData": kvlist(a.var),
                   "test": True}
        if a.webhook:
            payload["callbackId"] = a.webhook
        return payload
    if backend == "creatomate":
        return {"template_id": a.template_id, "modifications": kvlist(a.mod)}
    raise ValueError(backend)


def collect_text(backend: str, a) -> str:
    if backend == "heygen":
        return a.script or ""
    if backend == "synthesia":
        return " ".join(kvlist(a.var).values())
    if backend == "creatomate":
        return " ".join(kvlist(a.mod).values())
    return ""


def send(backend: str, payload: dict, key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if backend == "heygen":
        headers["X-Api-Key"] = key
    else:  # synthesia, creatomate use Bearer
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(ENDPOINTS[backend],
                                 data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def run(backend: str, a):
    text = collect_text(backend, a)
    payload = build_payload(backend, a)
    key = os.environ.get(ENV_KEYS[backend])

    if a.dry_run or not key:
        why = "--dry-run" if a.dry_run else f"no {ENV_KEYS[backend]} set"
        print(f"# DRY RUN ({why}) — would POST to {ENDPOINTS[backend]}")
        print(f"# header: {'X-Api-Key' if backend=='heygen' else 'Authorization: Bearer'} <key>")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print("# NOTE: govern.py redaction gate runs before any *real* send.",
              file=sys.stderr)
        return

    govern_check(text)
    try:
        resp = send(backend, payload, key)
    except Exception as e:  # noqa: BLE001
        print(f"[api_gen] request failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(resp, indent=2, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="api_gen", description="API video generation backends")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the request payload, do not call the network")
    sub = ap.add_subparsers(dest="backend", required=True)

    h = sub.add_parser("heygen")
    h.add_argument("--script", required=True)
    h.add_argument("--avatar", required=True)
    h.add_argument("--voice", default=None)

    s = sub.add_parser("synthesia")
    s.add_argument("--template-id", required=True)
    s.add_argument("--var", action="append", default=[], help="k=v (repeatable)")
    s.add_argument("--webhook", default=None)

    c = sub.add_parser("creatomate")
    c.add_argument("--template-id", required=True)
    c.add_argument("--mod", action="append", default=[], help="k=v (repeatable)")

    a = ap.parse_args(argv)
    run(a.backend, a)


if __name__ == "__main__":
    main()
