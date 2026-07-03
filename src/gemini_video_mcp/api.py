"""
Gemini Omni Flash 動画生成 API クライアント（interactions エンドポイント）。

認証は auth.py の OAuth access token を使用（Authorization: Bearer）。
出所: https://ai.google.dev/gemini-api/docs/omni

リクエスト形式（interactions）:
  {
    "model": "gemini-omni-flash-preview",
    "input": "テキスト" もしくは
             [{"type":"image","data":"<base64>","mime_type":"image/jpeg"},
              {"type":"text","text":"..."}],
    "response_format": {"type": "video", "aspect_ratio": "16:9"},   # 任意
    "generation_config": {"video_config": {"task": "image_to_video"}},  # 任意
    "previous_interaction_id": "v1_..."   # ステートフル編集時（任意）
  }

レスポンス（未加工 REST）:
  {
    "steps": [
      {"type": "user_input", ...},
      {"type": "thought", ...},
      {"type": "model_output",
       "content": [{"type": "video", "mime_type": "video/mp4", "data": "<base64>"}]}
    ],
    "id": "v1_...",
    "status": "completed",
    "model": "gemini-omni-flash-preview",
    "object": "interaction"
  }
  ※ SDK 専用の便利フィールド interaction.output_video は REST には無いため、
    steps 配列から動画出力を取り出す。
"""

import base64
import logging
import mimetypes
import os
from typing import Any, Optional

import httpx

from .constants import build_interactions_url

logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    def __init__(self, message: str, retry_after_ms: int = 60000) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


class CapacityError(Exception):
    def __init__(self, message: str, retry_after_ms: int = 60000) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


def image_to_input_part(image_path: str) -> dict[str, Any]:
    """入力画像を interactions の input パート（image）に変換する。

    出所のスキーマ: {"type": "image", "data": "<base64>", "mime_type": "image/jpeg"}
    """
    abs_path = os.path.abspath(os.path.expanduser(image_path))
    if not os.path.exists(abs_path):
        raise ValueError(f"入力画像が見つかりません: {abs_path}")
    with open(abs_path, "rb") as f:
        raw = f.read()
    mime = mimetypes.guess_type(abs_path)[0] or "image/png"
    return {"type": "image", "data": base64.b64encode(raw).decode(), "mime_type": mime}


def build_input(
    prompt: str,
    input_images: Optional[list[str]] = None,
) -> Any:
    """interactions の input を構築する。

    - 画像が無ければ文字列（テキストのみ）を返す。
    - 画像があれば [image..., {"type":"text","text":prompt}] のパート配列を返す
      （画像→動画・被写体参照。複数画像も可）。
    """
    if not input_images:
        return prompt
    parts: list[dict[str, Any]] = []
    for path in input_images:
        parts.append(image_to_input_part(path))
    parts.append({"type": "text", "text": prompt})
    return parts


def _build_request_body(
    model: str,
    input_value: Any,
    *,
    aspect_ratio: Optional[str],
    task: Optional[str],
    previous_interaction_id: Optional[str],
) -> dict[str, Any]:
    """公式 interactions のリクエストボディを構築する。"""
    body: dict[str, Any] = {
        "model": model,
        "input": input_value,
    }
    if aspect_ratio:
        body["response_format"] = {"type": "video", "aspect_ratio": aspect_ratio}
    if task:
        body["generation_config"] = {"video_config": {"task": task}}
    if previous_interaction_id:
        body["previous_interaction_id"] = previous_interaction_id
    return body


async def create_interaction(
    access_token: str,
    model: str,
    input_value: Any,
    *,
    aspect_ratio: Optional[str] = None,
    task: Optional[str] = None,
    previous_interaction_id: Optional[str] = None,
    project_id: Optional[str] = None,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Gemini Omni Flash で動画を生成する（interactions を叩く）。"""
    url = build_interactions_url()
    body = _build_request_body(
        model,
        input_value,
        aspect_ratio=aspect_ratio,
        task=task,
        previous_interaction_id=previous_interaction_id,
    )
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    # OAuth 方式では課金/クォータ帰属のため x-goog-user-project を付ける。
    if project_id:
        headers["x-goog-user-project"] = project_id

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as e:
            raise ValueError(f"API への接続に失敗しました: {e}") from e

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        retry_ms = int(retry_after) * 1000 if retry_after else 60 * 1000
        raise RateLimitError(
            f"レート制限を超過しました。約 {max(1, retry_ms // 1000)} 秒後に再試行してください。",
            retry_ms,
        )

    if resp.status_code == 503:
        raise CapacityError(f"モデル容量が不足しています（503）: {resp.text[:500]}")

    if resp.status_code == 401 or resp.status_code == 403:
        raise ValueError(
            f"認証/権限エラー ({resp.status_code}): {resp.text[:500]}\n"
            "OAuth ログイン状態（必要なら reauthorize）、プロジェクトの Generative Language API "
            "（generativelanguage.googleapis.com）有効化、および当該モデルへのアクセス権を"
            "確認してください。"
        )

    if resp.status_code != 200:
        raise ValueError(f"API リクエストに失敗しました ({resp.status_code}): {resp.text[:800]}")

    return resp.json()


def extract_videos(response: dict[str, Any]) -> list[dict[str, str]]:
    """レスポンス（steps 配列）から動画(base64)を取り出す。

    model_output ステップの content 内、type == "video" のパートを収集する。
    """
    if response.get("error"):
        raise ValueError(f"API エラー: {response['error'].get('message')}")

    status = response.get("status")
    if status and status not in ("completed", "succeeded"):
        # blocked / failed 等
        raise ValueError(f"生成が完了しませんでした（status={status}）: {str(response)[:500]}")

    steps = response.get("steps") or []
    if not steps:
        raise ValueError("動画が生成されませんでした（steps が空）")

    videos: list[dict[str, str]] = []
    for step in steps:
        if step.get("type") not in ("model_output", None):
            # model_output 以外（user_input / thought）はスキップ
            if step.get("type"):
                continue
        for part in step.get("content") or []:
            if part.get("type") == "video" and part.get("data"):
                videos.append(
                    {
                        "mimeType": part.get("mime_type") or part.get("mimeType", "video/mp4"),
                        "data": part["data"],
                    }
                )

    if not videos:
        raise ValueError("レスポンスに動画データが含まれていませんでした")
    return videos
