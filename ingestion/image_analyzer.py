"""Analyze extracted PDF images using GPT-5.4 on Azure AI Foundry.

Each image is sent individually with a type-aware prompt that produces two
levels of output:

  SUMMARY  — one concise sentence for human readability.
  DETAIL   — exhaustive, verbatim extraction tailored to the image type.
             This is what makes the image content retrievable by vector search,
             so it must be as complete and literal as possible.

The result becomes the placeholder text embedded into the enriched document
before PageIndex builds its tree.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from ingestion.image_extractor import ExtractedImage
from utils import get_sync_client

logger = logging.getLogger(__name__)

_ANALYSIS_PROMPT = """\
Analyze this image extracted from a technical document.

Step 1 — Identify the image type. Choose exactly one:
table | flowchart | architecture_diagram | chart | code_screenshot | ui_screenshot | photograph | equation | other

Step 2 — Write a SUMMARY: one concise sentence describing what this image shows.

Step 3 — Write a DETAIL section. This must be exhaustive and verbatim — it is
used for search indexing so completeness is critical. Follow the rules for the
identified type:

  table:
    Reproduce the FULL table as a markdown table. Include every row and every
    column. Do not truncate, summarise, or omit any cell.

  flowchart:
    List every node/step with its exact label, then list every directional
    connection as "A → B". Include all branch conditions.

  architecture_diagram:
    List every component with its exact label and role. Then list every
    connection as "A → B (label if any)". Include all annotations.

  chart:
    State the chart type, axis labels with units, every data series name and
    its values, all legend entries, and the primary trend or conclusion shown.

  code_screenshot:
    Reproduce the COMPLETE visible code exactly as written, character for
    character, preserving indentation. Include every line number if visible.
    After the code block, list any visible UI elements (file tabs, editor name,
    terminal output, error messages) verbatim.

  ui_screenshot:
    Describe every visible panel, toolbar, and section by name. Extract every
    visible label, button text, menu item, form field name, and its value.
    For any visible JSON, code, or structured data reproduce it verbatim.
    Describe the overall workflow or state the UI is showing.

  photograph:
    Describe the subject, setting, and every relevant visible detail.

  equation:
    Write the equation in plain text and LaTeX. Describe every variable and
    what the equation computes.

  other:
    Extract every piece of visible text verbatim. Describe all visual elements
    as specifically as possible.

Step 4 — TEXT: list any remaining visible text not already captured above, or write "none".

Respond in this EXACT format (do not add extra headings or commentary):
TYPE: <type>
SUMMARY: <one sentence>
DETAIL:
<exhaustive extraction from Step 3>
TEXT: <remaining text or "none">"""


@dataclass
class ImageAnalysis:
    """Structured result from one GPT-5.4 vision call."""

    img_id: str
    page_num: int
    image_type: str
    description: str          # concise one-sentence summary
    detailed_extraction: str  # exhaustive verbatim extraction for retrieval
    all_text: str             # any remaining visible text


def analyze_images(
    images: list[ExtractedImage],
    model: str,
    doc_id: str,
    doc_type: str,
) -> list[ImageAnalysis]:
    """Call GPT-5.4 on each image and return structured analysis.

    Args:
        images: Extracted images from the PDF.
        model: The deployment name for GPT-5.4 on Azure AI Foundry.
        doc_id: Used for context in the system prompt.
        doc_type: Used for context in the system prompt.

    Returns:
        One ImageAnalysis per input image, in the same order.
    """
    if not images:
        return []

    client = get_sync_client()
    results: list[ImageAnalysis] = []

    for img in images:
        b64 = base64.b64encode(img.data).decode("utf-8")
        data_url = f"data:{img.mime_type};base64,{b64}"

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"You are analyzing images extracted from a '{doc_type}' document "
                            f"(doc_id: {doc_id}). Be precise and exhaustive — every piece of "
                            "text and structured content you extract will be used for search "
                            "indexing, so completeness and verbatim accuracy are critical."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            },
                            {
                                "type": "text",
                                "text": _ANALYSIS_PROMPT,
                            },
                        ],
                    },
                ],
                max_completion_tokens=10000,
            )
            raw = response.choices[0].message.content or ""
            analysis = _parse_response(raw, img)
        except Exception as exc:
            logger.warning("Image analysis failed for %s: %s", img.img_id, exc)
            analysis = _fallback_analysis(img)

        results.append(analysis)

    return results


def _parse_response(raw: str, img: ExtractedImage) -> ImageAnalysis:
    """Parse the structured GPT-5.4 response into an ImageAnalysis."""
    lines = raw.strip().splitlines()

    image_type = "other"
    description = ""
    detail_lines: list[str] = []
    all_text = "none"

    section = None
    for line in lines:
        if line.startswith("TYPE:"):
            image_type = line.removeprefix("TYPE:").strip().lower()
            section = None
        elif line.startswith("SUMMARY:"):
            description = line.removeprefix("SUMMARY:").strip()
            section = None
        elif line.startswith("DETAIL:"):
            section = "detail"
        elif line.startswith("TEXT:"):
            all_text = line.removeprefix("TEXT:").strip()
            section = None
        elif section == "detail":
            detail_lines.append(line)

    return ImageAnalysis(
        img_id=img.img_id,
        page_num=img.page_num,
        image_type=image_type,
        description=description or f"Image on page {img.page_num}",
        detailed_extraction="\n".join(detail_lines).strip(),
        all_text=all_text,
    )


def _fallback_analysis(img: ExtractedImage) -> ImageAnalysis:
    """Return a minimal placeholder when the vision call fails."""
    return ImageAnalysis(
        img_id=img.img_id,
        page_num=img.page_num,
        image_type="other",
        description=f"Image on page {img.page_num} (analysis unavailable)",
        detailed_extraction="",
        all_text="none",
    )
