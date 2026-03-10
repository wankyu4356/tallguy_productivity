from __future__ import annotations

import io
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from pypdf.generic import Fit
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Flowable, HRFlowable,
)
from reportlab.lib.colors import black

from app.models.schemas import ClassifiedOutput, ArticleWithContent
from app.utils.logging import get_logger

logger = get_logger(__name__)

_FONTS_REGISTERED = False

# Page dimensions
_PAGE_W, _PAGE_H = A4
_LEFT_MARGIN = 25 * mm
_RIGHT_MARGIN = 25 * mm
_CONTENT_WIDTH = _PAGE_W - _LEFT_MARGIN - _RIGHT_MARGIN


# ---------------------------------------------------------------------------
# Font registration
# ---------------------------------------------------------------------------

def _register_fonts() -> tuple[str, str]:
    """Register Korean fonts. Returns (regular, bold) font names."""
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return "KFont", "KFontBold"

    font_configs = [
        # 새굴림 (New Gulim) — user-specified font; Windows-only, may be copied here
        ("/usr/share/fonts/truetype/gulim/NewGulim.ttf",
         "/usr/share/fonts/truetype/gulim/NewGulim.ttf", None),
        # NanumBarunGothic — closest Linux substitute for 새굴림 (rounded gothic)
        ("/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
         "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf", None),
        # Fallback to NanumGothic (sans-serif)
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
         "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", None),
        # Noto CJK variants
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
        ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", 0),
        # WenQuanYi (CJK fallback)
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
         "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ]

    for reg_path, bold_path, sub_idx in font_configs:
        if not Path(reg_path).exists():
            continue
        try:
            kwargs = {"subfontIndex": sub_idx} if sub_idx is not None else {}
            pdfmetrics.registerFont(TTFont("KFont", reg_path, **kwargs))
            if Path(bold_path).exists():
                pdfmetrics.registerFont(TTFont("KFontBold", bold_path, **kwargs))
            else:
                pdfmetrics.registerFont(TTFont("KFontBold", reg_path, **kwargs))
            _FONTS_REGISTERED = True
            logger.info(f"Registered Korean font: {reg_path}")
            return "KFont", "KFontBold"
        except Exception as e:
            logger.warning(f"Failed to register font {reg_path}: {e}")
            continue

    logger.warning("No Korean font found — falling back to Helvetica")
    return "Helvetica", "Helvetica-Bold"


# ---------------------------------------------------------------------------
# Custom flowable: TOC entry with dot leader + page number + link tracking
# ---------------------------------------------------------------------------

class _TOCLine(Flowable):
    """A single TOC line: title ....... page_number

    Also records its drawn position for clickable-link creation.
    """

    def __init__(self, title: str, page_num: int, font_name: str,
                 font_size: float = 10, article_id: str | None = None,
                 target_page: int | None = None):
        Flowable.__init__(self)
        self.title = title
        self.page_num = page_num
        self.font_name = font_name
        self.font_size = font_size
        self.article_id = article_id
        self.target_page = target_page
        # Recorded after draw
        self.drawn_page_idx: int | None = None
        self.drawn_y: float | None = None

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        self.height = self.font_size + 4  # line height with spacing
        return self.width, self.height

    def draw(self):
        c = self.canv
        fs = self.font_size
        fn = self.font_name

        c.setFont(fn, fs)

        # Measure title and page number widths
        page_str = str(self.page_num)
        page_w = c.stringWidth(page_str, fn, fs)
        title_w = c.stringWidth(self.title, fn, fs)

        # Draw title (left-aligned)
        c.drawString(0, 2, self.title)

        # Draw page number (right-aligned)
        c.drawRightString(self.width, 2, page_str)

        # Fill the gap with dots
        dot = "\u00B7"  # middle dot (·)
        dot_w = c.stringWidth(dot + " ", fn, fs)
        gap_start = title_w + 4
        gap_end = self.width - page_w - 4

        if gap_end > gap_start and dot_w > 0:
            num_dots = int((gap_end - gap_start) / dot_w)
            dots = (dot + " ") * num_dots
            c.drawString(gap_start, 2, dots)


class _TOCDocTemplate(SimpleDocTemplate):
    """DocTemplate that records positions of _TOCLine flowables."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.toc_entries: list[dict] = []

    def afterFlowable(self, flowable):
        if isinstance(flowable, _TOCLine) and flowable.article_id:
            frame = self.frame
            page_idx = self.page - 1  # 0-indexed
            y_bottom = frame._y
            y_top = y_bottom + flowable.height
            self.toc_entries.append({
                "article_id": flowable.article_id,
                "target_page": flowable.target_page,
                "page_idx": page_idx,
                "y_bottom": y_bottom,
                "y_top": y_top,
                "x_left": frame._x1,
                "x_right": frame._x1 + frame._width,
            })


# ---------------------------------------------------------------------------
# Build index PDF (flat list matching reference image)
# ---------------------------------------------------------------------------

def _build_index_pdf(
    ordered_articles: list[ArticleWithContent],
    page_offsets: dict[str, int],
) -> tuple[bytes, list[dict]]:
    """Generate a flat-list TOC page matching the reference format.

    Format:
        콘텐츠
        ─────────────────────────────
        기사 제목 ··················· 페이지
        기사 제목 ··················· 페이지
        ...

    Returns (pdf_bytes, toc_link_info).
    """
    buf = io.BytesIO()
    font_name, font_name_bold = _register_fonts()

    doc = _TOCDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=_LEFT_MARGIN,
        rightMargin=_RIGHT_MARGIN,
        topMargin=25 * mm,
        bottomMargin=20 * mm,
    )

    title_style = ParagraphStyle(
        "TOC_Title",
        fontName=font_name_bold,
        fontSize=16,
        leading=20,
        alignment=1,  # center
        spaceAfter=4 * mm,
    )

    elements: list = []

    # Title: 콘텐츠
    elements.append(Paragraph("콘텐츠", title_style))

    # Horizontal rule
    elements.append(HRFlowable(
        width="100%", thickness=1, color=black,
        spaceAfter=4 * mm, spaceBefore=2 * mm,
    ))

    # Article entries as flat list
    for a in ordered_articles:
        page_num = page_offsets.get(a.info.id, 0)
        entry = _TOCLine(
            title=a.info.title,
            page_num=page_num,
            font_name=font_name,
            font_size=10,
            article_id=a.info.id,
            target_page=page_num,
        )
        elements.append(entry)
        elements.append(Spacer(1, 2 * mm))

    doc.build(elements)
    return buf.getvalue(), doc.toc_entries


# ---------------------------------------------------------------------------
# Merge PDFs
# ---------------------------------------------------------------------------

def merge_pdfs(
    classification: ClassifiedOutput,
    articles: list[ArticleWithContent],
    output_path: Path,
    on_progress: callable | None = None,
) -> Path:
    """Merge individual article PDFs into a single file with index and bookmarks."""
    articles_map = {a.info.id: a for a in articles if a.pdf_path}
    writer = PdfWriter()

    # Collect ordered articles
    ordered_articles: list[ArticleWithContent] = []
    seen_ids: set[str] = set()
    for aid in classification.article_order:
        a = articles_map.get(aid)
        if a and a.pdf_path and Path(a.pdf_path).exists():
            ordered_articles.append(a)
            seen_ids.add(aid)

    # Add articles we might have missed
    for a in articles:
        if a.info.id not in seen_ids and a.pdf_path and Path(a.pdf_path).exists():
            ordered_articles.append(a)

    if not ordered_articles:
        logger.warning("No PDFs to merge")
        return output_path

    # Pre-read page counts
    article_page_counts: dict[str, int] = {}
    for a in ordered_articles:
        try:
            reader = PdfReader(a.pdf_path)
            article_page_counts[a.info.id] = len(reader.pages)
        except Exception as e:
            logger.error(f"Error reading PDF {a.pdf_path}: {e}")

    # First pass: rough page offsets (without index pages)
    page_offsets: dict[str, int] = {}
    current_page = 1
    for a in ordered_articles:
        pc = article_page_counts.get(a.info.id, 0)
        if pc:
            page_offsets[a.info.id] = current_page
            current_page += pc

    # Generate index to determine its page count
    if on_progress:
        on_progress("인덱스 페이지 생성 중...")
    index_pdf_bytes, _ = _build_index_pdf(ordered_articles, page_offsets)
    index_page_count = len(PdfReader(io.BytesIO(index_pdf_bytes)).pages)

    # Second pass: recalculate with index pages included
    page_offsets_final: dict[str, int] = {}
    current_page = index_page_count + 1
    for a in ordered_articles:
        pc = article_page_counts.get(a.info.id, 0)
        if pc:
            page_offsets_final[a.info.id] = current_page
            current_page += pc

    # Regenerate index with correct page numbers
    index_pdf_bytes, toc_entries = _build_index_pdf(
        ordered_articles, page_offsets_final
    )
    index_reader = PdfReader(io.BytesIO(index_pdf_bytes))

    # Add index pages
    for page in index_reader.pages:
        writer.add_page(page)

    # Add article PDFs with bookmarks
    if on_progress:
        on_progress("PDF 합본 중...")

    article_first_page: dict[str, int] = {}

    for i, a in enumerate(ordered_articles):
        try:
            reader = PdfReader(a.pdf_path)
            first_page_idx = len(writer.pages)
            article_first_page[a.info.id] = first_page_idx

            for page in reader.pages:
                writer.add_page(page)

            writer.add_outline_item(a.info.title, first_page_idx)

            if on_progress:
                on_progress(f"PDF 합본: {i + 1}/{len(ordered_articles)}")
        except Exception as e:
            logger.error(f"Error merging PDF for '{a.info.title}': {e}")

    # Add clickable links on TOC pages → jump to article pages
    for entry in toc_entries:
        aid = entry["article_id"]
        dest_page_idx = article_first_page.get(aid)
        if dest_page_idx is None:
            continue
        toc_page_idx = entry["page_idx"]
        try:
            rect = (
                entry["x_left"],
                entry["y_bottom"],
                entry["x_right"],
                entry["y_top"],
            )
            link = Link(
                rect=rect,
                target_page_index=dest_page_idx,
                fit=Fit.fit_horizontally(top=800),
                border=[0, 0, 0],
            )
            writer.add_annotation(page_number=toc_page_idx, annotation=link)
        except Exception as e:
            logger.debug(f"Failed to add TOC link for '{aid}': {e}")

    # Write merged PDF
    with open(output_path, "wb") as f:
        writer.write(f)

    if on_progress:
        on_progress(f"PDF 합본 완료: {len(ordered_articles)}개 기사")

    return output_path
