import os
import json
import time
from pathlib import Path

def _get_notes_dir() -> Path:
    runtime_dir = Path(os.environ.get("OPENCLAW_RUNTIME_DIR", "."))
    # Tramo 2 rule: notes saved in notes/<document_id>/<page_number>.json
    d = runtime_dir / "library" / "notes"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_notes(document_id: str, page_number: int) -> list:
    """Gets notes mapped to a given document and page number."""
    safe_doc = "".join(c for c in document_id if c.isalnum() or c in ("-", "_")).strip() or "default"
    p = _get_notes_dir() / safe_doc / f"page_{page_number}.json"
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def add_note(document_id: str, page_number: int, text: str, role: str = "user") -> dict:
    """Adds a new persistent note to the specified document and page."""
    safe_doc = "".join(c for c in document_id if c.isalnum() or c in ("-", "_")).strip() or "default"
    d = _get_notes_dir() / safe_doc
    d.mkdir(parents=True, exist_ok=True)

    p = d / f"page_{page_number}.json"
    notes = get_notes(document_id, page_number)

    new_note = {
        "text": text,
        "role": role,
        "timestamp": time.time()
    }
    notes.append(new_note)

    with open(p, "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=2, ensure_ascii=False)

    return new_note
