"""
Gemini Video MCP Server — stdio 版（MCP 内蔵ブラウザ OAuth 方式）

自分の Google Cloud プロジェクトの OAuth クライアント（デスクトップアプリ型）で
ブラウザログインし、Gemini Omni Flash（gemini-omni-flash-preview）を
Gemini API の interactions エンドポイント経由で叩いて動画を生成・編集する MCP サーバー。
gcloud / google-auth には依存しない。トークンは MCP 自身が保存・自動更新する。
（gemini-image-mcp の姉妹プロジェクト。認証・トークン運用は同じパターン。）

前提:
  1. Google Cloud Console で OAuth クライアント（デスクトップアプリ型）を作成
     （gemini-image-mcp と同じクライアントを流用可）
  2. CLIENT_ID / CLIENT_SECRET を環境変数で渡す
  3. 対象プロジェクトで Generative Language API（generativelanguage.googleapis.com）を有効化
  4. 初回 generate_video / reauthorize 時にブラウザが開きログイン → トークン保存

環境変数:
  GEMINI_VIDEO_CLIENT_ID     : OAuth クライアントID（必須）
  GEMINI_VIDEO_CLIENT_SECRET     : クライアントシークレット（必須）
  GEMINI_VIDEO_REDIRECT_PORT : 初回認可のローカルポート（未指定なら空きポートを自動選択）
  GOOGLE_CLOUD_PROJECT / GEMINI_VIDEO_PROJECT_ID : 使用するGCPプロジェクトID（クォータ帰属）
  GEMINI_VIDEO_MODEL         : モデル。既定 gemini-omni-flash-preview
                               別名: omni-flash / gemini-omni-flash
  GEMINI_VIDEO_ASPECT_RATIO  : 既定アスペクト比。既定 16:9（他に 9:16）
  GEMINI_VIDEO_OUT_DIR       : out_path 省略時の出力先。既定 ~/gemini-videos
  GEMINI_VIDEO_TIMEOUT       : API タイムアウト秒。既定 600

登録例:
  uvx --from git+https://github.com/tsukumonasu/gemini-video-mcp gemini-video-mcp
"""

import os
import logging
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp.server import FastMCP

from . import api as ag_api
from . import auth as ag_auth
from .constants import (
    DEFAULT_MODEL,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_OUT_DIR,
    DEFAULT_TIMEOUT,
    VALID_ASPECT_RATIOS,
    VALID_TASKS,
    SUPPORTED_MODELS,
    CANDIDATE_MODELS,
    MODEL_ALIASES,
    MODEL_FORCED,
    resolve_model,
)
from .video_saver import save_videos

logger = logging.getLogger(__name__)


class Settings:
    def __init__(self) -> None:
        self.out_dir = Path(DEFAULT_OUT_DIR)
        self.timeout = DEFAULT_TIMEOUT


def create_server(settings: Settings, tokens: ag_auth.TokenManager) -> FastMCP:
    app = FastMCP(
        name="Gemini Video MCP Server",
        instructions=(
            "自分の Google Cloud プロジェクトの Gemini API 経由で "
            "Gemini Omni Flash を使い動画を生成・編集する MCP サーバー。"
            "認証は MCP 内蔵のブラウザ OAuth。auth_status で準備状況を確認し、"
            "generate_video で生成、edit_video で（前の生成結果を踏まえて）編集する。"
            "認証エラー時は reauthorize を実行する。"
        ),
    )

    @app.tool()
    async def auth_status() -> dict[str, Any]:
        """認証・プロジェクト・モデルの準備状況を確認する。

        Returns:
            ready: 動画生成の準備が整っているか
            auth: 認証状態（valid / expired / needs_reauth）
            client_configured: OAuthクライアント(ID/SECRET)が設定済みか
            project_id: 解決されたプロジェクトID
            model: 現在の既定モデル
            available_models: 選択可能なモデル一覧
            message: 状態の説明と次のアクション
        """
        info: dict[str, Any] = {
            "model": DEFAULT_MODEL,
            "available_models": SUPPORTED_MODELS,
            "model_aliases": MODEL_ALIASES,
            "out_dir": str(settings.out_dir),
        }
        client_configured = bool(
            tokens.settings.client_id and tokens.settings.client_secret
        )
        project_id = ag_auth.resolve_project_id()
        auth_state = tokens.status()

        ready = client_configured and bool(project_id) and auth_state in ("valid", "expired")
        if not client_configured:
            msg = (
                "OAuth クライアントが未設定です。Google Cloud Console でデスクトップアプリ型の "
                "OAuth クライアントを作成し、GEMINI_VIDEO_CLIENT_ID / GEMINI_VIDEO_CLIENT_SECRET "
                "を設定してください。"
            )
        elif not project_id:
            msg = (
                "プロジェクトIDが未設定です。GOOGLE_CLOUD_PROJECT か GEMINI_VIDEO_PROJECT_ID を設定してください。"
            )
        elif auth_state == "needs_reauth":
            msg = "未認証です。generate_video を実行するとブラウザが開きます（または reauthorize を実行）。"
        else:
            msg = "準備完了。generate_video を実行できます。"

        info.update(
            client_configured=client_configured,
            project_id=project_id,
            auth=auth_state,
            ready=ready,
            message=msg,
        )
        return info

    @app.tool()
    async def reauthorize() -> dict[str, Any]:
        """ブラウザで Google に再ログインしてトークンを取り直す。

        ブラウザが自動で開き、Google のログイン画面が表示されます。
        認可を完了すると新しいトークンが保存されます。
        認証エラー（401等）や Refresh Token 失効時に実行してください。
        """
        try:
            await tokens.reauthorize_async()
        except ag_auth.AuthError as e:
            raise ValueError(str(e)) from None
        return {"status": "ok", "message": "再認証が完了し、トークンを更新しました。"}

    @app.tool()
    async def list_models() -> dict[str, Any]:
        """利用可能な動画生成モデルの一覧と別名を返す。"""
        return {
            "models": SUPPORTED_MODELS,
            "candidate_models": CANDIDATE_MODELS,
            "aliases": MODEL_ALIASES,
            "default": DEFAULT_MODEL,
            "forced_by_env": MODEL_FORCED,
            "aspect_ratios": VALID_ASPECT_RATIOS,
            "tasks": VALID_TASKS,
            "note": (
                "モデルは環境変数 GEMINI_VIDEO_MODEL が最優先（設定時は generate_video の "
                "model 引数を無視して常にこのモデルを使う）。未設定なら model 引数、無ければ既定 "
                f"{DEFAULT_MODEL} を使う。別名（omni-flash など）も使えます。"
                "candidate_models は将来の GA/上位版を見越した候補で、現時点では未提供の可能性が "
                "ありますが GEMINI_VIDEO_MODEL に指定すればそのまま送信されます。"
                "アスペクト比は 16:9（既定）/ 9:16。task は未指定ならプロンプトから推測されます。"
            ),
        }

    async def _get_token_or_raise() -> str:
        try:
            return await tokens.get_token()
        except ag_auth.ReauthorizationRequired as e:
            raise ValueError(
                f"{e} 認証が必要です。reauthorize ツールを実行してブラウザでログインしてください。"
            ) from None
        except ag_auth.AuthError as e:
            raise ValueError(str(e)) from None

    def _resolve_out(out_path: Optional[str]) -> tuple[str, Optional[str]]:
        """出力ディレクトリと custom_name を決める。"""
        if out_path:
            out = Path(os.path.expanduser(out_path))
            out.parent.mkdir(parents=True, exist_ok=True)
            return str(out.parent), out.stem
        settings.out_dir.mkdir(parents=True, exist_ok=True)
        return str(settings.out_dir), None

    @app.tool()
    async def generate_video(
        prompt: str,
        out_path: Optional[str] = None,
        model: Optional[str] = None,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        task: Optional[str] = None,
        input_images: Optional[list[str]] = None,
        input_videos: Optional[list[str]] = None,
        project_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Gemini Omni Flash で動画を生成する。

        テキストのみ（text_to_video）、参照画像あり（image_to_video / reference_to_video）に対応。
        音声付きの MP4 が生成されます。シーン説明・カメラの動き・照明・ムードなどを
        プロンプトに具体的に書くと良い結果になります。

        Args:
            prompt: 生成したい動画の説明。input_images 指定時はその画像をどう使うかの指示。
            out_path: 出力先ファイルパス（.mp4）。省略時は GEMINI_VIDEO_OUT_DIR に自動命名で保存。
                      ※ Amazon Quick でプレビューするには許可フォルダ内のパスを指定すること。
            model: 使用モデル。別名 omni-flash 可。環境変数 GEMINI_VIDEO_MODEL 設定時はこの引数は無視。
            aspect_ratio: アスペクト比。16:9（既定・横）または 9:16（縦）。
            task: 動作の明示指定（任意）。text_to_video / image_to_video / reference_to_video / edit。
                  未指定ならモデルがプロンプト・入力からタスクを推測する。
            input_images: 参照画像のパス配列（任意）。1枚なら画像→動画、複数なら被写体参照など。
                          高解像度画像＋具体的な動きの指示を推奨。
            input_videos: 入力動画のパス配列（任意）。動画→動画（編集）に使う。動画を渡すと
                          その動画を素材に編集した動画を生成する（task は image_to_video ではなく
                          未指定または edit を推奨）。※ 複数動画の同時参照は非対応のため通常は1本。
                          ※ 大きな動画は base64 送信のためペイロード上限に注意。
                          ※ EEA/スイス/UK ではアップロード動画の編集は非対応（モデル生成動画の編集は可）。
            project_id: プロジェクトID上書き（任意。クォータ帰属用）。

        Returns:
            success / videos / out_path / interaction_id / model / project_id
            interaction_id は edit_video の previous_interaction_id に渡してステートフル編集できる。
        """
        if aspect_ratio not in VALID_ASPECT_RATIOS:
            raise ValueError(f"無効なアスペクト比: {aspect_ratio}。有効値: {VALID_ASPECT_RATIOS}")
        if task is not None and task not in VALID_TASKS:
            raise ValueError(f"無効な task: {task}。有効値: {VALID_TASKS}")

        resolved_model = resolve_model(model)
        model_note = None
        if MODEL_FORCED and model and MODEL_ALIASES.get(model, model) != resolved_model:
            model_note = (
                f"model={model} が指定されましたが、環境変数 GEMINI_VIDEO_MODEL の "
                f"{resolved_model} を使用します。"
            )
            logger.info(model_note)

        proj = ag_auth.resolve_project_id(explicit=project_id)
        access_token = await _get_token_or_raise()

        input_value = ag_api.build_input(
            prompt, input_images=input_images, input_videos=input_videos
        )

        response = await ag_api.create_interaction(
            access_token,
            resolved_model,
            input_value,
            aspect_ratio=aspect_ratio,
            task=task,
            project_id=proj,
            timeout=settings.timeout,
        )
        videos = ag_api.extract_videos(response)

        output_dir, custom_name = _resolve_out(out_path)
        saved = save_videos(videos, prompt, output_dir, custom_name=custom_name)

        result: dict[str, Any] = {
            "success": True,
            "videos": saved,
            "out_path": saved[0]["path"] if saved else None,
            "count": len(saved),
            "interaction_id": response.get("id"),
            "model": resolved_model,
            "aspect_ratio": aspect_ratio,
            "project_id": proj,
        }
        if model_note:
            result["note"] = model_note
        return result

    @app.tool()
    async def edit_video(
        prompt: str,
        previous_interaction_id: Optional[str] = None,
        out_path: Optional[str] = None,
        model: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        input_images: Optional[list[str]] = None,
        input_videos: Optional[list[str]] = None,
        project_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """動画を編集する。編集対象は次の2通りのいずれかで指定する。

        1. ステートフル編集（前の生成結果を継続編集）:
           previous_interaction_id に generate_video / edit_video が返した interaction_id を渡すと、
           前の動画を再アップロードせずに編集が適用されます。モデルは動画のコンテキストを記憶し、
           言及していない要素を保持しながら変更を適用します。
        2. 手持ち動画の編集（動画→動画）:
           input_videos に手元の動画ファイルパスを渡すと、その動画を素材に編集した動画を生成します
           （previous_interaction_id が無い場合はこちら）。

        いずれの場合も task は edit として送信します。previous_interaction_id と input_videos の
        少なくとも一方を指定してください。

        Args:
            prompt: 変更したい内容の説明（例: "空を夕焼けにして、カメラをゆっくり右へパン"）。
                    編集はシンプルなプロンプトが有効。「他はそのまま」を添えると一貫性を保ちやすい。
            previous_interaction_id: 前回の生成/編集で返った interaction_id（ステートフル編集時）。
            out_path: 出力先ファイルパス（.mp4）。省略時は GEMINI_VIDEO_OUT_DIR に自動命名で保存。
            model: 使用モデル（任意）。環境変数 GEMINI_VIDEO_MODEL 設定時は無視。
            aspect_ratio: アスペクト比（任意。16:9 / 9:16）。省略時は前の設定を引き継ぐ。
            input_images: 追加の参照画像パス配列（任意）。
            input_videos: 編集対象の入力動画パス配列（任意。動画→動画）。previous_interaction_id を
                          使わず手持ち動画を編集する場合に指定。※ 複数動画の同時参照は非対応（通常1本）。
                          ※ 大きな動画は base64 送信のためペイロード上限に注意。
                          ※ EEA/スイス/UK ではアップロード動画の編集は非対応（モデル生成動画の編集は可）。
            project_id: プロジェクトID上書き（任意）。

        Returns:
            success / videos / out_path / interaction_id / model / project_id
        """
        if aspect_ratio is not None and aspect_ratio not in VALID_ASPECT_RATIOS:
            raise ValueError(f"無効なアスペクト比: {aspect_ratio}。有効値: {VALID_ASPECT_RATIOS}")
        if not previous_interaction_id and not input_videos:
            raise ValueError(
                "編集対象がありません。previous_interaction_id（ステートフル編集）または "
                "input_videos（手持ち動画の編集）の少なくとも一方を指定してください。"
            )

        resolved_model = resolve_model(model)
        proj = ag_auth.resolve_project_id(explicit=project_id)
        access_token = await _get_token_or_raise()

        input_value = ag_api.build_input(
            prompt, input_images=input_images, input_videos=input_videos
        )

        response = await ag_api.create_interaction(
            access_token,
            resolved_model,
            input_value,
            aspect_ratio=aspect_ratio,
            task="edit",
            previous_interaction_id=previous_interaction_id,
            project_id=proj,
            timeout=settings.timeout,
        )
        videos = ag_api.extract_videos(response)

        output_dir, custom_name = _resolve_out(out_path)
        saved = save_videos(videos, prompt, output_dir, custom_name=custom_name)

        return {
            "success": True,
            "videos": saved,
            "out_path": saved[0]["path"] if saved else None,
            "count": len(saved),
            "interaction_id": response.get("id"),
            "previous_interaction_id": previous_interaction_id,
            "model": resolved_model,
            "project_id": proj,
        }

    return app


def main() -> None:
    """uvx / コンソールスクリプトのエントリポイント。"""
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    auth_settings = ag_auth.Settings()
    tokens = ag_auth.TokenManager(auth_settings)
    mcp = create_server(settings, tokens)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
