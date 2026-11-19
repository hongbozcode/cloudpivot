import csv
import io
import math
from codecs import getincrementaldecoder
from contextlib import closing
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from threading import Lock
from zipfile import ZipFile

from markdown import markdown
from openpyxl import load_workbook
from pypdfium2 import PdfDocument
from xlrd import open_workbook


MAX_ROWS = 50
MAX_COLUMNS = 30
MAX_TEXT_BYTES = 128 * 1024
MAX_PREVIEW_BYTES = 25 * 1024 * 1024
IMAGE_TYPES = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
    '.webp': 'image/webp', '.bmp': 'image/bmp', '.ico': 'image/x-icon', '.svg': 'image/svg+xml',
    '.avif': 'image/avif',
}
TEXT_FORMATS = {'.txt', '.log', '.json', '.yaml', '.yml', '.xml', '.tsv'}
MARKDOWN_FORMATS = {'.md', '.markdown'}
WORKBOOK_FORMATS = {'.xlsx', '.xlsm', '.xltx', '.xltm', '.xls'}
_pdf_lock = Lock()


@dataclass(frozen=True)
class PdfPreview:
    png: bytes
    page_count: int


def _pdf_preview(path: Path, index: int) -> PdfPreview:
    # PDFium only allows one caller at a time, including across separate documents.
    with _pdf_lock, PdfDocument(path) as document:
        count = len(document)
        if index >= count:
            raise ValueError('PDF 页码超出范围。')
        with closing(document[index]) as page:
            longest_side = max(page.get_size())
            if not math.isfinite(longest_side) or longest_side <= 0:
                raise ValueError('PDF 页面大小无效。')
            scale = min(2, 1600 / longest_side)
            with closing(page.render(scale=scale, limit_image_cache=True)) as bitmap:
                with bitmap.to_pil() as image, io.BytesIO() as output:
                    image.save(output, format='PNG')
                    return PdfPreview(output.getvalue(), count)


def _text(data: bytes, *, truncated: bool = False) -> str:
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return getincrementaldecoder('utf-16')().decode(data, final=not truncated)
    try:
        return getincrementaldecoder('utf-8-sig')().decode(data, final=not truncated)
    except UnicodeDecodeError:
        return getincrementaldecoder('gb18030')().decode(data, final=not truncated)


def _table(rows, sheets, truncated):
    return {
        'kind': 'table', 'sheets': sheets, 'truncated': truncated,
        'rows': [[value[:2048] if isinstance(value, str) else value for value in row] for row in rows],
    }


def _workbook_preview(path: Path, extension: str, sheet: int) -> dict:
    if extension == '.xls':
        with open_workbook(filename=str(path), on_demand=True) as book:
            names = book.sheet_names()
            page = book.sheet_by_index(sheet)
            rows = [page.row_values(row, end_colx=MAX_COLUMNS) for row in range(min(page.nrows, MAX_ROWS))]
            return _table(rows, names, page.nrows > MAX_ROWS or page.ncols > MAX_COLUMNS)
    with path.open('rb') as source:
        with ZipFile(source) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > 64 * 1024 * 1024:
                raise ValueError('工作簿解包后超出预览上限。')
        book = load_workbook(source, read_only=True, data_only=True, keep_links=False)
        try:
            page = book.worksheets[sheet]
            width = min(page.max_column or MAX_COLUMNS, MAX_COLUMNS)
            rows = list(islice(page.iter_rows(max_col=width, values_only=True), MAX_ROWS + 1))
            truncated = len(rows) > MAX_ROWS or (page.max_column or 0) > MAX_COLUMNS
            return _table(rows[:MAX_ROWS], [tab.title for tab in book.worksheets], truncated)
        finally:
            book.close()


def make_preview(path: Path, extension: str, sheet: int, page: int) -> dict | PdfPreview | str | None:
    """Return structured data or the media type for an inline binary preview."""
    if path.stat().st_size > MAX_PREVIEW_BYTES:
        raise ValueError('文件超过在线预览大小上限。')
    if extension in IMAGE_TYPES:
        return IMAGE_TYPES[extension]
    if extension == '.pdf':
        return _pdf_preview(path, page)
    if extension in WORKBOOK_FORMATS:
        return _workbook_preview(path, extension, sheet)
    if extension == '.csv':
        rows = list(islice(csv.reader(io.StringIO(_text(path.read_bytes()), newline=''), strict=True), MAX_ROWS + 1))
        truncated = len(rows) > MAX_ROWS or any(len(row) > MAX_COLUMNS for row in rows)
        return _table([row[:MAX_COLUMNS] for row in rows[:MAX_ROWS]], [], truncated)
    if extension in TEXT_FORMATS | MARKDOWN_FORMATS:
        with path.open('rb') as source:
            data = source.read(MAX_TEXT_BYTES + 1)
        truncated = len(data) > MAX_TEXT_BYTES
        text = _text(data[:MAX_TEXT_BYTES], truncated=truncated)
        if '\x00' in text:
            raise ValueError('文件不是可预览的文本。')
        if extension in MARKDOWN_FORMATS:
            return {'kind': 'markdown', 'text': markdown(text, extensions=['tables', 'fenced_code']), 'truncated': truncated}
        return {'kind': 'text', 'text': text, 'truncated': truncated}
    return None
