#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

echo "== verify reader mode v0 ==" >&2
echo "tmp_dir=${TMP_DIR}" >&2

python3 - "${TMP_DIR}" <<'PY'
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


tmp_dir = Path(sys.argv[1])
repo_root = Path.cwd()
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "scripts"))

import app.reader
import openclaw_direct_chat as direct_chat  # noqa: E402

os.environ["DIRECT_CHAT_TTS_DRY_RUN"] = "1"
os.environ["OPENCLAW_RUNTIME_DIR"] = str(tmp_dir)

library_dir = tmp_dir / "library"
library_dir.mkdir(parents=True, exist_ok=True)
book_path = library_dir / "verify_book.txt"
book_path.write_text(
    "Primer bloque del libro de prueba.\n\n"
    "Segundo bloque para probar barge-in.\n\n"
    "Tercer bloque final.",
    encoding="utf-8",
)

state_path = tmp_dir / "reading_sessions.json"
lock_path = tmp_dir / ".reading_sessions.lock"
index_path = tmp_dir / "reader_library_index.json"
index_lock = tmp_dir / ".reader_library_index.lock"
cache_dir = tmp_dir / "reader_cache"

# IMPORTANTE: Monkeypatch las instancias de app.reader que usa el router
app.reader._READER_STORE = app.reader.ReaderSessionStore(state_path=state_path, lock_path=lock_path)
app.reader._READER_LIBRARY = app.reader.ReaderLibraryIndex(
    library_dir=library_dir,
    index_path=index_path,
    lock_path=index_lock,
    cache_dir=cache_dir,
)

httpd = direct_chat.ThreadingHTTPServer(("127.0.0.1", 0), direct_chat.Handler)
httpd.gateway_token = "verify-token"
th = threading.Thread(target=httpd.serve_forever, daemon=True)
th.start()
time.sleep(0.1)
base = f"http://127.0.0.1:{httpd.server_address[1]}"


def request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    headers = {}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(base + path, method=method, data=data, headers=headers)
    try:
        with urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw or "{}")
            if not isinstance(parsed, dict):
                parsed = {}
            return int(resp.getcode()), parsed
    except HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        return int(e.code), parsed


def ensure(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


try:
    # Aseguramos que el libro sea indexado por el store modular
    request("POST", "/api/reader/rescan", {})

    code, started = request("POST", "/api/reader/session/start", {"session_id": "verify_rm", "book_id": "verify_book", "reset": True})
    ensure(code == 200 and bool(started.get("ok")) and bool(started.get("started")), f"start_failed code={code} body={started}")
    print("PASS start_session")

    code, first = request("GET", "/api/reader/session/next?session_id=verify_rm")
    chunk1 = first.get("chunk", {})
    chunk1_id = str(chunk1.get("chunk_id", ""))
    ensure(code == 200 and int(chunk1.get("chunk_index", -1)) == 0, f"next_1_failed code={code} body={first}")
    print("PASS next_chunk_1")

    code, commit1 = request("POST", "/api/reader/session/commit", {"session_id": "verify_rm", "chunk_id": chunk1_id})
    ensure(code == 200 and bool(commit1.get("committed")), f"commit_1_failed code={code} body={commit1}")
    print("PASS commit_chunk_1")

    code, second = request("GET", "/api/reader/session/next?session_id=verify_rm")
    chunk2 = second.get("chunk", {})
    chunk2_id = str(chunk2.get("chunk_id", ""))
    ensure(code == 200 and int(chunk2.get("chunk_index", -1)) == 1, f"next_2_failed code={code} body={second}")
    print("PASS next_chunk_2")

    code, barge = request("POST", "/api/reader/session/barge_in", {"session_id": "verify_rm"})
    ensure(code == 200 and bool(barge.get("ok")), f"barge_failed code={code} body={barge}")
    ensure(barge.get("reader_state") == "commenting", f"barge_bad_state body={barge}")
    print("PASS barge_in_pending")

    code, replay = request("GET", "/api/reader/session/next?session_id=verify_rm")
    ensure(code == 200 and int(replay.get("chunk", {}).get("chunk_index", -1)) == 1, f"restart_replay_failed body={replay}")
    print("PASS restart_replays_pending_chunk")

    code, commit2 = request("POST", "/api/reader/session/commit", {"session_id": "verify_rm", "chunk_id": chunk2_id})
    ensure(code == 200 and bool(commit2.get("committed")), f"commit_2_failed code={code} body={commit2}")
    ensure(int(commit2.get("cursor", -1)) == 2, f"commit_2_bad_cursor body={commit2}")
    print("PASS commit_chunk_2_after_replay")

    code, third = request("GET", "/api/reader/session/next?session_id=verify_rm")
    chunk3 = third.get("chunk", {})
    chunk3_id = str(chunk3.get("chunk_id", ""))
    ensure(code == 200 and int(chunk3.get("chunk_index", -1)) == 2, f"next_3_failed code={code} body={third}")
    print("PASS next_chunk_3")

    code, commit3 = request("POST", "/api/reader/session/commit", {"session_id": "verify_rm", "chunk_id": chunk3_id})
    ensure(code == 200 and bool(commit3.get("committed")), f"commit_3_failed code={code} body={commit3}")
    ensure(bool(commit3.get("done")), f"commit_3_not_done body={commit3}")
    print("PASS commit_chunk_3")

    code, eof = request("GET", "/api/reader/session/next?session_id=verify_rm")
    ensure(code == 200 and bool(eof.get("ok")), f"next_eof_failed code={code} body={eof}")
    ensure(eof.get("chunk") is None, f"next_eof_expected_null_chunk body={eof}")
    ensure(bool(eof.get("done")), f"next_eof_expected_done body={eof}")
    print("PASS eof")

    code, final = request("GET", "/api/reader/session?session_id=verify_rm")
    ensure(code == 200 and bool(final.get("done")), f"final_status_not_done body={final}")
    print("PASS persisted_status")

    print("READER_MODE_OK")
finally:
    try:
        httpd.shutdown()
        th.join(timeout=1.0)
    except Exception:
        pass
PY
