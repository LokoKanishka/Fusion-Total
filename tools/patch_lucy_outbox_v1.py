#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def ensure_workflow(obj):
    if isinstance(obj, list):
        if not obj:
            raise SystemExit("empty workflow list")
        return obj[0], True
    return obj, False


def patch_js(js: str) -> str:
    if "outbox_contract" in js and "outbox_path" in js:
        return js

    target = "next: `ipc://inbox/${correlationId}.json`"
    replacement = (
        "next: `ipc://inbox/${correlationId}.json`,\n"
        "  outbox_path: `ipc://outbox/${correlationId}.json`,\n"
        "  outbox_contract: 'lucy_output_v1'"
    )
    if target not in js:
        raise SystemExit("cannot patch js: next field not found")
    return js.replace(target, replacement, 1)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: patch_lucy_outbox_v1.py <input.json> <output.json>", file=sys.stderr)
        return 2

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])

    raw = json.loads(src.read_text(encoding="utf-8"))
    wf, wrapped = ensure_workflow(raw)

    nodes = wf.get("nodes") or []

    code_node = None
    for node in nodes:
        if node.get("type") == "n8n-nodes-base.code" and node.get("name") == "Gateway Contract + IPC":
            code_node = node
            break

    if code_node is None:
        raise SystemExit("workflow missing Gateway Contract + IPC code node")

    params = code_node.get("parameters") or {}
    js = params.get("jsCode")
    if not isinstance(js, str):
        raise SystemExit("workflow code node missing jsCode")

    params["jsCode"] = patch_js(js)
    code_node["parameters"] = params

    out_obj = [wf] if wrapped else wf
    dst.write_text(json.dumps(out_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
