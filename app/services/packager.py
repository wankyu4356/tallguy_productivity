import zipfile
from pathlib import Path

from app.models.schemas import ArticleWithContent
from app.utils.logging import get_logger

logger = get_logger(__name__)


def create_zip(
    individual_pdfs: list[ArticleWithContent],
    merged_pdf_path: Path,
    docx_path: Path,
    output_path: Path,
    date_str: str,
) -> Path:
    """Create a ZIP file containing all outputs.

    Structure:
    - individual/ - individual article PDFs
    - (더벨) Daily News Clipping YYYY.MM.DD.pdf
    - (더벨) Daily News Clipping YYYY.MM.DD.docx
    """
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add individual PDFs
        for article in individual_pdfs:
            if article.pdf_path and Path(article.pdf_path).exists():
                pdf_file = Path(article.pdf_path)
                zf.write(pdf_file, f"individual/{pdf_file.name}")

        # Add merged PDF
        if merged_pdf_path.exists():
            merged_name = f"(더벨) Daily News Clipping {date_str}.pdf"
            zf.write(merged_pdf_path, merged_name)

        # Add DOCX
        if docx_path.exists():
            docx_name = f"(더벨) Daily News Clipping {date_str}.docx"
            zf.write(docx_path, docx_name)

    logger.info(f"ZIP created: {output_path}")
    return output_path
