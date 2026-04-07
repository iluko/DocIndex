"""Build an enriched Markdown document from a PDF by weaving in image analysis.

The enricher extracts raw text per page using pymupdf, then inserts
IMAGE_REF placeholders at the correct page positions so that downstream
PageIndex tree-building operates on text that includes image content.
"""

from __future__ import annotations

from pathlib import Path

from ingestion.image_analyzer import ImageAnalysis


def _build_placeholder(analysis: ImageAnalysis, stored_path: str) -> str:
    """Render one IMAGE_REF block that will be embedded in the markdown.

    The block contains two levels of content:
      summary  — one sentence, for human readability.
      detail   — exhaustive verbatim extraction, for vector search retrieval.
    Both are embedded as plain text (not quoted attributes) so the full content
    is indexed by PageIndex without truncation.
    """
    return (
        f'[IMAGE_REF id="{analysis.img_id}" type="{analysis.image_type}" '
        f'page={analysis.page_num} stored="{stored_path}"]\n\n'
        f'**Image summary:** {analysis.description}\n\n'
        f'**Image detail ({analysis.image_type}):**\n\n'
        f'{analysis.detailed_extraction}\n\n'
        f'**Additional visible text:** {analysis.all_text}\n\n'
        f'[/IMAGE_REF]'
    )


def build_enriched_markdown(
    pdf_path: str,
    analyses: list[ImageAnalysis],
    stored_paths: dict[str, str],
) -> str:
    """Extract PDF text and splice in IMAGE_REF blocks page by page.

    Args:
        pdf_path: Path to the source PDF.
        analyses: Vision-model results, one per extracted image.
        stored_paths: Mapping of img_id → relative stored image path.

    Returns:
        A markdown string suitable for passing to md_to_tree.

    Raises:
        ImportError: If pymupdf is not installed.
    """
    try:
        import fitz  # pymupdf
    except ImportError as exc:
        raise ImportError(
            "pymupdf is required for PDF enrichment. Install it with: pip install pymupdf"
        ) from exc

    # Group analyses by page number for O(1) lookup during page iteration.
    by_page: dict[int, list[ImageAnalysis]] = {}
    for analysis in analyses:
        by_page.setdefault(analysis.page_num, []).append(analysis)

    parts: list[str] = []
    total_pages = 0

    with fitz.open(pdf_path) as doc:
        total_pages = len(doc)
        for page_index, page in enumerate(doc):
            page_num = page_index + 1
            page_images = by_page.get(page_num, [])
            page_text = page.get_text("text").strip()

            # Only emit a page section if there is content (text or images).
            if not page_text and not page_images:
                continue

            # Synthetic heading gives md_to_tree the structure it needs to
            # build a non-empty tree even when the PDF has no heading hierarchy.
            parts.append(f"## Page {page_num}")

            if page_text:
                parts.append(page_text)

            # Append IMAGE_REF blocks for every image on this page.
            for analysis in page_images:
                stored = stored_paths.get(analysis.img_id, "")
                parts.append(_build_placeholder(analysis, stored))

    return "\n\n".join(parts)


def parse_image_refs(text: str) -> list[dict[str, str]]:
    """Extract IMAGE_REF metadata from a block of retrieved node text.

    Used at query time to detect which images are relevant to an answer.

    Returns a list of dicts with keys: id, type, page, stored.
    """
    import re

    pattern = re.compile(
        r'\[IMAGE_REF\s+'
        r'id="(?P<id>[^"]+)"\s+'
        r'type="(?P<type>[^"]+)"\s+'
        r'page=(?P<page>\d+)\s+'
        r'stored="(?P<stored>[^"]*)"\]',
        re.DOTALL,
    )

    return [
        {
            "id": m.group("id"),
            "type": m.group("type"),
            "page": m.group("page"),
            "stored": m.group("stored"),
        }
        for m in pattern.finditer(text)
    ]
