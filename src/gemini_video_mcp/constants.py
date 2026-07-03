"""
Gemini Omni Flash 動画生成の定数。

認証は MCP 内蔵のブラウザ OAuth を使う（auth.py 参照）。gcloud / google-auth には依存しない。
gemini-image-mcp が Gemini Enterprise Agent Platform（aiplatform.googleapis.com）の
generateContent を叩くのに対し、本 MCP は Gemini API の interactions エンドポイント
（generativelanguage.googleapis.com/v1beta/interactions）で動画を生成する。

出所（公式ドキュメント）:
  - https://ai.google.dev/gemini-api/docs/omni（Gemini Omni Flash: テキスト/画像→動画、
    アスペクト比、task パラメータ、ステートフル編集）
"""

import os

# --- Gemini API（interactions）エンドポイント ---
# 動画生成は Gemini Developer API の interactions で提供される。
# OAuth の場合は Authorization: Bearer <token>、API キーの場合は ?key= を使う（本 MCP は OAuth）。
GEMINI_API_HOST = os.environ.get(
    "GEMINI_VIDEO_API_HOST", "https://generativelanguage.googleapis.com"
)
API_VERSION = os.environ.get("GEMINI_VIDEO_API_VERSION", "v1beta")


def build_interactions_url() -> str:
    """interactions エンドポイントの完全な URL を組み立てる。"""
    return f"{GEMINI_API_HOST}/{API_VERSION}/interactions"


# --- モデル（GEMINI_VIDEO_MODEL / MCP 設定で切替可能）---
# 出所: ai.google.dev/gemini-api/docs/omni
MODEL_ALIASES = {
    # 分かりやすい別名 -> 実モデルID
    "omni-flash": "gemini-omni-flash-preview",
    "gemini-omni-flash": "gemini-omni-flash-preview",
    "omni-flash-preview": "gemini-omni-flash-preview",
    "omni-flash-latest": "gemini-omni-flash-preview",
}
SUPPORTED_MODELS = [
    "gemini-omni-flash-preview",  # Gemini Omni Flash（プレビュー / 動画生成・編集）
]
# 将来追加されうる候補モデルID（GA 版・上位版など）。現時点では未提供だが、
# GEMINI_VIDEO_MODEL でこれらを指定してもエラーにせず素通しできるよう、
# 既知候補として控えておく（resolve_model は SUPPORTED_MODELS に無い値も許容する）。
CANDIDATE_MODELS = [
    "gemini-omni-flash",        # GA 想定（preview サフィックスなし）
    "gemini-omni-flash-001",    # 日付/版サフィックス付き想定
    "gemini-omni-pro-preview",  # 上位版（プレビュー）想定
    "gemini-omni-pro",          # 上位版 GA 想定
]
# 環境変数 GEMINI_VIDEO_MODEL が設定されていれば、それを常に使う（呼び出し側の
# model 引数より優先＝強制）。未設定なら既定（Gemini Omni Flash）を使う。
_ENV_MODEL_RAW = os.environ.get("GEMINI_VIDEO_MODEL", "").strip()
MODEL_FORCED = bool(_ENV_MODEL_RAW)
_DEFAULT_MODEL_RAW = _ENV_MODEL_RAW or "gemini-omni-flash-preview"
DEFAULT_MODEL = MODEL_ALIASES.get(_DEFAULT_MODEL_RAW, _DEFAULT_MODEL_RAW)


def resolve_model(model: str | None) -> str:
    """別名を実モデルIDに解決する。

    GEMINI_VIDEO_MODEL が設定されている場合は、引数 model を無視して常に
    環境変数のモデルを返す。未設定なら引数のモデル（別名可）、無ければ既定。
    """
    if MODEL_FORCED:
        return DEFAULT_MODEL
    if not model:
        return DEFAULT_MODEL
    return MODEL_ALIASES.get(model, model)


# --- 動画パラメータ ---
# 出所: ai.google.dev/gemini-api/docs/omni（アスペクト比は 16:9 / 9:16 のみ、既定 16:9）
VALID_ASPECT_RATIOS = ["16:9", "9:16"]
DEFAULT_ASPECT_RATIO = os.environ.get("GEMINI_VIDEO_ASPECT_RATIO", "16:9")

# task パラメータ（video_config.task）。未指定ならモデルがプロンプトから推測する。
VALID_TASKS = ["text_to_video", "image_to_video", "reference_to_video", "edit"]

# --- 出力先 ---
DEFAULT_OUT_DIR = os.environ.get(
    "GEMINI_VIDEO_OUT_DIR", os.path.expanduser("~/gemini-videos")
)

# --- タイムアウト（動画生成は画像より時間がかかるため長めの既定）---
DEFAULT_TIMEOUT = float(os.environ.get("GEMINI_VIDEO_TIMEOUT", "600"))
