"""
Gemini Video MCP Server — stdio 版（MCP 内蔵ブラウザ OAuth 方式）

自分の Google Cloud プロジェクトの OAuth クライアント（デスクトップアプリ型）で
ブラウザログインし、Gemini Omni 1.1 Flash（gemini-omni-1.1-flash）を
Gemini API の interactions エンドポイント経由で叩いて動画を生成・編集・延長する MCP サーバー。
MCP Python SDK 2.x（mcp.server.mcpserver.MCPServer）を使用。
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
  GEMINI_VIDEO_MODEL         : モデル。既定 gemini-omni-1.1-flash
                               別名: omni-flash / gemini-omni-flash / omni-1.1-flash
                               （旧世代を使うなら gemini-omni-flash-preview）
  GEMINI_VIDEO_ASPECT_RATIO  : 既定アスペクト比。既定 16:9（他に 9:16）
  GEMINI_VIDEO_RESOLUTION    : 既定解像度。既定 720p（他に 360p / 1080p / 4k）
  GEMINI_VIDEO_OUT_DIR       : out_path 省略時の出力先。既定 ~/gemini-videos
  GEMINI_VIDEO_TIMEOUT       : API タイムアウト秒。既定 600

登録例:
  uvx --from git+https://github.com/tsukumonasu/gemini-video-mcp gemini-video-mcp
"""

import os
import functools
import logging
from pathlib import Path
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import api as ag_api
from . import auth as ag_auth
from .constants import (
    DEFAULT_MODEL,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_RESOLUTION,
    DEFAULT_OUT_DIR,
    DEFAULT_TIMEOUT,
    VALID_ASPECT_RATIOS,
    VALID_RESOLUTIONS,
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


# 想定内の失敗（入力不備・API エラー・認証エラー）。MCP SDK 2.x では ToolError 以外の
# 例外は「クラッシュ」扱いになり、クライアントには "Error executing tool <name>" しか
# 返らない（メッセージが隠れる）。これらは ToolError に変換してメッセージを届ける。
_EXPECTED_ERRORS: tuple[type[Exception], ...] = (
    ValueError,
    ag_api.RateLimitError,
    ag_api.CapacityError,
    ag_auth.AuthError,
    ag_auth.ReauthorizationRequired,
)


def _as_tool_error(fn):
    """ツール本体の想定内例外を ToolError に変換するデコレータ。"""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except _EXPECTED_ERRORS as e:
            raise ToolError(str(e)) from e

    return wrapper


def create_server(settings: Settings, tokens: ag_auth.TokenManager) -> MCPServer:
    app = MCPServer(
        name="Gemini Video MCP Server",
        instructions=(
            "自分の Google Cloud プロジェクトの Gemini API 経由で "
            "Gemini Omni 1.1 Flash を使い動画を生成・編集・延長する MCP サーバー。"
            "認証は MCP 内蔵のブラウザ OAuth。auth_status で準備状況を確認し、"
            "generate_video で生成、edit_video で（前の生成結果を踏まえて）編集、"
            "extend_video で動画の続きを生成する。認証エラー時は reauthorize を実行する。"
        ),
        version="0.2.0",
    )

    @app.tool()
    @_as_tool_error
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
    @_as_tool_error
    async def reauthorize() -> dict[str, Any]:
        """ブラウザで Google に再ログインしてトークンを取り直す。

        ブラウザが自動で開き、Google のログイン画面が表示されます。
        認可を完了すると新しいトークンが保存されます。
        認証エラー（401等）や Refresh Token 失効時に実行してください。
        """
        try:
            await tokens.reauthorize_async()
        except ag_auth.AuthError as e:
            raise ToolError(str(e)) from None
        return {"status": "ok", "message": "再認証が完了し、トークンを更新しました。"}

    @app.tool()
    @_as_tool_error
    async def list_models() -> dict[str, Any]:
        """利用可能な動画生成モデルの一覧と別名を返す。"""
        return {
            "models": SUPPORTED_MODELS,
            "candidate_models": CANDIDATE_MODELS,
            "aliases": MODEL_ALIASES,
            "default": DEFAULT_MODEL,
            "forced_by_env": MODEL_FORCED,
            "aspect_ratios": VALID_ASPECT_RATIOS,
            "resolutions": VALID_RESOLUTIONS,
            "default_resolution": DEFAULT_RESOLUTION,
            "tasks": VALID_TASKS,
            "note": (
                "モデルは環境変数 GEMINI_VIDEO_MODEL が最優先（設定時は generate_video の "
                "model 引数を無視して常にこのモデルを使う）。未設定なら model 引数、無ければ既定 "
                f"{DEFAULT_MODEL} を使う。別名（omni-flash など）も使えます。"
                "gemini-omni-flash-preview は旧世代（1.0）で、提供終了の可能性があります。"
                "candidate_models は将来の GA/上位版を見越した候補で、現時点では未提供の可能性が "
                "ありますが GEMINI_VIDEO_MODEL に指定すればそのまま送信されます。"
                "アスペクト比は 16:9（既定）/ 9:16。解像度は 360p / 720p（既定）/ 1080p / 4k。"
                "task は未指定ならプロンプトから推測されます（extend は動画の続きを生成）。"
            ),
        }

    async def _get_token_or_raise() -> str:
        try:
            return await tokens.get_token()
        except ag_auth.ReauthorizationRequired as e:
            raise ToolError(
                f"{e} 認証が必要です。reauthorize ツールを実行してブラウザでログインしてください。"
            ) from None
        except ag_auth.AuthError as e:
            raise ToolError(str(e)) from None

    def _resolve_out(out_path: Optional[str]) -> tuple[str, Optional[str]]:
        """出力ディレクトリと custom_name を決める。"""
        if out_path:
            out = Path(os.path.expanduser(out_path))
            out.parent.mkdir(parents=True, exist_ok=True)
            return str(out.parent), out.stem
        settings.out_dir.mkdir(parents=True, exist_ok=True)
        return str(settings.out_dir), None

    @app.tool()
    @_as_tool_error
    async def generate_video(
        prompt: str,
        out_path: Optional[str] = None,
        model: Optional[str] = None,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        resolution: str = DEFAULT_RESOLUTION,
        task: Optional[str] = None,
        input_images: Optional[list[str]] = None,
        input_videos: Optional[list[str]] = None,
        first_frame: Optional[str] = None,
        last_frame: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Gemini Omni 1.1 Flash で動画を生成する。

        テキストのみ（text_to_video）、参照画像あり（image_to_video / reference_to_video）、
        最初と最後のフレーム指定（first_frame / last_frame）、参照動画あり（reference_to_video）、
        動画→動画（edit）に対応。音声付きの MP4（最大 10 秒）が生成されます。
        シーン説明・カメラの動き・照明・ムードなどをプロンプトに具体的に書くと良い結果になります。
        360p で素早く試作し、決まったら 1080p / 4k（アップスケール）で仕上げる使い方ができます。

        Args:
            prompt: 生成したい動画の説明。input_images 指定時はその画像をどう使うかの指示。
            out_path: 出力先ファイルパス（.mp4）。省略時は GEMINI_VIDEO_OUT_DIR に自動命名で保存。
                      ※ Amazon Quick でプレビューするには許可フォルダ内のパスを指定すること。
            model: 使用モデル。別名 omni-flash 可。環境変数 GEMINI_VIDEO_MODEL 設定時はこの引数は無視。
            aspect_ratio: アスペクト比。16:9（既定・横）または 9:16（縦）。
            resolution: 解像度。360p / 720p（既定）/ 1080p / 4k。1080p・4k はアップスケール。
                        ※ 本 MCP は base64 で受信するため、高解像度はペイロード上限で失敗する場合がある。
            task: 動作の明示指定（任意）。text_to_video / image_to_video / reference_to_video / edit / extend。
                  未指定ならモデルがプロンプト・入力からタスクを推測する。
            input_images: 参照画像のパス配列（任意・最大 10 枚）。1枚なら画像→動画、複数なら被写体参照など。
                          高解像度画像＋具体的な動きの指示を推奨。
            input_videos: 入力動画のパス配列（任意）。用途は2つ。
                          (a) 動画→動画の編集: 1 本（10 秒以内）を渡し task は未指定または edit。
                          (b) 参照動画: 人物の動きなどを参考にさせる短い動画（3 秒以内・最大 3 本）を渡し
                              task=reference_to_video。プロンプトでどう参考にするか指示する。
                          ※ 複数動画にまたがる内容理解・推論は非対応。
                          ※ 大きな動画は base64 送信のためペイロード上限に注意。
                          ※ EEA/スイス/UK ではアップロード動画の編集は非対応（モデル生成動画の編集は可）。
            first_frame: 動画の最初のフレームにする画像パス（任意）。last_frame と組み合わせると
                         2 枚の画像の間をつなぐ動画（キーフレーム補間）を生成する。
            last_frame: 動画の最後のフレームにする画像パス（任意）。first_frame と一緒に指定する。
            project_id: プロジェクトID上書き（任意。クォータ帰属用）。

        Returns:
            success / videos / out_path / interaction_id / model / project_id
            interaction_id は edit_video / extend_video の previous_interaction_id に渡して
            ステートフル編集・延長できる。
        """
        if aspect_ratio not in VALID_ASPECT_RATIOS:
            raise ToolError(f"無効なアスペクト比: {aspect_ratio}。有効値: {VALID_ASPECT_RATIOS}")
        if resolution not in VALID_RESOLUTIONS:
            raise ToolError(f"無効な解像度: {resolution}。有効値: {VALID_RESOLUTIONS}")
        if task is not None and task not in VALID_TASKS:
            raise ToolError(f"無効な task: {task}。有効値: {VALID_TASKS}")

        resolved_model = resolve_model(model)
        model_note = None
        if MODEL_FORCED and model and MODEL_ALIASES.get(model, model) != resolved_model:
            model_note = (
                f"model={model} が指定されましたが、環境変数 GEMINI_VIDEO_MODEL の "
                f"{resolved_model} を使用します。"
            )
            logger.info(model_note)

        if (first_frame is None) != (last_frame is None):
            raise ToolError("first_frame と last_frame は両方指定してください（片方だけなら input_images を使う）。")
        if first_frame and last_frame:
            # 公式例: 最初のフレーム → 最後のフレーム → テキスト の順で input に並べる
            input_images = [first_frame, last_frame, *(input_images or [])]

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
            resolution=resolution,
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
            "resolution": resolution,
            "project_id": proj,
        }
        if model_note:
            result["note"] = model_note
        return result

    @app.tool()
    @_as_tool_error
    async def edit_video(
        prompt: str,
        previous_interaction_id: Optional[str] = None,
        out_path: Optional[str] = None,
        model: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        resolution: Optional[str] = None,
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
            resolution: 解像度（任意。360p / 720p / 1080p / 4k）。省略時は API 既定（720p）。
            input_images: 追加の参照画像パス配列（任意）。
            input_videos: 編集対象の入力動画パス配列（任意。動画→動画・10 秒以内）。previous_interaction_id を
                          使わず手持ち動画を編集する場合に指定。※ 複数動画にまたがる編集は非対応（1本）。
                          ※ 大きな動画は base64 送信のためペイロード上限に注意。
                          ※ EEA/スイス/UK ではアップロード動画の編集は非対応（モデル生成動画の編集は可）。
            project_id: プロジェクトID上書き（任意）。

        Returns:
            success / videos / out_path / interaction_id / model / project_id
        """
        return await _continue_video(
            prompt,
            task="edit",
            previous_interaction_id=previous_interaction_id,
            out_path=out_path,
            model=model,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            input_images=input_images,
            input_videos=input_videos,
            project_id=project_id,
        )

    @app.tool()
    @_as_tool_error
    async def extend_video(
        prompt: str,
        previous_interaction_id: Optional[str] = None,
        out_path: Optional[str] = None,
        model: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        resolution: Optional[str] = None,
        input_videos: Optional[list[str]] = None,
        project_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """動画の末尾に続きを生成して延長する（Omni 1.1 Flash の extend task）。

        1 回の延長で 3〜10 秒の続きが生成され、合計 40 秒まで延長できます。モデルは直前 10 秒を
        コンテキストとして見るため、人物・照明の一貫性が保たれやすいです。延長対象は edit_video と
        同様に previous_interaction_id（前の生成/編集/延長結果）または input_videos（手持ち動画・
        10 秒以内）で指定します。戻り値の interaction_id を再度 previous_interaction_id に渡せば、
        さらに延長を重ねられます。
        ※ EEA/スイス/UK ではアップロード動画の延長は非対応。アップロード動画に台詞を追加する延長も非対応。

        Args:
            prompt: 続きの内容の説明（例: "カメラがゆっくり引いて街全体が見える"）。
            previous_interaction_id: 前回の生成/編集/延長で返った interaction_id。
            out_path: 出力先ファイルパス（.mp4）。省略時は GEMINI_VIDEO_OUT_DIR に自動命名で保存。
            model: 使用モデル（任意）。環境変数 GEMINI_VIDEO_MODEL 設定時は無視。
            aspect_ratio: アスペクト比（任意。16:9 / 9:16）。省略時は前の設定を引き継ぐ。
            resolution: 解像度（任意。360p / 720p / 1080p / 4k）。
            input_videos: 延長対象の手持ち動画パス配列（任意。通常1本）。
            project_id: プロジェクトID上書き（任意）。

        Returns:
            success / videos / out_path / interaction_id / previous_interaction_id / model / project_id
        """
        return await _continue_video(
            prompt,
            task="extend",
            previous_interaction_id=previous_interaction_id,
            out_path=out_path,
            model=model,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            input_images=None,
            input_videos=input_videos,
            project_id=project_id,
        )

    async def _continue_video(
        prompt: str,
        *,
        task: str,
        previous_interaction_id: Optional[str],
        out_path: Optional[str],
        model: Optional[str],
        aspect_ratio: Optional[str],
        resolution: Optional[str],
        input_images: Optional[list[str]],
        input_videos: Optional[list[str]],
        project_id: Optional[str],
    ) -> dict[str, Any]:
        """edit / extend 共通: 前の interaction か手持ち動画を元に動画を生成する。"""
        if aspect_ratio is not None and aspect_ratio not in VALID_ASPECT_RATIOS:
            raise ToolError(f"無効なアスペクト比: {aspect_ratio}。有効値: {VALID_ASPECT_RATIOS}")
        if resolution is not None and resolution not in VALID_RESOLUTIONS:
            raise ToolError(f"無効な解像度: {resolution}。有効値: {VALID_RESOLUTIONS}")
        if not previous_interaction_id and not input_videos:
            what = "編集" if task == "edit" else "延長"
            raise ToolError(
                f"{what}対象がありません。previous_interaction_id（ステートフル{what}）または "
                f"input_videos（手持ち動画の{what}）の少なくとも一方を指定してください。"
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
            resolution=resolution,
            task=task,
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
            "task": task,
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
