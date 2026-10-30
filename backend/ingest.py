"""Parse PDFs and PPTs into per-page text, then split into searchable chunks."""
import re
import posixpath
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path

from pypdf import PdfReader

SUPPORTED = {".pdf", ".pptx"}


def parse_pdf(path: Path) -> dict[int, str]:
    pages = {}
    for i, page in enumerate(PdfReader(str(path)).pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages[i] = text
    return pages


def parse_pptx(path: Path) -> dict[int, str]:
    pages = {}
    ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
          'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
          'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    with zipfile.ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError('Expanded presentation is too large')
        presentation = ET.fromstring(archive.read('ppt/presentation.xml'))
        relations = ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
        targets = {item.get('Id'): item.get('Target') for item in relations
                   if item.get('TargetMode') != 'External'}
        for i, slide in enumerate(presentation.findall('p:sldIdLst/p:sldId', ns), 1):
            target = targets[slide.get('{' + ns['r'] + '}id')]
            member = target.lstrip('/') if target.startswith('/') else posixpath.normpath('ppt/' + target)
            xml = ET.fromstring(archive.read(member))
            text = '\n'.join(''.join(p.itertext()).strip() for p in xml.findall('.//a:t', ns)).strip()
            if text:
                pages[i] = text
    return pages


def parse(path: Path) -> dict[int, str]:
    if path.suffix.lower() == ".pdf":
        return parse_pdf(path)
    return parse_pptx(path)


def chunk(files: dict[str, dict[int, str]], window=160, step=120) -> list[dict]:
    """Split each page into overlapping word windows. Every chunk remembers its file and page."""
    chunks = []
    for fname, pages in files.items():
        kind = Path(fname).suffix.lower().lstrip(".")
        for page, text in pages.items():
            words = re.sub(r"\s+", " ", text).split()
            for i in range(0, max(len(words), 1), step):
                chunks.append({
                    "file": fname, "type": kind, "page": page,
                    "text": " ".join(words[i:i + window]),
                })
                if i + window >= len(words):
                    break
    return chunks
