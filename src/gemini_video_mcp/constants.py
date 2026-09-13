"""
Gemini Omni 1.1 Flash 動画生成の定数。

認証は MCP 内蔵のブラウザ OAuth を使う（auth.py 参照）。gcloud / google-auth には依存しない。
gemini-image-mcp が Gemini Enterprise Agent Platform（aiplatform.googleapis.com）の
generateContent を叩くのに対し、本 MCP は Gemini API の interactions エンドポイント
（generativelanguage.googleapis.com/v1beta/interactions）で動画を生成する。

出所（公式ドキュメント）:
  - https://ai.google.dev/gemini-api/docs/omni（Gemini Omni 1.1 Flash: テキスト/画像→動画、
    アスペクト比、解像度、task パラメータ、ステートフル編集・延長）
  - https://ai.google.dev/gemini-api/docs/models（モデルコード: gemini-omni-1.1-flash）
  - https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-1-flash
    （Vertex 側の同モデル。ID は gemini-omni-1.1-flash-preview、2026-08-27 リリース、
      動画は最大 10 秒、解像度 360p/720p/1080p/4k、アスペクト比 16:9 / 9:16）
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
# 出所: ai.google.dev/gemini-api/docs/models, ai.google.dev/gemini-api/docs/omni
# Gemini API（generativelanguage）側のモデルコードは "gemini-omni-1.1-flash"。
# Vertex（Gemini Enterprise Agent Platform）側は "gemini-omni-1.1-flash-preview" と
# 表記されるが、本 MCP は Gemini API を叩くため Gemini API 側の ID を正とする。
OMNI_1_1_FLASH = "gemini-omni-1.1-flash"
OMNI_FLASH_LEGACY = "gemini-omni-flash-preview"  # 旧世代（1.0 プレビュー）

MODEL_ALIASES = {
    # 分かりやすい別名 -> 実モデルID（最新世代）
    "omni-flash": OMNI_1_1_FLASH,
    "omni-flash-latest": OMNI_1_1_FLASH,
    "gemini-omni-flash": OMNI_1_1_FLASH,
    "omni-1.1-flash": OMNI_1_1_FLASH,
    "gemini-omni-1.1-flash-preview": OMNI_1_1_FLASH,  # Vertex 側の表記
    "omni-1.1-flash-preview": OMNI_1_1_FLASH,
    # 旧世代を明示したいとき
    "omni-flash-preview": OMNI_FLASH_LEGACY,
    "omni-flash-legacy": OMNI_FLASH_LEGACY,
}
SUPPORTED_MODELS = [
    OMNI_1_1_FLASH,     # Gemini Omni 1.1 Flash（プレビュー / 動画生成・編集・延長）
    OMNI_FLASH_LEGACY,  # Gemini Omni Flash（旧プレビュー。提供終了の可能性あり）
]
# 将来追加されうる候補モデルID（GA 版・上位版など）。現時点では未提供だが、
# GEMINI_VIDEO_MODEL でこれらを指定してもエラーにせず素通しできるよう、
# 既知候補として控えておく（resolve_model は SUPPORTED_MODELS に無い値も許容する）。
CANDIDATE_MODELS = [
    "gemini-omni-1.1-flash-001",  # 日付/版サフィックス付き想定
    "gemini-omni-1.1-pro",        # 上位版想定
    "gemini-omni-pro-preview",    # 上位版（プレビュー）想定
]
# 環境変数 GEMINI_VIDEO_MODEL が設定されていれば、それを常に使う（呼び出し側の
# model 引数より優先＝強制）。未設定なら既定（Gemini Omni 1.1 Flash）を使う。
_ENV_MODEL_RAW = os.environ.get("GEMINI_VIDEO_MODEL", "").strip()
MODEL_FORCED = bool(_ENV_MODEL_RAW)
_DEFAULT_MODEL_RAW = _ENV_MODEL_RAW or OMNI_1_1_FLASH
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

# 解像度（response_format.resolution）。Omni 1.1 Flash で追加。既定 720p。
# 1080p / 4k はアップスケール。4MB 超の動画は公式では delivery="uri" 推奨だが、
# 本 MCP は base64 インライン受信のため、高解像度はペイロード上限に注意。
VALID_RESOLUTIONS = ["360p", "720p", "1080p", "4k"]
DEFAULT_RESOLUTION = os.environ.get("GEMINI_VIDEO_RESOLUTION", "720p")

# task パラメータ（video_config.task）。未指定ならモデルがプロンプトから推測する。
# extend は Omni 1.1 Flash で追加（動画末尾に 3〜10 秒の続きを生成）。
VALID_TASKS = ["text_to_video", "image_to_video", "reference_to_video", "edit", "extend"]

# --- 出力先 ---
DEFAULT_OUT_DIR = os.environ.get(
    "GEMINI_VIDEO_OUT_DIR", os.path.expanduser("~/gemini-videos")
)

# --- タイムアウト（動画生成は画像より時間がかかるため長めの既定）---
DEFAULT_TIMEOUT = float(os.environ.get("GEMINI_VIDEO_TIMEOUT", "600"))
