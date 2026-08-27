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

import re as _re

from app.models.schemas import ClassifiedOutput, ArticleWithContent
from app.utils.logging import get_logger
from app.utils.paths import resource_path

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

    # Bundled fonts directory (ships with the app — always available)
    _BUNDLED = resource_path("app", "static", "fonts")

    font_configs = [
        # --- Bundled fonts (guaranteed to exist) ---
        (str(_BUNDLED / "NanumBarunGothic.ttf"),
         str(_BUNDLED / "NanumBarunGothicBold.ttf"), None),
        (str(_BUNDLED / "NanumGothic.ttf"),
         str(_BUNDLED / "NanumGothicBold.ttf"), None),
        # --- System fonts (fallbacks) ---
        # 새굴림 (New Gulim) — Windows-only, may be manually installed
        ("/usr/share/fonts/truetype/gulim/NewGulim.ttf",
         "/usr/share/fonts/truetype/gulim/NewGulim.ttf", None),
        ("/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
         "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf", None),
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

        # Measure page number width
        page_str = str(self.page_num)
        page_w = c.stringWidth(page_str, fn, fs)

        # Reserve space: page number + minimum dot leader area
        dot = "\u00B7"  # middle dot (·)
        dot_unit_w = c.stringWidth(dot + " ", fn, fs)
        min_dots_w = dot_unit_w * 5  # at least 5 dots
        max_title_w = self.width - page_w - min_dots_w - 8  # 8px padding

        # Truncate title if it exceeds max width
        title = self.title
        title_w = c.stringWidth(title, fn, fs)
        if title_w > max_title_w:
            ellipsis = "…"
            while title and c.stringWidth(title + ellipsis, fn, fs) > max_title_w:
                title = title[:-1]
            title = title.rstrip() + ellipsis
            title_w = c.stringWidth(title, fn, fs)

        # Draw title (left-aligned)
        c.drawString(0, 2, title)

        # Draw page number (right-aligned)
        c.drawRightString(self.width, 2, page_str)

        # Fill the gap with dots
        gap_start = title_w + 4
        gap_end = self.width - page_w - 4

        if gap_end > gap_start and dot_unit_w > 0:
            num_dots = int((gap_end - gap_start) / dot_unit_w)
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
    classification: ClassifiedOutput | None = None,
) -> tuple[bytes, list[dict]]:
    """Generate a TOC with category section headers.

    Format:
        콘텐츠
        ─────────────────────────────
        1. Deal
          A. 경영권 인수 및 매각, 투자 유치
        기사 제목 ··················· 페이지
          B. 투자회수
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

    # --- Styles ---
    title_style = ParagraphStyle(
        "TOC_Title",
        fontName=font_name_bold,
        fontSize=16,
        leading=20,
        alignment=1,  # center
        spaceAfter=4 * mm,
    )
    cat_style = ParagraphStyle(
        "TOC_Category",
        fontName=font_name_bold,
        fontSize=12,
        leading=15,
        spaceBefore=4 * mm,
        spaceAfter=1.5 * mm,
    )
    subcat_style = ParagraphStyle(
        "TOC_Subcategory",
        fontName=font_name_bold,
        fontSize=10.5,
        leading=13,
        leftIndent=4 * mm,
        spaceBefore=2 * mm,
        spaceAfter=1 * mm,
        textColor=black,
    )
    subitem_style = ParagraphStyle(
        "TOC_SubItem",
        fontName=font_name_bold,
        fontSize=10,
        leading=12,
        leftIndent=8 * mm,
        spaceBefore=1.5 * mm,
        spaceAfter=0.5 * mm,
        textColor=black,
    )

    articles_map = {a.info.id: a for a in ordered_articles}
    article_font_size = 11

    elements: list = []

    # Title: 콘텐츠
    elements.append(Paragraph("콘텐츠", title_style))

    # Horizontal rule
    elements.append(HRFlowable(
        width="100%", thickness=1, color=black,
        spaceAfter=4 * mm, spaceBefore=2 * mm,
    ))

    def _add_article_entries(article_ids: list[str]):
        """Helper to add TOC lines for a list of article IDs."""
        for aid in article_ids:
            a = articles_map.get(aid)
            if not a:
                continue
            page_num = page_offsets.get(aid, 0)
            entry = _TOCLine(
                title=a.info.title,
                page_num=page_num,
                font_name=font_name,
                font_size=article_font_size,
                article_id=aid,
                target_page=page_num,
            )
            elements.append(entry)
            elements.append(Spacer(1, 1.5 * mm))

    # Build with classification structure if available
    if classification and classification.categories:
        emitted_ids: set[str] = set()

        for cat_idx, cat in enumerate(classification.categories, 1):
            # Category header: "1. Deal"
            elements.append(
                Paragraph(f"{cat_idx}. {xml_escape(cat.name)}", cat_style)
            )

            if cat.subcategories:
                for sub_idx, sub in enumerate(cat.subcategories):
                    sub_letter = chr(ord("A") + sub_idx)
                    # Subcategory header: "A. 경영권 인수 및 매각, 투자 유치"
                    elements.append(
                        Paragraph(
                            f"{sub_letter}. {xml_escape(sub.name)}",
                            subcat_style,
                        )
                    )

                    if sub.sub_items:
                        for si in sub.sub_items:
                            # Sub-item header: "- 환경/폐기물"
                            elements.append(
                                Paragraph(
                                    f"- {xml_escape(si.name)}",
                                    subitem_style,
                                )
                            )
                            _add_article_entries(si.articles)
                            emitted_ids.update(si.articles)
                    if sub.articles:
                        _add_article_entries(sub.articles)
                        emitted_ids.update(sub.articles)
            if cat.articles:
                _add_article_entries(cat.articles)
                emitted_ids.update(cat.articles)
    else:
        # No classification — flat list fallback
        for a in ordered_articles:
            page_num = page_offsets.get(a.info.id, 0)
            entry = _TOCLine(
                title=a.info.title,
                page_num=page_num,
                font_name=font_name,
                font_size=article_font_size,
                article_id=a.info.id,
                target_page=page_num,
            )
            elements.append(entry)
            elements.append(Spacer(1, 1.5 * mm))

    doc.build(elements)
    return buf.getvalue(), doc.toc_entries


# ---------------------------------------------------------------------------
# Copyright-only page detection
# ---------------------------------------------------------------------------

_COPYRIGHT_PATTERNS = [
    _re.compile(r"저작권자.*?thebell", _re.IGNORECASE | _re.DOTALL),
    _re.compile(r"무단\s*전재.*?재배포.*?금지", _re.DOTALL),
    _re.compile(r"AI\s*학습\s*이용\s*금지"),
    _re.compile(r"자본시장\s*미디어"),
    _re.compile(r"thebell", _re.IGNORECASE),
]

# Letters/digits only — punctuation, whitespace and symbols don't count as content.
_MEANINGFUL_CHARS = _re.compile(r"[0-9A-Za-z가-힣ㄱ-ㆎ一-鿿]")

# A page needs at least this many real characters (after the copyright notice is
# removed) to count as content. The copyright line alone yields 0.
_MIN_MEANINGFUL_CHARS = 20

# Safety net against text-extraction failures: a page whose drawing instructions
# are larger than this is treated as real content even if no text was extracted.
_MAX_JUNK_CONTENT_BYTES = 6000

# Images smaller than this (in pixels) are treated as logos/spacers, not content.
_MIN_CONTENT_IMAGE_PIXELS = 10_000  # e.g. 100x100


def _page_has_content_image(page) -> bool:
    """True if the page embeds an image big enough to be real content."""
    try:
        resources = page.get("/Resources")
        if not resources:
            return False
        xobjects = resources.get_object().get("/XObject")
        if not xobjects:
            return False
        xobjects = xobjects.get_object()
        for name in xobjects:
            try:
                obj = xobjects[name].get_object()
                if obj.get("/Subtype") != "/Image":
                    continue
                width = int(obj.get("/Width", 0) or 0)
                height = int(obj.get("/Height", 0) or 0)
                if width * height >= _MIN_CONTENT_IMAGE_PIXELS:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _page_content_bytes(page) -> int:
    """Size of the page's drawing instructions. -1 when it can't be determined."""
    try:
        contents = page.get_contents()
        if contents is None:
            return 0
        data = contents.get_data()
        return len(data) if data else 0
    except Exception:
        return -1


def _page_junk_stats(page) -> tuple[int, int, bool]:
    """Return (meaningful_chars, content_bytes, has_image) for diagnostics."""
    try:
        text = page.extract_text() or ""
    except Exception:
        text = ""

    residual = text
    for pattern in _COPYRIGHT_PATTERNS:
        residual = pattern.sub(" ", residual)

    meaningful = len(_MEANINGFUL_CHARS.findall(residual))
    return meaningful, _page_content_bytes(page), _page_has_content_image(page)


def _is_junk_page(page) -> bool:
    """True for blank pages and for pages carrying only the thebell copyright line.

    thebell's print pages routinely spill a trailing page that holds nothing but
    the copyright notice. Those pages are dropped from both the individual and
    the merged PDF.
    """
    meaningful, content_bytes, has_image = _page_junk_stats(page)

    if meaningful >= _MIN_MEANINGFUL_CHARS:
        return False
    if has_image:
        return False
    # Text extraction can fail on some embedded fonts; if the page still carries
    # a lot of drawing instructions, assume it has content and keep it.
    if content_bytes > _MAX_JUNK_CONTENT_BYTES:
        return False
    return True


def _compress_writer(writer: PdfWriter) -> None:
    """Merge duplicate objects and drop unreferenced ones before writing.

    pypdf copies a page's resources every time `add_page` is called, so merging
    N article PDFs that embed the same Korean web fonts stores those fonts N
    times over. De-duplicating them shrinks the merged file dramatically.
    """
    if not hasattr(writer, "compress_identical_objects"):
        return
    try:
        writer.compress_identical_objects()
    except Exception as e:
        logger.warning(f"PDF 객체 중복 제거 실패 (계속 진행): {type(e).__name__}: {e}")


def _strip_junk_pages(pdf_path: Path) -> tuple[int, int]:
    """Rewrite `pdf_path` without blank / copyright-only pages.

    Returns (kept_pages, dropped_pages). On any failure the file is left
    untouched and its unchanged page count is reported, so the caller's page
    accounting always matches what is actually on disk.
    """
    try:
        reader = PdfReader(str(pdf_path))
        total = len(reader.pages)
        keep = [p for p in reader.pages if not _is_junk_page(p)]

        if not keep:
            # Everything looked like junk — trust the source over the heuristic
            # rather than emitting an empty PDF.
            logger.warning(
                f"전 페이지가 빈 페이지로 판정되어 원본 유지: {pdf_path.name} ({total}p)"
            )
            return total, 0

        if len(keep) == total:
            return total, 0

        writer = PdfWriter()
        for page in keep:
            writer.add_page(page)
        _compress_writer(writer)

        tmp_path = pdf_path.with_name(pdf_path.name + ".tmp")
        with open(tmp_path, "wb") as f:
            writer.write(f)
        tmp_path.replace(pdf_path)

        return len(keep), total - len(keep)

    except Exception as e:
        logger.warning(f"빈 페이지 정리 실패 ({pdf_path.name}): {type(e).__name__}: {e}")
        try:
            return len(PdfReader(str(pdf_path)).pages), 0
        except Exception:
            return 0, 0


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

    # Collect ordered articles following the classification tree structure
    # so that the PDF page order matches the TOC order exactly.
    ordered_articles: list[ArticleWithContent] = []
    seen_ids: set[str] = set()

    def _collect_ids_from_tree(cls: ClassifiedOutput) -> list[str]:
        """Walk the classification tree and return article IDs in TOC order."""
        ids: list[str] = []
        for cat in cls.categories:
            if cat.subcategories:
                for sub in cat.subcategories:
                    if sub.sub_items:
                        for si in sub.sub_items:
                            ids.extend(si.articles)
                    if sub.articles:
                        ids.extend(sub.articles)
            if cat.articles:
                ids.extend(cat.articles)
        return ids

    for aid in _collect_ids_from_tree(classification):
        if aid in seen_ids:
            continue
        a = articles_map.get(aid)
        if a and a.pdf_path and Path(a.pdf_path).exists():
            ordered_articles.append(a)
            seen_ids.add(aid)

    # Add articles we might have missed (not in classification tree)
    for a in articles:
        if a.info.id not in seen_ids and a.pdf_path and Path(a.pdf_path).exists():
            ordered_articles.append(a)

    if not ordered_articles:
        logger.warning("No PDFs to merge")
        return output_path

    # Strip blank / copyright-only pages from each article PDF up front, so the
    # files on disk (which also go into the ZIP) match the page counts used to
    # number the index. Articles left with no pages are dropped entirely — a TOC
    # entry with no destination would render as page 0 and link to the wrong
    # article.
    if on_progress:
        on_progress("빈 페이지 정리 중...")

    article_page_counts: dict[str, int] = {}
    kept_articles: list[ArticleWithContent] = []
    total_dropped = 0

    for a in ordered_articles:
        kept, dropped = _strip_junk_pages(Path(a.pdf_path))
        total_dropped += dropped
        if dropped:
            logger.info(
                f"빈 페이지 {dropped}개 제거: {a.info.title[:40]} ({kept + dropped}p → {kept}p)"
            )
        if kept <= 0:
            logger.warning(f"페이지가 없어 합본에서 제외: {a.info.title[:40]}")
            continue
        article_page_counts[a.info.id] = kept
        kept_articles.append(a)

    ordered_articles = kept_articles
    if not ordered_articles:
        logger.warning("No PDFs to merge after cleanup")
        return output_path

    logger.info(
        f"빈 페이지 정리 완료 | 제거={total_dropped}개 | 기사={len(ordered_articles)}개"
    )
    if on_progress and total_dropped:
        on_progress(f"빈 페이지 {total_dropped}개 제거됨")

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
    index_pdf_bytes, _ = _build_index_pdf(
        ordered_articles, page_offsets, classification,
    )
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
        ordered_articles, page_offsets_final, classification,
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

            # Junk pages were already stripped from the file above, so every
            # page here is counted in article_page_counts / page_offsets_final.
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

    # De-duplicate the fonts/images that every thebell page embeds before
    # writing — without this the merged file carries one copy per page.
    if on_progress:
        on_progress("PDF 용량 최적화 중...")
    _compress_writer(writer)

    # Write merged PDF
    with open(output_path, "wb") as f:
        writer.write(f)

    size_mb = output_path.stat().st_size / 1_000_000 if output_path.exists() else 0
    logger.info(
        f"PDF 합본 완료 | 기사={len(ordered_articles)}개 | "
        f"페이지={len(writer.pages)}p | 용량={size_mb:.1f}MB"
    )
    if on_progress:
        on_progress(
            f"PDF 합본 완료: {len(ordered_articles)}개 기사, "
            f"{len(writer.pages)}페이지 ({size_mb:.1f}MB)"
        )

    return output_path
