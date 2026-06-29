"""Pollinations.ai image-generation backend — free, no API key.

Text-to-image via the keyless Pollinations endpoint
(``https://image.pollinations.ai/prompt/<prompt>``). Saves PNG/JPEG into the
Hermes image cache via the standard ImageGenProvider helpers.

Enable with:  image_gen.provider: pollinations  (in config.yaml)
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    success_response,
)

logger = logging.getLogger(__name__)

_SIZES = {"landscape": (1280, 720), "square": (1024, 1024), "portrait": (720, 1280)}
_BASE = "https://image.pollinations.ai/prompt/"


class PollinationsImageGenProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "pollinations"

    @property
    def display_name(self) -> str:
        return "Pollinations (free)"

    def is_available(self) -> bool:
        try:
            import httpx  # noqa: F401
            return True
        except ImportError:
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"id": "flux", "display": "Pollinations Flux", "speed": "~10s",
                 "strengths": "Free, no API key", "price": "free"}]

    def default_model(self) -> Optional[str]:
        return "flux"

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Pollinations (free)",
            "badge": "free",
            "tag": "Free text-to-image, no API key required",
            "env_vars": [],
            "post_setup_hint": "No key needed. Set image_gen.provider: pollinations",
        }

    def capabilities(self) -> Dict[str, Any]:
        return {"modalities": ["text"], "max_reference_images": 0}

    def generate(self, prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO,
                 *, image_url: Optional[str] = None,
                 reference_image_urls: Optional[List[str]] = None,
                 **kwargs: Any) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        if not prompt:
            return error_response(error="Prompt is required", error_type="invalid_argument",
                                  provider="pollinations", aspect_ratio=aspect)
        try:
            import httpx
        except ImportError:
            return error_response(error="httpx not installed", error_type="missing_dependency",
                                  provider="pollinations", aspect_ratio=aspect)

        w, h = _SIZES.get(aspect, _SIZES["square"])
        url = f"{_BASE}{quote(prompt)}?width={w}&height={h}&nologo=true"
        try:
            with httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0)) as http:
                r = http.get(url, follow_redirects=True)
                r.raise_for_status()
                data = r.content
            if not data or len(data) < 256:
                return error_response(error="Pollinations returned empty image",
                                      error_type="empty_response", provider="pollinations",
                                      aspect_ratio=aspect)
            b64 = base64.b64encode(data).decode("ascii")
            saved = save_b64_image(b64, prefix="pollinations")
        except Exception as exc:
            logger.debug("Pollinations generation failed", exc_info=True)
            return error_response(error=f"Pollinations request failed: {exc}",
                                  error_type="api_error", provider="pollinations",
                                  aspect_ratio=aspect)

        return success_response(image=str(saved), model="flux", prompt=prompt,
                                aspect_ratio=aspect, provider="pollinations",
                                extra={"size": f"{w}x{h}"})


def register(ctx) -> None:
    ctx.register_image_gen_provider(PollinationsImageGenProvider())
