import sys
import argparse
import re
from pathlib import Path
from collections import Counter

# Common connectors and short words in Spanish for splitting glued words
CONNECTORS = [
    "que", "de", "del", "dela", "en", "el", "la", "los", "las", "mi", "me", "se", 
    "con", "por", "para", "una", "uno", "todo", "toda", "un", "al", "ala", "alos",
    "su", "sus", "si", "no", "ni", "ya", "o", "u", "e", "y"
]

# Proper names and specific game terms that should NOT be split
PROTECTED_TERMS = {
    "Ars", "Magica", "Hermes", "Hermetica", "Hermética", "Jerbiton", "Bonisagus",
    "Bjornaer", "Flambeau", "Tremere", "Tytalus", "Verditius", "Criamon",
    "Merinita", "Quaesitor", "Quaesitores", "Miscellanea", "Intellego", "Creo",
    "Muto", "Perdo", "Rego", "Corpus", "Mentem", "Animal", "Aquam", "Auram",
    "Ignem", "Terram", "Vim", "Dominio", "Duendes", "Voluntas", "Blackthorn",
    "Semitae", "Schola", "Pythagoranis", "Antoninus", "Jocelin", "Fulk"
}

def remove_image_placeholders(markdown: str) -> str:
    """Remove all image-related placeholders and markers."""
    markdown = re.sub(r'!\[.*?\]\(.*?\)', '', markdown)
    markdown = re.sub(r'<img.*?>', '', markdown, flags=re.IGNORECASE | re.DOTALL)
    markdown = re.sub(r'<!--.*?image.*?-->', '', markdown, flags=re.IGNORECASE)
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
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    return text

def repair_glued_words(text: str) -> str:
    """Repair words glued together due to OCR failures."""
    # 1. Exact frequent patterns (highly safe replacements)
    # We use a helper to maintain case if it starts with Upper
    exact_patterns = {
        r'\bediciony\b': 'edición y',
        r'\bedicionen\b': 'edición en',
        r'\bdela\b': 'de la',
        r'\bdelas\b': 'de las',
        r'\bdelos\b': 'de los',
        r'\benel\b': 'en el',
        r'\benla\b': 'en la',
        r'\balos\b': 'a los',
        r'\bala\b': 'a la',
        r'\bconel\b': 'con el',
        r'\bconla\b': 'con la',
        r'\bconlos\b': 'con los',
        r'\bporla\b': 'por la',
        r'\bporlo\b': 'por lo',
        r'\bparaque\b': 'para que',
        r'\bantesdeque\b': 'antes de que',
        r'\bdespuesde\b': 'después de',
        r'\btodoel\b': 'todo el',
        r'\btodala\b': 'toda la',
        r'\btodamivida\b': 'toda mi vida',
        r'\bmivida\b': 'mi vida',
        r'\bmipadre\b': 'mi padre',
        r'\bmimaestra\b': 'mi maestra',
        r'\bestelibro\b': 'este libro',
        r'\bestemundo\b': 'este mundo',
        r'\bestavez\b': 'esta vez',
        r'\bestabaen\b': 'estaba en',
        r'\bsaliopara\b': 'salió para',
        r'\btratamoscon\b': 'tratamos con',
        r'\btodoslos\b': 'todos los',
        r'\btodaslas\b': 'todas las',
        r'\bNohay\b': 'No hay',
        r'\bniiglesia\b': 'ni iglesia',
        r'\bDiariode\b': 'Diario de',
        r'\bDiseniooriginal\b': 'Diseño original',
        r'\bDesarrollodela\b': 'Desarrollo de la',
        r'\bCuartaEdicion\b': 'Cuarta Edición',
        r'\bmajestuosoqueha\b': 'majestuoso que ha',
        r'\bobservadotodamivida\b': 'observado toda mi vida',
        r'\bLlegueaestemundodesufrimientoenelanode\b': 'Llegué a este mundo de sufrimiento en el año de',
        r'\bFulkmehabloantesdeque\b': 'Fulk me habló antes de que',
        r'\bLaspalabrasrompieroncualquierhechizoenelqueme\b': 'Las palabras rompieron cualquier hechizo en el que me',
        r'\bwizardsofthecoast\b': 'Wizards of the Coast',
        r'\balnorte\b': 'al norte',
        r'\bNuestroviaje\b': 'Nuestro viaje',
        r'\bNuestroSenor\b': 'Nuestro Señor',
    }
    
    def replace_keep_case(match, replacement):
        word = match.group(0)
        if word[0].isupper():
            # Try to capitalize the first word of replacement
            parts = replacement.split()
            if parts:
                parts[0] = parts[0].capitalize()
                return " ".join(parts)
        return replacement

    for p, r in exact_patterns.items():
        # We need to handle the replacement carefully to preserve capitalization
        text = re.sub(p, lambda m: replace_keep_case(m, r), text, flags=re.IGNORECASE)
        
    # 2. Separate lowercase+Uppercase (CamelCase)
    def split_camel(match):
        full = match.group(0)
        if full in PROTECTED_TERMS:
            return full
        return match.group(1) + " " + match.group(2)
        
    text = re.sub(r'([a-z])([A-Z])', split_camel, text)
    
    return text

def fix_punctuation_spacing(text: str) -> str:
    """Add spaces after punctuation when missing."""
    text = re.sub(r'([,:;])([a-zA-ZáéíóúÁÉÍÓÚñÑ])', r'\1 \2', text)
    text = re.sub(r'(\.)([a-zA-ZáéíóúÁÉÍÓÚñÑ]{2,})', r'\1 \2', text)
    return text

def remove_repeated_running_headers(lines: list[str]) -> list[str]:
    """Detect and remove headers/footers that repeat across pages."""
    if len(lines) < 20:
        return lines
    counts = Counter(line.strip() for line in lines if line.strip() and len(line.strip()) < 50)
    running_headers = {line for line, count in counts.items() if count > 2}
    header_patterns = [
        r'Ars Magica', r'introducción', r'personajes', r'alianza',
        r'\d+$'
    ]
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        is_header = False
        if stripped in running_headers:
            for pattern in header_patterns:
                if re.search(pattern, stripped, re.I):
                    is_header = True
                    break
        if is_header:
            if line.startswith("#"):
                cleaned_lines.append(line)
            else:
                continue
        else:
            cleaned_lines.append(line)
    return cleaned_lines

def sanitize_markdown(content: str):
    """Orchestrate the editorial cleanup of Markdown."""
    content = remove_image_placeholders(content)
    content = normalize_common_ocr_errors(content)
    content = repair_glued_words(content)
    content = fix_punctuation_spacing(content)
    
    lines = content.splitlines()
    lines = remove_repeated_running_headers(lines)
    
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if stripped.startswith("![Image]") or stripped.startswith("![Table]"):
            continue
        if len(stripped) > 80 and re.match(r'^[a-zA-Z0-9+/=]+$', stripped):
            continue
        if len(stripped) == 1 and ord(stripped[0]) > 0x007F and not re.match(r'[¡¿áéíóúÁÉÍÓÚñÑüÜ]', stripped):
            continue
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
