import os
import time
from pathlib import Path
from app.reader import _READER_LIBRARY

def save_uploaded_document(filename: str, content: str) -> dict:
    if not filename.lower().endswith(('.txt', '.md')):
        return {"ok": False, "error": "unsupported_format", "message": "Solo se soportan archivos .txt y .md en esta versión."}
    
    runtime_dir = Path(os.environ.get("OPENCLAW_RUNTIME_DIR", "."))
    uploads_dir = runtime_dir / "library" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    
    # Sanitize filename
    safe_name = "".join(c for c in filename if c.isalnum() or c in ("-", "_", ".")).strip()
    if not safe_name:
        safe_name = f"upload_{int(time.time())}.txt"
        
    file_path = uploads_dir / safe_name
    
    try:
        file_path.write_text(content, encoding="utf-8")
        
        # Inject directly into library to avoid needing a full rescan of potentially nested dirs if not supported yet
        book_id = safe_name.rsplit(".", 1)[0]
        
        def _add_to_index(state: dict) -> dict:
            books = state.get("books", {})
            books[book_id] = {
                "id": book_id,
                "book_id": book_id,
                "title": safe_name,  # Use full name with extension to be clear it's a file
                "path": str(file_path),
                "cached_text_path": str(file_path),
                "added_ts": float(time.time()),
            }
            state["books"] = books
            return {"ok": True, "book_id": book_id, "title": safe_name}
            
        res = _READER_LIBRARY._with_state(True, _add_to_index)
        return {"ok": True, "book_id": res["book_id"], "title": res["title"]}
        
    except Exception as e:
        return {"ok": False, "error": "upload_failed", "message": str(e)}
