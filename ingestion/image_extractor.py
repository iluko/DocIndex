"""Extract embedded images from PDF files using pymupdf.

Each extracted image is filtered by a minimum bounding-box threshold so that
decorative assets (logos, dividers, background fills) are skipped before any
expensive vision-model calls happen.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# Images smaller than this in either dimension are considered decorative.
_MIN_DIMENSION_PX = 80


@dataclass
class ExtractedImage:
    """One image pulled from a PDF, ready for analysis."""

    img_id: str
    """Stable identifier: {doc_id}__p{page_num}_i{index}."""
    page_num: int
    """1-based page number where the image appears."""
    data: bytes
    """Raw image bytes (native format from the PDF)."""
    mime_type: str
    """MIME type inferred from the image stream (image/png or image/jpeg)."""
    width: int
    height: int


def extract_images_from_pdf(file_path: str, doc_id: str) -> list[ExtractedImage]:
    """Return all content-bearing images embedded in a PDF.

    Args:
        file_path: Absolute path to the PDF file.
        doc_id: The logical document identifier used to build stable img_ids.

    Returns:
        A list of ExtractedImage objects, ordered by page then by position.

    Raises:
        ImportError: If pymupdf is not installed.
        FileNotFoundError: If the PDF does not exist.
    """
    try:
        import fitz  # pymupdf
    except ImportError as exc:
        raise ImportError(
            "pymupdf is required for image extraction. Install it with: pip install pymupdf"
        ) from exc

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    results: list[ExtractedImage] = []

    with fitz.open(str(path)) as doc:
        for page_index, page in enumerate(doc):
            page_num = page_index + 1
            image_list = page.get_images(full=True)

            for img_index, img_info in enumerate(image_list):
                xref = img_info[0]

                try:
                    base_image = doc.extract_image(xref)
                except Exception:
                    continue

                width = base_image.get("width", 0)
                height = base_image.get("height", 0)

                if width < _MIN_DIMENSION_PX or height < _MIN_DIMENSION_PX:
                    continue

                raw_ext = base_image.get("ext", "png").lower()
                mime_type = "image/jpeg" if raw_ext in ("jpg", "jpeg") else "image/png"

                img_id = f"{doc_id}__p{page_num}_i{img_index}"

                results.append(
                    ExtractedImage(
                        img_id=img_id,
                        page_num=page_num,
                        data=base_image["image"],
                        mime_type=mime_type,
                        width=width,
                        height=height,
                    )
                )

    return results
