import sys
import argparse
from pathlib import Path

try:
    from docx import Document
    from docx.shared import Pt
except ImportError:
    print("Error: python-docx not installed in this environment.")
    sys.exit(1)

def convert_md_to_docx(md_path: Path, docx_path: Path):
    if not md_path.exists():
        print(f"Error: Markdown file not found at {md_path}")
        sys.exit(1)

    doc = Document()
    content = md_path.read_text(encoding="utf-8")

    # Simple Markdown parser for headings and paragraphs
    lines = content.splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("# "):
            doc.add_heading(line[2:], level=0)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=1)
        elif line.startswith("### "):
            doc.add_heading(line[4:], level=2)
        elif line.startswith("#### "):
            doc.add_heading(line[5:], level=3)
        elif line.startswith("- ") or line.startswith("* "):
            doc.add_paragraph(line[2:], style='List Bullet')
        elif line.startswith("|") and "|" in line[1:]:
            # Simple table placeholder or skip if complex
            doc.add_paragraph(line, style='Quote')
        else:
            doc.add_paragraph(line)

    doc.save(str(docx_path))
    print(f"Successfully converted {md_path} to {docx_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Input Markdown file")
    parser.add_argument("output", help="Output DOCX file")
    args = parser.parse_args()
    
    convert_md_to_docx(Path(args.input), Path(args.output))
