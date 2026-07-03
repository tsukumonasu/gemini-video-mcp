"""
生成動画の保存。

生成動画を base64 からファイルに保存するユーティリティ。
  - slugify / generate_filename / save_videos
"""

import base64
import os
import re
from datetime import datetime
from typing import Any, Optional

_EXT_BY_MIME = {
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
}


def slugify(text: str, max_length: int = 30) -> str:
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s[:max_length]


def _ext(mime_type: str) -> str:
    return _EXT_BY_MIME.get(mime_type, "mp4")


def generate_filename(
    prompt: str, index: int, mime_type: str, custom_name: Optional[str] = None
) -> str:
    ext = _ext(mime_type)
    timestamp = datetime.now().isoformat().replace(":", "-").replace(".", "-")[:19]
    suffix = f"-{index + 1}" if index > 0 else ""
    if custom_name:
        return f"{slugify(custom_name, 50)}{suffix}.{ext}"
    return f"{slugify(prompt)}-{timestamp}{suffix}.{ext}"


def save_videos(
    videos: list[dict[str, str]],
    prompt: str,
    output_dir: str,
    custom_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    """base64 動画群をファイルに保存し、保存先情報を返す。"""
    os.makedirs(output_dir, exist_ok=True)
    saved: list[dict[str, Any]] = []
    for i, video in enumerate(videos):
        filename = generate_filename(prompt, i, video["mimeType"], custom_name)
        path = os.path.join(output_dir, filename)
        with open(path, "wb") as f:
            f.write(base64.b64decode(video["data"]))
        saved.append({"path": path, "mimeType": video["mimeType"], "index": i})
    return saved
