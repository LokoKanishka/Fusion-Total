#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - <<'PY'
from pathlib import Path
import subprocess
import json

src_path = Path("scripts/molbot_direct_chat/reader_ui_html.py")
src = src_path.read_text(encoding="utf-8")
needle = "function parseSlash(text)"
start = src.find(needle)
if start < 0:
    required = [
        "/api/reader/books",
        "/api/reader/session/start",
        "/api/reader/session?include_chunks=1",
        "/api/reader/session/next",
        "/api/reader/session/barge_in",
    ]
    missing = [item for item in required if item not in src]
    if missing:
        raise SystemExit(f"FAIL: reader UI missing controls/endpoints: {missing}")
    print("SLASH_READER_UI_OK dedicated_reader_ui=1")
    raise SystemExit(0)
brace_open = src.find("{", start)
if brace_open < 0:
    raise SystemExit("FAIL: parseSlash() malformed (missing '{')")

depth = 0
end = -1
for i in range(brace_open, len(src)):
    ch = src[i]
    if ch == "{":
        depth += 1
    elif ch == "}":
        depth -= 1
        if depth == 0:
            end = i
            break

if end < 0:
    raise SystemExit("FAIL: parseSlash() malformed (unbalanced braces)")

fn = src[start:end + 1]

tests = [
    ("/new", {"kind":"new"}),
    ("/escritorio", {"kind":"message", "text":"decime que carpetas y archivos hay en mi escritorio"}),
    ("/lib", {"kind":"message", "text":"biblioteca"}),
    ("/rescan", {"kind":"message", "text":"biblioteca rescan"}),
    ("/read 3", {"kind":"message", "text":"leer libro 3"}),
    ("/next", {"kind":"message", "text":"seguí"}),
    ("/repeat", {"kind":"message", "text":"repetir"}),
    ("/status", {"kind":"message", "text":"estado lectura"}),
    ("/help reader", {"kind":"message", "text":"ayuda lectura"}),
]

js = [
    fn,
    "const tests = " + json.dumps(tests, ensure_ascii=False) + ";",
    """
function same(a,b){
  if(!a||!b) return false;
  const ka = Object.keys(a).sort();
  const kb = Object.keys(b).sort();
  if (ka.join(",") !== kb.join(",")) return false;
  for (const k of ka) if (String(a[k]) !== String(b[k])) return false;
  return true;
}
let ok = 0;
for (const [input, expected] of tests){
  const got = parseSlash(input);
  if(!same(got, expected)){
    console.error("FAIL:", input);
    console.error(" expected:", expected);
    console.error(" got     :", got);
    process.exit(1);
  }
  ok++;
}
console.log("SLASH_READER_UI_OK tests=" + ok);
""",
]
payload = "\n".join(js)

r = subprocess.run(["node", "-e", payload], capture_output=True, text=True)
if r.returncode != 0:
    print(r.stdout)
    print(r.stderr)
    raise SystemExit(r.returncode)
print(r.stdout.strip())
PY
