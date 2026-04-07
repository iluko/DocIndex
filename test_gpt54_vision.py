"""Quick smoke test: verify GPT-5.4 on Azure AI Foundry accepts image input.

Usage:
    python test_gpt54_vision.py <deployment-name> <image-path>

Examples:
    python test_gpt54_vision.py gpt-5.4 ./sample.jpg
    python test_gpt54_vision.py gpt-5.4 ./diagram.png

The script uses your existing Azure config from .env (same client as the app).
It reads a normal local JPEG or PNG file, base64-encodes it, and asks the model
to describe it.
"""

import base64
import sys
import mimetypes
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

def _load_image_as_data_url(image_path: str) -> str:
    path = Path(image_path)

    if not path.is_file():
        print(f"Image file not found: {image_path}")
        sys.exit(1)

    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type not in {"image/jpeg", "image/png"}:
        print("Only JPEG and PNG files are supported.")
        sys.exit(1)

    image_bytes = path.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{image_b64}"

def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python test_gpt54_vision.py <deployment-name> <image-path>")
        sys.exit(1)

    deployment = sys.argv[1]
    image_path = sys.argv[2]
    image_data_url = _load_image_as_data_url(image_path)

    from utils import get_sync_client

    client = get_sync_client()

    print(f"Sending test image to deployment: {deployment}")
    print(f"Image: {image_path}")
    print("Endpoint:", client.base_url)

    response = client.chat.completions.create(
        model=deployment,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data_url,
                        },
                    },
                    {
                        "type": "text",
                        "text": """Analyze this image extracted from a technical document. 

Step 1 — Identify the image type:
table | flowchart | architecture_diagram | chart | screenshot | photograph | equation | other

Step 2 — Based on the type, extract:
- table: reconstruct as full markdown table with all rows and columns
- flowchart: list all nodes/steps and their directional connections
- architecture_diagram: list components, their roles, and how they connect
- chart: chart type, axes labels, all data series, notable values, and the key trend
- screenshot: describe the UI/content shown, extract all visible text
- photograph: describe subject, context, and any relevant detail
- equation: describe what it represents mathematically and what it calculates

Step 3 — Any visible text not captured above (supplements OCR).""",
                    },
                ],
            }
        ],
        max_completion_tokens=10000,
    )

    answer = response.choices[0].message.content
    print(f"\nModel response: {answer}")
    print("\nVision API: OK")


if __name__ == "__main__":
    main()
