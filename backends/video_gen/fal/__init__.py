"""fal.ai text-to-video backend for Hermes's video_gen framework.

Uses fal's REST *queue* API directly via httpx (no fal_client dependency):
  POST https://queue.fal.run/<model>      -> {request_id, status_url, response_url}
  GET  <status_url>                        -> {status: IN_QUEUE|IN_PROGRESS|COMPLETED}
  GET  <response_url>                       -> {video: {url: ...}}

Auth: ``FAL_KEY`` env var (free trial credits at https://fal.ai/dashboard/keys).
Enable with:  hermes plugins enable video_gen/fal ; image_gen-style config:
  video_gen.provider: fal

⚠️ UNVERIFIED: written against fal's documented queue API but not yet run end
to end (no key was available at authoring time). Verify with a real FAL_KEY.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from agent.video_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_RESOLUTION,
    VideoGenProvider,
    error_response,
    save_bytes_video,
    success_response,
)

logger = logging.getLogger(__name__)

_QUEUE_BASE = "https://queue.fal.run"
# Model families on fal. Default to LTX (fast/cheap text-to-video). Override
# with video_gen.fal.model in config.yaml or FAL_VIDEO_MODEL env.
_MODELS = {
    "ltx": "fal-ai/ltx-video",
    "kling": "fal-ai/kling-video/v1/standard/text-to-video",
    "wan": "fal-ai/wan/v2.2-5b/text-to-video",
}
DEFAULT_MODEL = "ltx"
_POLL_INTERVAL = 3.0
_MAX_WAIT = 300.0


def _fal_key() -> str:
    return (os.environ.get("FAL_KEY") or "").strip()


def _resolve_model(model: Optional[str]) -> str:
    name = (model or os.environ.get("FAL_VIDEO_MODEL") or DEFAULT_MODEL).strip()
    return _MODELS.get(name, name if "/" in name else _MODELS[DEFAULT_MODEL])


class FalVideoGenProvider(VideoGenProvider):
    @property
    def name(self) -> str:
        return "fal"

    @property
    def display_name(self) -> str:
        return "fal Video"

    def is_available(self) -> bool:
        if not _fal_key():
            return False
        try:
            import httpx  # noqa: F401
            return True
        except ImportError:
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"id": k, "display": f"fal {k}", "strengths": v} for k, v in _MODELS.items()]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "fal Video",
            "badge": "trial",
            "tag": "text-to-video via fal — free trial credits",
            "env_vars": ["FAL_KEY"],
            "post_setup_hint": "Get a key at https://fal.ai/dashboard/keys",
        }

    def capabilities(self) -> Dict[str, Any]:
        return {"modalities": ["text", "image"], "max_reference_images": 1}

    def generate(self, prompt: str, *, model: Optional[str] = None,
                 image_url: Optional[str] = None,
                 reference_image_urls: Optional[List[str]] = None,
                 duration: Optional[int] = None,
                 aspect_ratio: str = DEFAULT_ASPECT_RATIO,
                 resolution: str = DEFAULT_RESOLUTION,
                 negative_prompt: Optional[str] = None,
                 audio: Optional[bool] = None,
                 seed: Optional[int] = None,
                 **kwargs: Any) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        if not prompt:
            return error_response(error="Prompt is required", error_type="invalid_argument",
                                  provider="fal", aspect_ratio=aspect_ratio)
        key = _fal_key()
        if not key:
            return error_response(error="FAL_KEY not set — add it in the CLI Matrix Media panel.",
                                  error_type="auth_required", provider="fal", aspect_ratio=aspect_ratio)
        try:
            import httpx
        except ImportError:
            return error_response(error="httpx not installed", error_type="missing_dependency",
                                  provider="fal", aspect_ratio=aspect_ratio)

        model_id = _resolve_model(model)
        headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
        args: Dict[str, Any] = {"prompt": prompt}
        if negative_prompt:
            args["negative_prompt"] = negative_prompt
        if seed is not None:
            args["seed"] = seed
        if image_url:
            args["image_url"] = image_url

        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, read=60.0)) as http:
                sub = http.post(f"{_QUEUE_BASE}/{model_id}", headers=headers, json=args)
                sub.raise_for_status()
                job = sub.json()
                status_url = job.get("status_url")
                response_url = job.get("response_url")
                if not status_url or not response_url:
                    return error_response(error=f"Unexpected fal submit response: {job}",
                                          error_type="api_error", provider="fal",
                                          aspect_ratio=aspect_ratio)
                deadline = time.time() + _MAX_WAIT
                while time.time() < deadline:
                    st = http.get(status_url, headers=headers).json()
                    if st.get("status") == "COMPLETED":
                        break
                    time.sleep(_POLL_INTERVAL)
                else:
                    return error_response(error="fal video timed out", error_type="timeout",
                                          provider="fal", aspect_ratio=aspect_ratio)
                result = http.get(response_url, headers=headers).json()
                video = result.get("video") or {}
                video_url = video.get("url") if isinstance(video, dict) else None
                if not video_url:
                    return error_response(error=f"No video URL in fal result: {result}",
                                          error_type="empty_response", provider="fal",
                                          aspect_ratio=aspect_ratio)
                data = http.get(video_url, follow_redirects=True).content
        except Exception as exc:
            logger.debug("fal video generation failed", exc_info=True)
            return error_response(error=f"fal video request failed: {exc}",
                                  error_type="api_error", provider="fal", aspect_ratio=aspect_ratio)

        saved = save_bytes_video(data, prefix="fal", extension="mp4")
        return success_response(video=str(saved), model=model_id, prompt=prompt,
                                modality="image" if image_url else "text",
                                aspect_ratio=aspect_ratio, provider="fal")


def register(ctx) -> None:
    ctx.register_video_gen_provider(FalVideoGenProvider())
