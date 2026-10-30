from io import BytesIO
import re

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
MARGIN = 42
BODY_COLOR = (0.18, 0.22, 0.30)
HEADING_COLOR = (0.08, 0.13, 0.24)
MUTED_COLOR = (0.35, 0.39, 0.47)


def _wrap(text: str, font_size: float, bold: bool) -> list[str]:
    max_chars = max(18, int((PAGE_WIDTH - 2 * MARGIN) / (font_size * (0.56 if bold else 0.52))))
    words = re.sub(r"\s+", " ", text).split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _pdf_string(text: str) -> str:
    text = text.replace("·", "-").replace("—", "-").replace("–", "-")
    encoded = text.encode("cp1252", errors="replace")
    escaped = bytearray()
    for byte in encoded:
        if byte in (ord("("), ord(")"), ord("\\")):
            escaped.append(ord("\\"))
            escaped.append(byte)
        elif byte < 32 or byte > 126:
            escaped.extend(f"\\{byte:03o}".encode("ascii"))
        else:
            escaped.append(byte)
    return escaped.decode("ascii")


def create_one_page_summary(subject_name: str, summary: dict, sources: list[dict]) -> bytes:
    blocks: list[tuple[str, float, bool, tuple[float, float, float], float]] = [
        (subject_name, 20, True, HEADING_COLOR, 3),
        ("ONE-PAGE STUDY SUMMARY | Key topics and definitions", 8, False, MUTED_COLOR, 10),
        ("MOST IMPORTANT TOPICS", 12, True, HEADING_COLOR, 6),
    ]
    for index, topic in enumerate(summary["topics"], 1):
        blocks.append((f"{index}. {topic['title']}", 9.5, True, HEADING_COLOR, 1))
        details = " ".join([topic["importance"], *topic["key_points"]])
        blocks.append((details, 8.5, False, BODY_COLOR, 5))

    blocks.append(("KEY DEFINITIONS", 12, True, HEADING_COLOR, 6))
    for item in summary["definitions"]:
        blocks.append((f"{item['term']}: {item['definition']}", 8.5, False, BODY_COLOR, 4))

    filenames = sorted({source["file"] for source in sources})
    source_note = f"Based on {len(sources)} pages/slides across {len(filenames)} files: "
    source_note += ", ".join(filenames)
    blocks.append((source_note, 7, False, MUTED_COLOR, 0))

    content_height = PAGE_HEIGHT - 2 * MARGIN

    def layout(
        scale: float,
    ) -> tuple[list[tuple[str, float, bool, tuple[float, float, float], float, bool]], float]:
        lines = []
        used_height = 0.0
        for text, base_size, bold, color, spacing_after in blocks:
            font_size = base_size * scale
            wrapped = _wrap(text, font_size, bold)
            section_rule = text in ("MOST IMPORTANT TOPICS", "KEY DEFINITIONS")
            for index, line in enumerate(wrapped):
                last_line = index == len(wrapped) - 1
                lines.append((
                    line,
                    font_size,
                    bold,
                    color,
                    (spacing_after * scale) if last_line else 0,
                    section_rule and last_line,
                ))
            leading = font_size * 1.6
            section_rule = 3 if text in ("MOST IMPORTANT TOPICS", "KEY DEFINITIONS") else 0
            used_height += len(wrapped) * leading + spacing_after * scale + section_rule
        return lines, used_height

    scale = 1.0
    lines, used_height = layout(scale)
    while used_height > content_height and scale > 0.55:
        scale *= content_height / used_height * 0.96
        lines, used_height = layout(scale)
    if used_height > content_height:
        raise ValueError("The subject summary is too long to fit legibly on one page")

    stream_lines = ["q", "1 1 1 rg", f"0 0 {PAGE_WIDTH} {PAGE_HEIGHT} re f"]
    y = PAGE_HEIGHT - MARGIN
    for line, font_size, bold, color, spacing_after, section_rule in lines:
        y -= font_size * 1.3
        red, green, blue = color
        font = "F2" if bold else "F1"
        stream_lines.append(
            f"BT /{font} {font_size:.2f} Tf {red:.3f} {green:.3f} {blue:.3f} rg "
            f"1 0 0 1 {MARGIN} {y:.2f} Tm ({_pdf_string(line)}) Tj ET"
        )
        y -= font_size * 0.3
        if section_rule:
            y -= 3
            stream_lines.append(
                f"0.87 0.89 0.92 RG 0.6 w {MARGIN} {y:.2f} m "
                f"{PAGE_WIDTH - MARGIN} {y:.2f} l S"
            )
        y -= spacing_after
    stream_lines.append("Q")

    writer = PdfWriter()
    page = writer.add_blank_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    font_regular = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    }))
    font_bold = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    }))
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): font_regular,
            NameObject("/F2"): font_bold,
        })
    })
    content_stream = DecodedStreamObject()
    content_stream.set_data("\n".join(stream_lines).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content_stream)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()
