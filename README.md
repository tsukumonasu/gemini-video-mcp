# gemini-video-mcp

自分の **Google Cloud プロジェクト**の OAuth クライアントで**ブラウザログイン**し、
**Gemini API**（`generativelanguage.googleapis.com`）の `interactions` エンドポイント経由で
**Gemini Omni Flash**（`gemini-omni-flash-preview`）を叩いて**動画を生成・編集**する
**stdio な MCP サーバー**。`FastMCP` + stdio 構成、`uvx` 対応。

> [gemini-image-mcp](https://github.com/tsukumonasu/gemini-image-mcp) の姉妹プロジェクトです。
> 認証（MCP 内蔵のブラウザ OAuth フロー）とトークン運用は同じパターンで、
> 同じ OAuth クライアントを流用できます。gcloud や google-auth には依存しません。
> 初回利用時にブラウザが開き、自分の Google アカウントでログイン → トークンを
> MCP 自身が保存・自動更新します。

## 画像版との違い

| | gemini-image-mcp | gemini-video-mcp |
|---|---|---|
| API | Gemini Enterprise Agent Platform (`aiplatform.googleapis.com`) の `generateContent` | Gemini API (`generativelanguage.googleapis.com`) の `interactions` |
| モデル | Nano Banana 2 Lite / 2 / Pro | Gemini Omni Flash (`gemini-omni-flash-preview`) |
| 出力 | 画像 (base64) | 動画 MP4 (base64) |
| 認証 | ブラウザ OAuth | ブラウザ OAuth（同じ） |

## 仕組み

```
MCP クライアント (Amazon Quick / Claude Desktop 等)
        │  stdio (MCP)
        ▼
gemini-video-mcp
        │  ① 初回: ブラウザOAuthログイン → access/refresh token を保存
        │  ② 以降: refresh token で access token を自動更新
        │  ③ POST /v1beta/interactions（Authorization: Bearer）
        ▼
https://generativelanguage.googleapis.com/v1beta/interactions
   model: gemini-omni-flash-preview
        │
        ▼
   動画 (base64 MP4) ──▶ ファイル保存
```

## 前提（セットアップ）

基本は gemini-image-mcp と同じです（同じ OAuth クライアントを流用可）。

1. **OAuth 同意画面**を設定（初回のみ）。「外部」の場合は自分をテストユーザーに追加。
2. **OAuth クライアントID（デスクトップアプリ型）**を作成し、Client ID / Secret を控える。
   - デスクトップアプリ型だけが `http://127.0.0.1:ポート/...` へのループバックリダイレクトを標準で許可します。
3. 対象プロジェクトで **Generative Language API**（`generativelanguage.googleapis.com`）を有効化。
   - `API とサービス` → `ライブラリ` で「Generative Language API」を検索して有効化。
4. 環境変数を設定（下表）。
5. 初回 `generate_video`（または `reauthorize`）でブラウザが開きログイン → トークン保存。

### 環境変数

| 変数 | 説明 |
|------|------|
| `GEMINI_VIDEO_CLIENT_ID` | OAuth クライアントID（必須） |
| `GEMINI_VIDEO_CLIENT_SECRET` | クライアントシークレット（必須） |
| `GOOGLE_CLOUD_PROJECT` または `GEMINI_VIDEO_PROJECT_ID` | 使用するプロジェクトID（クォータ帰属。必須） |
| `GEMINI_VIDEO_MODEL` | モデル。既定 `gemini-omni-flash-preview`（別名 `omni-flash`） |
| `GEMINI_VIDEO_ASPECT_RATIO` | 既定アスペクト比。既定 `16:9`（他に `9:16`） |
| `GEMINI_VIDEO_OUT_DIR` | `out_path` 省略時の出力先。既定 `~/gemini-videos` |
| `GEMINI_VIDEO_TIMEOUT` | API タイムアウト秒。既定 `600`（動画は時間がかかるため長め） |
| `GEMINI_VIDEO_REDIRECT_PORT` | 任意。初回認可のローカルポート（未指定なら空きポートを自動選択） |

> トークンは `~/.gemini_video_mcp_token.json` に保存されます（画像版とは別ファイル）。

## ツール

| ツール | 説明 |
|--------|------|
| `auth_status()` | 認証・プロジェクト・モデルの準備状況を確認する |
| `reauthorize()` | ブラウザで再ログインしてトークンを取り直す |
| `list_models()` | 利用可能なモデル・別名・アスペクト比・task 一覧を返す |
| `generate_video(prompt, out_path?, model?, aspect_ratio?, task?, input_images?, project_id?)` | テキスト/画像から動画を生成する |
| `edit_video(prompt, previous_interaction_id, out_path?, ...)` | 前の生成結果を踏まえてステートフルに編集する |

### 使い方の例

- **テキストから**: `generate_video(prompt="A marble rolling fast on a chain reaction style track, continuous smooth shot.")`
- **縦動画**: `generate_video(prompt="A futuristic city with neon lights...", aspect_ratio="9:16")`
- **画像から**: `generate_video(prompt="turn this into realistic footage, using the drawing only as a guide for movement", input_images=["/path/to/fish.jpg"], task="image_to_video")`
- **被写体参照（複数画像）**: `generate_video(prompt="A cat playfully batting at a ball of yarn.", input_images=["/path/cat.png", "/path/yarn.png"])`
- **ステートフル編集**: `generate_video(...)` の戻り値 `interaction_id` を `edit_video(prompt="空を夕焼けに", previous_interaction_id=<id>)` に渡す

> **画像→動画のコツ**: 高解像度の画像を使い、カメラの動き・被写体の動き・環境効果など
> 具体的な動きを指示すると良い結果になります。「動かして」のような曖昧な指示は避けてください。

### 出力先の注意（Amazon Quick）

`out_path` は Amazon Quick の許可フォルダ内（例 `/Users/tatsuya.naiki/PycharmProjects/...`）に
指定してください。既定の `~/gemini-videos` はエージェントのアクセス許可外でプレビューできません。

## 起動 / 登録

```
uvx --from git+https://github.com/tsukumonasu/gemini-video-mcp gemini-video-mcp
```

ローカルから:

```
cd /Users/tatsuya.naiki/PycharmProjects/gemini-video-mcp
uvx --from . gemini-video-mcp
```

## つまずきポイント

| 症状 | 原因・対処 |
|------|-----------|
| ログイン時「アクセスをブロック」 | OAuth同意画面が「外部」でテストユーザー未登録 → 自分を追加 |
| `redirect_uri_mismatch` | クライアントが「デスクトップアプリ」型でない → 作り直す |
| `403 PERMISSION_DENIED` | Generative Language API 未有効化 → セットアップ手順3を実施 |
| `reauthorize` が `Address already in use` | 前回の OAuth ローカルサーバーがポートを占有。`lsof -ti :<port> \| xargs kill -9` → `rm ~/.gemini_video_mcp_token.json` → 手動起動で再ログイン |

## 注記

Gemini Omni Flash は執筆時点で**プレビュー**です。`interactions` は Gemini Developer API の
比較的新しいサーフェスで、SDK の便利フィールド `interaction.output_video` は SDK 専用のため、
本 MCP は REST の `steps` 配列（`model_output` の `content` 内 `type=video`）から動画を取り出します。
API の仕様変更に追従できるよう、レスポンス抽出（`api.extract_videos`）は防御的に実装しています。

## ライセンス

MIT
