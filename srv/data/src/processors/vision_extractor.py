"""
Vision-based page analysis using multimodal LLM.

Renders PDF pages to images and sends them to a vision-language model
(qwen3.5-35b-multi on prod, qwen3.5-4b on dev) via LiteLLM to:
- Describe images, charts, graphs, and diagrams
- Extract data from charts as markdown tables
- Extract/reconstruct garbled tables as properly formatted markdown
- OCR pages where Tesseract fails
"""

import base64
import io
import os
from dataclasses import dataclass
from typing import Optional

import httpx
import structlog

from busibox_common.llm import get_registry

logger = structlog.get_logger()


DESCRIBE_IMAGE_PROMPT = (
    "Describe this page image in detail. Include all visible text, labels, "
    "annotations, headings, and any data. If there are images or photographs, "
    "describe them. Output as clean markdown."
)

EXTRACT_CHART_PROMPT = (
    "This page contains a chart or graph. Please extract:\n"
    "1. The chart type (bar, line, pie, scatter, etc.)\n"
    "2. The title and axis labels\n"
    "3. All data points as a properly formatted markdown table\n"
    "4. Key trends or findings\n\n"
    "Format as markdown with the data table first, then the description."
)

EXTRACT_TABLE_PROMPT = (
    "This page contains a table. Reproduce it as a properly formatted markdown "
    "table with correct headers, a separator row (|---|---|), and aligned columns. "
    "Include all rows and columns visible in the image. Output only the markdown "
    "table, no additional commentary."
)

OCR_PAGE_PROMPT = (
    "Extract all text from this page image. Preserve the document structure: "
    "headings, paragraphs, lists, and any tables. Output as clean markdown."
)


@dataclass
class VisionResult:
    """Result from vision model analysis."""
    text: str
    mode: str
    success: bool
    error: Optional[str] = None


class VisionExtractor:
    """
    Calls the vision-language model via LiteLLM to analyse page images.

    Uses purpose="vision" from the model registry (qwen3.5-35b-multi on prod,
    qwen3.5-4b on dev). All Qwen3.5 models are natively multimodal.
    """

    def __init__(self, config: dict):
        self.config = config

        self.litellm_base_url = (
            config.get("litellm_base_url")
            or os.getenv("LITELLM_BASE_URL")
            or "http://10.96.200.207:4000"
        )
        self.litellm_api_key = (
            config.get("litellm_api_key")
            or os.getenv("LITELLM_API_KEY")
            or os.getenv("LITELLM_MASTER_KEY")
            or ""
        )

        registry = get_registry()
        try:
            self.model_config = registry.get_config("vision")
            self.model = "vision"
            logger.info(
                "VisionExtractor initialized",
                litellm_model=self.model,
                underlying_model=self.model_config.get("model"),
                base_url=self.litellm_base_url,
            )
        except (ValueError, KeyError):
            self.model = "vision"
            self.model_config = {"temperature": 0.2, "max_tokens": 4096}
            logger.warning("Vision model not in registry, using fallback config")

    def render_page_to_base64(
        self, file_path: str, page_number: int, dpi: int = 200,
    ) -> Optional[str]:
        """Render a single PDF page to a base64-encoded PNG string."""
        try:
            from pdf2image import convert_from_path

            images = convert_from_path(
                file_path, dpi=dpi,
                first_page=page_number, last_page=page_number,
            )
            if not images:
                return None

            buf = io.BytesIO()
            images[0].save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            logger.warning(
                "Failed to render page to image",
                file_path=file_path, page=page_number, error=str(e),
            )
            return None

    async def analyse_page(
        self,
        file_path: str,
        page_number: int,
        mode: str = "describe",
        existing_text: str = "",
    ) -> VisionResult:
        """
        Render a page and send to the vision model.

        Args:
            file_path: Path to the PDF
            page_number: 1-based page number
            mode: One of "describe", "chart", "table", "ocr"
            existing_text: Current extracted text (for context)

        Returns:
            VisionResult with the model's response
        """
        image_b64 = self.render_page_to_base64(file_path, page_number)
        if not image_b64:
            return VisionResult(
                text="", mode=mode, success=False,
                error="Failed to render page image",
            )

        prompt_map = {
            "describe": DESCRIBE_IMAGE_PROMPT,
            "chart": EXTRACT_CHART_PROMPT,
            "table": EXTRACT_TABLE_PROMPT,
            "ocr": OCR_PAGE_PROMPT,
        }
        system_prompt = prompt_map.get(mode, DESCRIBE_IMAGE_PROMPT)

        user_content: list = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{image_b64}",
                },
            },
        ]
        if existing_text:
            user_content.append({
                "type": "text",
                "text": (
                    f"Current extracted text for this page (may be incomplete or garbled):\n"
                    f"---\n{existing_text[:2000]}\n---\n"
                    f"Please provide improved content."
                ),
            })

        headers = {"Content-Type": "application/json"}
        if self.litellm_api_key:
            headers["Authorization"] = f"Bearer {self.litellm_api_key}"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.litellm_base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "temperature": self.model_config.get("temperature", 0.2),
                        "max_tokens": self.model_config.get("max_tokens", 4096),
                    },
                )

                if response.status_code != 200:
                    logger.error(
                        "Vision model call failed",
                        status=response.status_code,
                        response=response.text[:300],
                        page=page_number,
                    )
                    return VisionResult(
                        text="", mode=mode, success=False,
                        error=f"HTTP {response.status_code}",
                    )

                result = response.json()
                text = result["choices"][0]["message"]["content"].strip()

                logger.info(
                    "Vision analysis complete",
                    page=page_number,
                    mode=mode,
                    output_length=len(text),
                )
                return VisionResult(text=text, mode=mode, success=True)

        except Exception as e:
            logger.error(
                "Vision model call failed",
                page=page_number, mode=mode, error=str(e),
            )
            return VisionResult(
                text="", mode=mode, success=False, error=str(e),
            )
