import sys
import argparse
import re
from pathlib import Path
from collections import Counter

def remove_image_placeholders(markdown: str) -> str:
    """Remove all image-related placeholders and markers."""
    # Remove ![Image](...) and ![Table](...)
    markdown = re.sub(r'!\[.*?\]\(.*?\)', '', markdown)
    # Remove HTML tags
    markdown = re.sub(r'<img.*?>', '', markdown, flags=re.IGNORECASE | re.DOTALL)
    # Remove explicit <!-- image --> comments
    markdown = re.sub(r'<!--.*?image.*?-->', '', markdown, flags=re.IGNORECASE)
    # Remove data URIs
    markdown = re.sub(r'data:image\/[a-zA-Z]*;base64,[a-zA-Z0-9+/=]*', '', markdown)
    return markdown

def normalize_common_ocr_errors(text: str) -> str:
    """Apply conservative corrections to common OCR mistakes."""
    replacements = {
        r'\bHrs Magica\b': 'Ars Magica',
        r'\bHRs Magica\b': 'Ars Magica',
        r'\bARs magica\b': 'Ars Magica',
        r'\bARs Magica\b': 'Ars Magica',
        r'\braylagica\b': 'Ars Magica',
        r'\bCuartaEdicion\b': 'Cuarta Edición',
        r'\bEdicion\b': 'Edición',
        r'\bedicion\b': 'edición',
        r'\bDisenio\b': 'Diseño',
        r'\bdisenio\b': 'diseño',
        r'\bDetectos\b': 'Defectos',
        r'\bHechbizos\b': 'Hechizos',
        r'\bMagio Hermetica\b': 'Magia Hermética',
        r'\bCuropa Mitica\b': 'Europa Mítica',
        r'\bintroduccion\b': 'introducción',
        r'\bIntroduccion\b': 'Introducción',
        r'\bCapitulo\b': 'Capítulo',
        r'\bNarracion\b': 'Narración',
        r'\bApendice\b': 'Apéndice',
        r'\bIndice\b': 'Índice',
        r'\bLatin\b': 'Latín',
        r'\bcompaneros\b': 'compañeros',
        r'\bcompanero\b': 'compañero',
        r'\bAlianzas\b': 'Alianzas', # Ensure casing
    }
    
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
        
    return text

def fix_glued_words(text: str) -> str:
    """Fix words glued together due to OCR failures."""
    # 1. Glued patterns (conservative)
    patterns = {
        r'\benel\b': 'en el',
        r'\bdela\b': 'de la',
        r'\bdelos\b': 'de los',
        r'\bconlos\b': 'con los',
        r'\balos\b': 'a los',
        r'\bala\b': 'a la',
        r'\bporlo\b': 'por lo',
        r'\btodoel\b': 'todo el',
        r'\bmimaestra\b': 'mi maestra',
        r'\belque\b': 'el que',
        r'\bqueno\b': 'que no',
        r'\bsunombre\b': 'su nombre',
        r'\besun\b': 'es un',
        r'\bcomoel\b': 'como el',
        r'\bsedecia\b': 'se decía',
        r'\bsefuera\b': 'se fuera',
    }
    for p, r in patterns.items():
        text = re.sub(p, r, text)
        
    # 2. Lowercase followed by Uppercase (e.g. QueDios -> Que Dios)
    # Only if it's not a common camelCase in technical docs (unlikely in this context)
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    
    return text

def fix_punctuation_spacing(text: str) -> str:
    """Add spaces after punctuation when missing."""
    # After comma, period, colon, semicolon if followed by a letter
    text = re.sub(r'([,:;])([a-zA-ZáéíóúÁÉÍÓÚñÑ])', r'\1 \2', text)
    # Period is trickier due to abbreviations and numbers, be conservative
    text = re.sub(r'(\.)([a-zA-ZáéíóúÁÉÍÓÚñÑ]{2,})', r'\1 \2', text)
    return text

def remove_repeated_running_headers(lines: list[str]) -> list[str]:
    """Detect and remove headers/footers that repeat across pages."""
    if len(lines) < 20:
        return lines
        
    # Count occurrences of non-empty short lines
    counts = Counter(line.strip() for line in lines if line.strip() and len(line.strip()) < 50)
    
    # Identify lines that appear more than 3 times (threshold for book-wide headers)
    # and match known running header patterns
    running_headers = {line for line, count in counts.items() if count > 2}
    
    # Patterns to always remove if repeated
    header_patterns = [
        r'Ars Magica', r'Ars Magica', r'introducción', r'personajes', r'alianza',
        r'\d+$' # Page numbers alone
    ]
    
    cleaned_lines = []
    headers_found = set()
    
    for line in lines:
        stripped = line.strip()
        is_header = False
        
        if stripped in running_headers:
            for pattern in header_patterns:
                if re.search(pattern, stripped, re.I):
                    is_header = True
                    break
        
        if is_header:
            # Keep only if it's a heading (starts with #) or if we haven't seen it in a while
            # (Very basic heuristic: keep first occurrence in a section)
            if line.startswith("#"):
                cleaned_lines.append(line)
            else:
                # Discard running header
                continue
        else:
            cleaned_lines.append(line)
            
    return cleaned_lines

def sanitize_markdown(content: str):
    """Orchestrate the editorial cleanup of Markdown."""
    # 1. Global text-based cleaning
    content = remove_image_placeholders(content)
    content = normalize_common_ocr_errors(content)
    content = fix_glued_words(content)
    content = fix_punctuation_spacing(content)
    
    # 2. Line-based cleaning
    lines = content.splitlines()
    lines = remove_repeated_running_headers(lines)
    
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        
        if not stripped:
            cleaned_lines.append("")
            continue
            
        # Skip noise leftovers
        if stripped.startswith("![Image]") or stripped.startswith("![Table]"):
            continue
        if len(stripped) > 80 and re.match(r'^[a-zA-Z0-9+/=]+$', stripped):
            continue
        if len(stripped) == 1 and ord(stripped[0]) > 0x007F and not re.match(r'[¡¿áéíóúÁÉÍÓÚñÑüÜ]', stripped):
            continue
            
        # Clean up HTML entities
        line = line.replace("&amp;", "&").replace("&quot;", "\"").replace("&lt;", "<").replace("&gt;", ">")
        
        cleaned_lines.append(line)
        
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

        if line_clean.startswith("# "):
            doc.add_heading(line_clean[2:], level=0)
        elif line_clean.startswith("## "):
            doc.add_heading(line_clean[3:], level=1)
        elif line_clean.startswith("### "):
            doc.add_heading(line_clean[4:], level=2)
        elif line_clean.startswith("#### "):
            doc.add_heading(line_clean[5:], level=3)
        elif line_clean.startswith("- ") or line_clean.startswith("* "):
            doc.add_paragraph(line_clean[2:], style='List Bullet')
        elif re.match(r'^\d+\. ', line_clean):
            text = re.sub(r'^\d+\. ', '', line_clean)
            doc.add_paragraph(text, style='List Number')
        elif line_clean.startswith("|") and "|" in line_clean[1:]:
            if re.match(r'^[ \|:\-]+$', line_clean):
                continue
            p = doc.add_paragraph(line_clean)
            try: p.style = 'Quote'
            except: pass
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
