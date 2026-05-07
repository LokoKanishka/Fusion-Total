import sys
import argparse
import re
from pathlib import Path

def sanitize_markdown(content: str):
    """Remove images, base64 and noise from Docling Markdown for a text-first output."""
    # 1. Remove explicit markdown images: ![alt](path_or_base64)
    content = re.sub(r'!\[.*?\]\(.*?\)', '', content)
    
    # 2. Remove HTML images: <img ...>
    content = re.sub(r'<img.*?>', '', content, flags=re.IGNORECASE | re.DOTALL)
    
    # 3. Remove base64 data URIs that might be outside of markdown tags
    content = re.sub(r'data:image\/[a-zA-Z]*;base64,[a-zA-Z0-9+/=]*', '', content)
    
    lines = content.splitlines()
    cleaned_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        if not stripped:
            cleaned_lines.append("")
            continue
            
        # Skip Docling specific placeholders that often point to images/tables-as-images
        if stripped.startswith("![Image]") or stripped.startswith("![Table]"):
            continue
            
        # Skip lines that are likely just long base64 chunks (noise)
        if len(stripped) > 60 and re.match(r'^[a-zA-Z0-9+/=]+$', stripped):
            continue
            
        # Skip standalone noise characters (often OCR artifacts like '福', '§', etc.)
        # We allow common punctuation but skip isolated high-unicode symbols if alone on a line
        if len(stripped) == 1 and ord(stripped[0]) > 0x007F and not re.match(r'[¡¿áéíóúÁÉÍÓÚñÑüÜ]', stripped):
            continue
            
        cleaned_lines.append(line)
        
    # Rejoin and compress multiple blank lines
    result = "\n".join(cleaned_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    
    return result

def convert_md_to_docx(md_path: Path, docx_path: Path):
    if not md_path.exists():
        print(f"Error: Markdown file not found at {md_path}")
        sys.exit(1)

    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        print("Error: python-docx not installed in this environment.")
        sys.exit(1)

    try:
        raw_content = md_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Error reading Markdown: {e}")
        sys.exit(1)
        
    content = sanitize_markdown(raw_content)

    doc = Document()
    
    # Basic style configuration
    if 'Normal' in doc.styles:
        style = doc.styles['Normal']
        font = style.font
        font.name = 'Calibri'
        font.size = Pt(11)

    lines = content.splitlines()
    
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        # Headings
        if line_clean.startswith("# "):
            doc.add_heading(line_clean[2:], level=0)
        elif line_clean.startswith("## "):
            doc.add_heading(line_clean[3:], level=1)
        elif line_clean.startswith("### "):
            doc.add_heading(line_clean[4:], level=2)
        elif line_clean.startswith("#### "):
            doc.add_heading(line_clean[5:], level=3)
        
        # Lists
        elif line_clean.startswith("- ") or line_clean.startswith("* "):
            doc.add_paragraph(line_clean[2:], style='List Bullet')
        elif re.match(r'^\d+\. ', line_clean):
            # Numeric lists
            text = re.sub(r'^\d+\. ', '', line_clean)
            doc.add_paragraph(text, style='List Number')
            
        # Tables (Simplified handling: keep text, mark as quote)
        elif line_clean.startswith("|") and "|" in line_clean[1:]:
            # Skip separator lines
            if re.match(r'^[ \|:\-]+$', line_clean):
                continue
            p = doc.add_paragraph(line_clean)
            try:
                p.style = 'Quote'
            except:
                pass # Fallback to normal if style missing
            
        # Normal Paragraph
        else:
            doc.add_paragraph(line_clean)

    try:
        doc.save(str(docx_path))
        print(f"Successfully converted {md_path} to {docx_path}")
    except Exception as e:
        print(f"Error saving DOCX: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Input Markdown file")
    parser.add_argument("output", help="Output DOCX file")
    args = parser.parse_args()
    
    convert_md_to_docx(Path(args.input), Path(args.output))
