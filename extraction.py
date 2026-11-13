"""Bounded document extraction. Image OCR requires a local Tesseract binary."""
from contextlib import closing
from io import BytesIO
import json
from pathlib import PurePath
import shutil
import subprocess
import sys
from zipfile import BadZipFile, ZipFile

from docx import Document
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
import pypdfium2

MAX_UPLOAD = 8 * 1024 * 1024
MAX_PAGES = 30
MAX_TEXT = 100000
MAX_PIXELS = 24_000_000
MIMES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
}


def inspect(filename, raw):
    extension = PurePath(filename).suffix.lower()
    if extension not in MIMES:
        raise ValueError("unsupported_file")
    markers = {".pdf": b"%PDF-", ".docx": b"PK\x03\x04", ".png": b"\x89PNG\r\n\x1a\n",
               ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff"}
    if not raw.startswith(markers[extension]):
        raise ValueError("file_signature_mismatch")
    return extension, MIMES[extension]


def ocr(image, language):
    binary = shutil.which("tesseract")
    if not binary:
        return "", "ocr_unavailable"
    image.thumbnail((3000, 3000))
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    try:
        result = subprocess.run([binary, "stdin", "stdout", "-l", language],
                                input=buffer.getvalue(), capture_output=True, timeout=14, check=True)
    except (OSError, subprocess.SubprocessError):
        return "", "ocr_failed"
    return result.stdout.decode("utf-8", errors="replace"), "extracted"


def extract_pdf(raw, languages):
    document = PdfReader(BytesIO(raw))
    if document.is_encrypted or len(document.pages) > MAX_PAGES:
        raise ValueError("unsupported_pdf")
    result = []
    warnings = set()
    raster = None
    try:
        for number, page in enumerate(document.pages, 1):
            value = (page.extract_text() or "").strip()
            if not value:
                if not shutil.which("tesseract"):
                    warnings.add("ocr_unavailable")
                    continue
                if raster is None:
                    raster = pypdfium2.PdfDocument(raw)
                with closing(raster[number - 1]) as raster_page:
                    width, height = raster_page.get_size()
                    if width <= 0 or height <= 0:
                        raise ValueError("invalid_pdf_page")
                    with closing(raster_page.render(scale=min(2, 2500 / max(width, height)))) as bitmap:
                        value, status = ocr(bitmap.to_pil(), languages)
                if status != "extracted":
                    warnings.add(status)
            if value.strip():
                result.append({"location": f"page:{number}", "text": value.strip()})
    finally:
        if raster is not None:
            raster.close()
    return result, warnings


def extract_docx(raw):
    with ZipFile(BytesIO(raw)) as archive:
        files = archive.infolist()
        if len(files) > 500 or sum(entry.file_size for entry in files) > 32 * 1024 * 1024:
            raise ValueError("docx_too_large")
        if "word/document.xml" not in archive.namelist():
            raise ValueError("invalid_docx")
    document = Document(BytesIO(raw))
    lines = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text.strip() for cell in row.cells))
    return [{"location": "body", "text": "\n".join(lines)}] if lines else []


def extract(extension, raw, languages):
    warnings = set()
    if extension == ".pdf":
        chunks, warnings = extract_pdf(raw, languages)
    elif extension == ".docx":
        chunks = extract_docx(raw)
    else:
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("image_too_large")
            value, status = ocr(ImageOps.exif_transpose(image), languages)
            chunks = [{"location": "image:1", "text": value.strip()}] if value.strip() else []
            if status != "extracted":
                warnings.add(status)
    bounded = []
    remaining = MAX_TEXT
    for item in chunks:
        if len(item["text"]) > remaining:
            warnings.add("extraction_truncated")
        if remaining:
            bounded.append({**item, "text": item["text"][:remaining]})
            remaining -= len(bounded[-1]["text"])
    return {
        "status": "partial" if bounded and warnings else "extracted" if bounded else "no_text",
        "warnings": sorted(warnings), "chunks": bounded,
    }


def extract_isolated(extension, raw, languages):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "zhkh_legal.extraction", extension, languages],
            input=raw, capture_output=True, timeout=45, check=True,
        )
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {"status": "no_text", "warnings": ["extraction_timeout"], "chunks": []}
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        raise ValueError("invalid_or_unreadable_document") from error


if __name__ == "__main__":
    try:
        print(json.dumps(extract(sys.argv[1], sys.stdin.buffer.read(MAX_UPLOAD + 1), sys.argv[2])))
    except (ValueError, BadZipFile, UnidentifiedImageError):
        sys.exit(2)
