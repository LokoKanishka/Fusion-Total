from __future__ import annotations

import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ConversionResult:
    ok: bool
    output_path: str = ""
    pages: int = 0
    warnings: list[str] = field(default_factory=list)
    engine: str = "pdftotext"
    text_blocks: int = 0
    notes_detected: int = 0
    error: str = ""


@dataclass(frozen=True)
class ParagraphBlock:
    style: str
    text: str


def safe_output_name(input_name: str) -> str:
    original = Path(str(input_name or "documento.pdf")).name
    stem = Path(original).stem or "documento"
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "documento"
    clean = clean[:120]
    return f"{clean}_convertido.docx"


def find_downloads_dir() -> Path:
    home = Path.home()
    preferred = home / "Descargas"
    if preferred.exists():
        return preferred
    alternate = home / "Downloads"
    if alternate.exists():
        return alternate
    return preferred


def is_pdf_textual(path: str | Path) -> bool:
    pdf_path = Path(path)
    for page_text in _extract_pages_text(pdf_path):
        if _has_meaningful_text(page_text):
            return True
    return False


def convert_pdf_to_docx(input_pdf: str | Path, output_docx: str | Path) -> ConversionResult:
    pdf_path = Path(input_pdf)
    output_path = Path(output_docx)
    warnings: list[str] = []
    pages = _page_count(pdf_path)
    page_texts = _extract_pages_text(pdf_path)
    meaningful_pages = [text for text in page_texts if _has_meaningful_text(text)]
    if not meaningful_pages:
        return ConversionResult(
            False,
            output_path=str(output_path),
            pages=pages,
            warnings=[],
            engine="pdftotext",
            text_blocks=0,
            notes_detected=0,
            error="Este PDF parece escaneado; OCR todavía no está habilitado en esta herramienta.",
        )
    blocks, notes_detected = build_docx_from_pdf_structure(page_texts, output_path)
    if pages and len(meaningful_pages) < pages:
        warnings.append("Algunas páginas no devolvieron texto extraíble.")
    return ConversionResult(
        True,
        output_path=str(output_path),
        pages=pages or len(page_texts),
        warnings=warnings,
        engine="pdftotext",
        text_blocks=blocks,
        notes_detected=notes_detected,
    )


def build_docx_from_pdf_structure(page_texts: Iterable[str], output_docx: str | Path) -> tuple[int, int]:
    blocks: list[ParagraphBlock] = []
    notes_detected = 0
    for index, page_text in enumerate(page_texts, start=1):
        clean_page = str(page_text or "").replace("\r", "")
        if not _has_meaningful_text(clean_page):
            continue
        blocks.append(ParagraphBlock("Normal", f"--- Página {index} ---"))
        paragraphs, page_notes = _page_paragraphs(clean_page)
        notes_detected += len(page_notes)
        blocks.extend(paragraphs)
        if page_notes:
            blocks.append(ParagraphBlock("Heading2", f"[Notas de página {index}]"))
            blocks.extend(ParagraphBlock("Quote", note) for note in page_notes)
    _write_minimal_docx(Path(output_docx), blocks)
    return len(blocks), notes_detected


def _page_count(pdf_path: Path) -> int:
    proc = subprocess.run(
        ["pdfinfo", str(pdf_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if proc.returncode != 0:
        return 0
    match = re.search(r"^Pages:\s+(\d+)\s*$", proc.stdout, flags=re.MULTILINE)
    return int(match.group(1)) if match else 0


def _extract_pages_text(pdf_path: Path) -> list[str]:
    pages = _page_count(pdf_path)
    if pages <= 0:
        raw = _pdftotext_page(pdf_path, None)
        return [segment for segment in raw.split("\f") if segment is not None]
    out: list[str] = []
    for page in range(1, pages + 1):
        out.append(_pdftotext_page(pdf_path, page))
    return out


def _pdftotext_page(pdf_path: Path, page: int | None) -> str:
    cmd = ["pdftotext", "-layout"]
    if page is not None:
        cmd.extend(["-f", str(page), "-l", str(page)])
    cmd.extend([str(pdf_path), "-"])
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "pdftotext_failed").strip())
    return proc.stdout


def _has_meaningful_text(text: str) -> bool:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    alpha = re.sub(r"[^A-Za-zÁÉÍÓÚáéíóúÑñÜü0-9]", "", compact)
    return len(alpha) >= 12


def _page_paragraphs(page_text: str) -> tuple[list[ParagraphBlock], list[str]]:
    lines = [line.rstrip() for line in page_text.splitlines()]
    non_empty = [line.strip() for line in lines if line.strip()]
    note_candidates: list[str] = []
    if non_empty:
        tail = non_empty[-3:]
        for candidate in tail:
            if re.match(r"^\d+[\])\.\-]?\s+\S+", candidate):
                note_candidates.append(candidate)
    note_set = set(note_candidates)
    filtered_lines = [line for line in lines if line.strip() not in note_set]

    paragraphs: list[str] = []
    current: list[str] = []
    for raw_line in filtered_lines:
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append(_join_paragraph_lines(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(_join_paragraph_lines(current))

    blocks = [ParagraphBlock(_classify_style(text), text) for text in paragraphs if text]
    return blocks, note_candidates


def _join_paragraph_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    merged = lines[0]
    for line in lines[1:]:
        if merged.endswith("-"):
            merged = merged[:-1] + line.lstrip()
        else:
            merged = f"{merged} {line.lstrip()}"
    return re.sub(r"\s+", " ", merged).strip()


def _classify_style(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return "Normal"
    if re.match(r"^(cap[ií]tulo|chapter|parte|secci[oó]n)\b", clean, flags=re.IGNORECASE):
        return "Heading1"
    if re.match(r"^\d+(\.\d+)*\s+\S+", clean):
        return "Heading2"
    if len(clean) <= 70 and clean == clean.upper() and re.search(r"[A-ZÁÉÍÓÚÑÜ]", clean):
        return "Heading1"
    if len(clean) <= 80 and clean[:1].isupper() and not clean.endswith((".", ";", ":", ",")):
        words = clean.split()
        titled = sum(1 for word in words if word[:1].isupper())
        if titled >= max(1, len(words) - 1):
            return "Heading2"
    return "Normal"


def _write_minimal_docx(output_path: Path, blocks: list[ParagraphBlock]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml())
        zf.writestr("_rels/.rels", _rels_xml())
        zf.writestr("docProps/core.xml", _core_xml())
        zf.writestr("docProps/app.xml", _app_xml())
        zf.writestr("word/styles.xml", _styles_xml())
        zf.writestr("word/document.xml", _document_xml(blocks))


def _document_xml(blocks: list[ParagraphBlock]) -> str:
    paragraphs = []
    for block in blocks:
        text = escape(block.text or "")
        style = escape(block.style or "Normal")
        paragraphs.append(
            "<w:p>"
            "<w:pPr><w:pStyle w:val=\"%s\"/></w:pPr>"
            "<w:r><w:t xml:space=\"preserve\">%s</w:t></w:r>"
            "</w:p>" % (style, text)
        )
    body = "".join(paragraphs) or "<w:p><w:r><w:t></w:t></w:r></w:p>"
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:wpc=\"http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas\" "
        "xmlns:mc=\"http://schemas.openxmlformats.org/markup-compatibility/2006\" "
        "xmlns:o=\"urn:schemas-microsoft-com:office:office\" "
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" "
        "xmlns:m=\"http://schemas.openxmlformats.org/officeDocument/2006/math\" "
        "xmlns:v=\"urn:schemas-microsoft-com:vml\" "
        "xmlns:wp14=\"http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing\" "
        "xmlns:wp=\"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing\" "
        "xmlns:w10=\"urn:schemas-microsoft-com:office:word\" "
        "xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" "
        "xmlns:w14=\"http://schemas.microsoft.com/office/word/2010/wordml\" "
        "xmlns:wpg=\"http://schemas.microsoft.com/office/word/2010/wordprocessingGroup\" "
        "xmlns:wpi=\"http://schemas.microsoft.com/office/word/2010/wordprocessingInk\" "
        "xmlns:wne=\"http://schemas.microsoft.com/office/word/2006/wordml\" "
        "xmlns:wps=\"http://schemas.microsoft.com/office/word/2010/wordprocessingShape\" mc:Ignorable=\"w14 wp14\">"
        "<w:body>%s<w:sectPr><w:pgSz w:w=\"12240\" w:h=\"15840\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\" w:header=\"708\" w:footer=\"708\" w:gutter=\"0\"/></w:sectPr></w:body>"
        "</w:document>" % body
    )


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:rPr><w:b/><w:sz w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:rPr><w:b/><w:sz w:val="28"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Quote">
    <w:name w:val="Quote"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:ind w:left="720"/></w:pPr>
    <w:rPr><w:i/></w:rPr>
  </w:style>
</w:styles>
"""


def _content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""


def _rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""


def _core_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>PDF convertido</dc:title>
  <dc:creator>Fusion Reader v2</dc:creator>
</cp:coreProperties>
"""


def _app_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Fusion Reader v2</Application>
</Properties>
"""
