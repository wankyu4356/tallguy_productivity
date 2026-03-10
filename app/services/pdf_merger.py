from __future__ import annotations

import io
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from pypdf.generic import Fit
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Flowable

from app.models.schemas import ClassifiedOutput, ArticleWithContent
from app.utils.logging import get_logger

logger = get_logger(__name__)

_FONTS_REGISTERED = False


def _register_fonts() -> tuple[str, str]:
    """Register Korean fonts. Returns (regular, bold) font names."""
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return "KoreanFont", "KoreanFontBold"

    font_configs = [
        # (regular_path, bold_path, subfontIndex)
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
         "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", None),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
        ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", 0),
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
         "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ]

    for reg_path, bold_path, sub_idx in font_configs:
        if not Path(reg_path).exists():
            continue
        try:
            kwargs = {"subfontIndex": sub_idx} if sub_idx is not None else {}
            pdfmetrics.registerFont(TTFont("KoreanFont", reg_path, **kwargs))
            if Path(bold_path).exists():
                pdfmetrics.registerFont(TTFont("KoreanFontBold", bold_path, **kwargs))
            else:
                pdfmetrics.registerFont(TTFont("KoreanFontBold", reg_path, **kwargs))
            _FONTS_REGISTERED = True
            logger.info(f"Registered Korean font: {reg_path}")
            return "KoreanFont", "KoreanFontBold"
        except Exception as e:
            logger.warning(f"Failed to register font {reg_path}: {e}")
            continue

    logger.warning("No Korean font found — falling back to Helvetica")
    return "Helvetica", "Helvetica-Bold"


# ---------------------------------------------------------------------------
# Custom flowable to track TOC item positions for clickable links
# ---------------------------------------------------------------------------

class _TOCEntry(Flowable):
    """Wrapper around Paragraph that records its drawn position."""

    def __init__(self, paragraph: Paragraph, article_id: str | None = None,
                 target_page: int | None = None):
        Flowable.__init__(self)
        self.paragraph = paragraph
        self.article_id = article_id
        self.target_page = target_page
        self.width = 0
        self.height = 0
        # Filled after draw
        self.drawn_page = None
        self.drawn_y = None

    def wrap(self, availWidth, availHeight):
        w, h = self.paragraph.wrap(availWidth, availHeight)
        self.width = w
        self.height = h
        return w, h

    def draw(self):
        self.paragraph.drawOn(self.canv, 0, 0)

    def split(self, availWidth, availHeight):
        return self.paragraph.split(availWidth, availHeight)


class _TOCDocTemplate(SimpleDocTemplate):
    """DocTemplate that records positions of TOCEntry flowables."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.toc_entries: list[dict] = []

    def afterFlowable(self, flowable):
        if isinstance(flowable, _TOCEntry) and flowable.article_id:
            frame = self.frame
            page_idx = self.page - 1  # 0-indexed
            # frame._y is the current Y cursor (bottom of last drawn item)
            # flowable was just drawn, so its top is at frame._y + flowable.height
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
# Build index PDF
# ---------------------------------------------------------------------------

def _build_index_pdf(
    classification: ClassifiedOutput,
    articles_map: dict[str, ArticleWithContent],
    page_offsets: dict[str, int],
) -> tuple[bytes, list[dict]]:
    """Generate an index/table-of-contents page as PDF bytes.

    Returns (pdf_bytes, toc_link_info) where toc_link_info contains
    position data for each article entry to create clickable links.
    """
    buf = io.BytesIO()
    font_name, font_name_bold = _register_fonts()

    doc = _TOCDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=25 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title_KR", parent=styles["Title"],
        fontName=font_name_bold, fontSize=18, spaceAfter=12,
    )
    h1_style = ParagraphStyle(
        "H1_KR", parent=styles["Heading1"],
        fontName=font_name_bold, fontSize=14, spaceAfter=8, spaceBefore=12,
    )
    h2_style = ParagraphStyle(
        "H2_KR", parent=styles["Heading2"],
        fontName=font_name_bold, fontSize=12, spaceAfter=6, spaceBefore=6,
        leftIndent=10 * mm,
    )
    h3_style = ParagraphStyle(
        "H3_KR", parent=styles["Heading3"],
        fontName=font_name, fontSize=11, spaceAfter=4, spaceBefore=4,
        leftIndent=20 * mm,
    )
    item_style = ParagraphStyle(
        "Item_KR", parent=styles["Normal"],
        fontName=font_name, fontSize=10, spaceAfter=3,
        leftIndent=25 * mm,
    )

    elements: list = []
    elements.append(Paragraph("[더벨] Daily News Clipping", title_style))
    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph("목 차", h1_style))
    elements.append(Spacer(1, 5 * mm))

    def _add_article(aid: str, article_num: int):
        a = articles_map.get(aid)
        if not a:
            return
        target_page = page_offsets.get(aid)
        title_text = xml_escape(a.info.title)
        para = Paragraph(f"({article_num}) {title_text}", item_style)
        entry = _TOCEntry(para, article_id=aid, target_page=target_page)
        elements.append(entry)

    for cat in classification.categories:
        elements.append(Paragraph(xml_escape(cat.name), h1_style))

        # Article numbering restarts from 1 per top-level category
        article_num = 1

        if cat.articles:
            for aid in cat.articles:
                if articles_map.get(aid):
                    _add_article(aid, article_num)
                    article_num += 1

        for sub in cat.subcategories:
            elements.append(Paragraph(xml_escape(sub.name), h2_style))

            if sub.articles:
                for aid in sub.articles:
                    if articles_map.get(aid):
                        _add_article(aid, article_num)
                        article_num += 1

            for sub_item in sub.sub_items:
                elements.append(Paragraph(f"- {xml_escape(sub_item.name)}", h3_style))
                for aid in sub_item.articles:
                    if articles_map.get(aid):
                        _add_article(aid, article_num)
                        article_num += 1

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

    # Pre-read page counts to avoid reading PDFs multiple times
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
    index_pdf_bytes, _ = _build_index_pdf(classification, articles_map, page_offsets)
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
        classification, articles_map, page_offsets_final
    )
    index_reader = PdfReader(io.BytesIO(index_pdf_bytes))

    # Add index pages
    for page in index_reader.pages:
        writer.add_page(page)

    # Add article PDFs with bookmarks
    if on_progress:
        on_progress("PDF 합본 중...")

    # Track article first-page indices in merged PDF (0-indexed)
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
