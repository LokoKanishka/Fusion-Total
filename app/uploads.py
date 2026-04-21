import os
import time
import base64
import subprocess
from pathlib import Path
from app.reader import _READER_LIBRARY

def _extract_pdf_text(path: Path) -> tuple[str, str]:
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        text = "\n\n".join(p for p in pages if p)
        if text.strip():
            return text, "pypdf"
    except Exception:
        pass

    try:
        from pdfplumber import open as pdf_open  # type: ignore
        with pdf_open(str(path)) as pdf:
            pages = [(page.extract_text() or "").strip() for page in pdf.pages]
        text = "\n\n".join(p for p in pages if p)
        if text.strip():
            return text, "pdfplumber"
    except Exception:
        pass

    try:
        proc = subprocess.run(
            ["pdftotext", str(path), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip(), "pdftotext"
    except Exception:
        pass

    return "", "unavailable"

def save_uploaded_document(filename: str, content: str = "", content_base64: str = "") -> dict:
    lower = filename.lower()
    if not lower.endswith(('.txt', '.md', '.pdf')):
        return {"ok": False, "error": "unsupported_format", "message": "Solo se soportan archivos .txt, .md y .pdf en esta versión."}
    
    runtime_dir = Path(os.environ.get("OPENCLAW_RUNTIME_DIR", "."))
    uploads_dir = runtime_dir / "library" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    
    # Sanitize filename
    safe_name = "".join(c for c in filename if c.isalnum() or c in ("-", "_", ".")).strip()
    if not safe_name:
        safe_name = f"upload_{int(time.time())}.txt"
        
    file_path = uploads_dir / safe_name
    
    try:
        fmt = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else "txt"
        extracted_text = content
        extractor = "raw_text"

        if fmt == "pdf":
            payload = str(content_base64 or content or "")
            if "," in payload and payload.lower().startswith("data:"):
                payload = payload.split(",", 1)[1]
            try:
                raw = base64.b64decode(payload, validate=False)
            except Exception as e:
                return {"ok": False, "error": "invalid_pdf_payload", "message": f"No pude decodificar el PDF: {e}"}
            file_path.write_bytes(raw)
            extracted_text, extractor = _extract_pdf_text(file_path)
            if not extracted_text.strip():
                return {
                    "ok": False,
                    "error": "pdf_text_extraction_failed",
                    "message": "No pude extraer texto del PDF. Si es escaneado, hace falta OCR antes de leerlo.",
                }
        else:
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
                "format": fmt,
                "extractor": extractor,
                "added_ts": float(time.time()),
            }
            if fmt == "pdf":
                text_cache = _READER_LIBRARY.cache_dir / f"{book_id}.txt"
                text_cache.write_text(extracted_text, encoding="utf-8")
                books[book_id]["cached_text_path"] = str(text_cache)
            state["books"] = books
            return {"ok": True, "book_id": book_id, "title": safe_name}
            
        res = _READER_LIBRARY._with_state(True, _add_to_index)
        return {"ok": True, "book_id": res["book_id"], "title": res["title"]}
        
    except Exception as e:
        return {"ok": False, "error": "upload_failed", "message": str(e)}
