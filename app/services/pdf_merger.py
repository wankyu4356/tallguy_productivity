from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

from app.models.schemas import ClassifiedOutput, ArticleWithContent
from app.utils.logging import get_logger

logger = get_logger(__name__)


def _register_fonts():
    """Register Korean fonts for PDF generation."""
    font_paths = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]

    for fp in font_paths:
        if Path(fp).exists():
            try:
                pdfmetrics.registerFont(TTFont("KoreanFont", fp))
                return "KoreanFont"
            except Exception:
                continue

    # Fallback to Helvetica
    return "Helvetica"


def _build_index_pdf(
    classification: ClassifiedOutput,
    articles_map: dict[str, ArticleWithContent],
    page_offsets: dict[str, int],
) -> bytes:
    """Generate an index/table-of-contents page as PDF bytes."""
    buf = io.BytesIO()
    font_name = _register_fonts()

    doc = SimpleDocTemplate(
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
        fontName=font_name, fontSize=18, spaceAfter=12,
    )
    h1_style = ParagraphStyle(
        "H1_KR", parent=styles["Heading1"],
        fontName=font_name, fontSize=14, spaceAfter=8, spaceBefore=12,
    )
    h2_style = ParagraphStyle(
        "H2_KR", parent=styles["Heading2"],
        fontName=font_name, fontSize=12, spaceAfter=6, spaceBefore=6,
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

    elements = []
    elements.append(Paragraph("[더벨] Daily News Clipping", title_style))
    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph("목 차", h1_style))
    elements.append(Spacer(1, 5 * mm))

    for cat in classification.categories:
        elements.append(Paragraph(cat.name, h1_style))

        if cat.articles:
            for aid in cat.articles:
                a = articles_map.get(aid)
                if a:
                    page_num = page_offsets.get(aid, "?")
                    elements.append(Paragraph(
                        f"({page_num}) {a.info.title}", item_style
                    ))

        for sub in cat.subcategories:
            elements.append(Paragraph(sub.name, h2_style))

            if sub.articles:
                for aid in sub.articles:
                    a = articles_map.get(aid)
                    if a:
                        page_num = page_offsets.get(aid, "?")
                        elements.append(Paragraph(
                            f"({page_num}) {a.info.title}", item_style
                        ))

            for sub_item in sub.sub_items:
                elements.append(Paragraph(f"- {sub_item.name}", h3_style))
                for aid in sub_item.articles:
                    a = articles_map.get(aid)
                    if a:
                        page_num = page_offsets.get(aid, "?")
                        elements.append(Paragraph(
                            f"({page_num}) {a.info.title}", item_style
                        ))

    doc.build(elements)
    return buf.getvalue()


def merge_pdfs(
    classification: ClassifiedOutput,
    articles: list[ArticleWithContent],
    output_path: Path,
    on_progress: callable | None = None,
) -> Path:
    """Merge individual article PDFs into a single file with index and bookmarks."""
    articles_map = {a.info.id: a for a in articles if a.pdf_path}
    writer = PdfWriter()

    # First pass: determine page offsets for each article
    ordered_articles = []
    for aid in classification.article_order:
        a = articles_map.get(aid)
        if a and a.pdf_path and Path(a.pdf_path).exists():
            ordered_articles.append(a)

    # Add articles we might have missed
    for a in articles:
        if a.info.id not in {oa.info.id for oa in ordered_articles} and a.pdf_path and Path(a.pdf_path).exists():
            ordered_articles.append(a)

    if not ordered_articles:
        logger.warning("No PDFs to merge")
        return output_path

    # Calculate page offsets (index page will be first)
    page_offsets = {}
    current_page = 1  # Will be updated after index generation

    # Placeholder: we'll update after knowing index page count
    for a in ordered_articles:
        try:
            reader = PdfReader(a.pdf_path)
            page_offsets[a.info.id] = current_page
            current_page += len(reader.pages)
        except Exception as e:
            logger.error(f"Error reading PDF {a.pdf_path}: {e}")

    # Generate index PDF
    if on_progress:
        on_progress("인덱스 페이지 생성 중...")
    index_pdf_bytes = _build_index_pdf(classification, articles_map, page_offsets)
    index_reader = PdfReader(io.BytesIO(index_pdf_bytes))
    index_page_count = len(index_reader.pages)

    # Recalculate offsets with index pages
    page_offsets_final = {}
    current_page = index_page_count + 1
    for a in ordered_articles:
        try:
            reader = PdfReader(a.pdf_path)
            page_offsets_final[a.info.id] = current_page
            current_page += len(reader.pages)
        except Exception:
            pass

    # Regenerate index with correct page numbers
    index_pdf_bytes = _build_index_pdf(classification, articles_map, page_offsets_final)
    index_reader = PdfReader(io.BytesIO(index_pdf_bytes))

    # Add index pages
    for page in index_reader.pages:
        writer.add_page(page)

    # Add article PDFs with bookmarks
    if on_progress:
        on_progress("PDF 합본 중...")

    for i, a in enumerate(ordered_articles):
        try:
            reader = PdfReader(a.pdf_path)
            first_page_idx = len(writer.pages)

            for page in reader.pages:
                writer.add_page(page)

            # Add bookmark for this article
            writer.add_outline_item(a.info.title, first_page_idx)

            if on_progress:
                on_progress(f"PDF 합본: {i + 1}/{len(ordered_articles)}")
        except Exception as e:
            logger.error(f"Error merging PDF for '{a.info.title}': {e}")

    # Write merged PDF
    with open(output_path, "wb") as f:
        writer.write(f)

    if on_progress:
        on_progress(f"PDF 합본 완료: {len(ordered_articles)}개 기사")

    return output_path
