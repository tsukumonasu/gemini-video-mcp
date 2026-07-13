# gemini-video-mcp

自分の **Google Cloud プロジェクト**の OAuth クライアントで**ブラウザログイン**し、
**Gemini API**（`generativelanguage.googleapis.com`）の `interactions` エンドポイント経由で
**Gemini Omni Flash**（`gemini-omni-flash-preview`）を叩いて**動画を生成・編集**する
**stdio な MCP サーバー**。`FastMCP` + stdio 構成、`uvx` 対応。

> 認証は **MCP 内蔵のブラウザ OAuth フロー**です。gcloud や google-auth には依存しません。
> 初回利用時にブラウザが開き、自分の Google アカウントでログイン → トークンを
> MCP 自身が `~/.gemini_video_mcp_token.json` に保存・自動更新します。

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

## セットアップ

Google Cloud Console（https://console.cloud.google.com/）で以下を行います。

### 1. OAuth 同意画面の設定（初回のみ）

1. 画面上部のプロジェクト選択で対象プロジェクトを選ぶ。
2. 「**API とサービス**」→「**OAuth 同意画面**」。
3. User Type を選択：
   - Google Workspace 組織内なら「**内部**」（テストユーザー登録が不要）
   - 個人 Gmail 等なら「**外部**」
4. アプリ名・ユーザーサポートメール・デベロッパー連絡先を入力。
5. スコープはここでは追加不要（そのまま進む）。
6. 「**外部**」を選んだ場合は「**テストユーザー**」に自分の Google アカウントを追加
   （これが無いとログイン時に「アクセスをブロック」されます）。

### 2. OAuth クライアントID を作成（デスクトップアプリ型）

1. 「**API とサービス**」→「**認証情報**」。
2. 上部「**+ 認証情報を作成**」→「**OAuth クライアント ID**」。
3. アプリケーションの種類：「**デスクトップアプリ**」を選択（← 重要）。
4. 名前（例: `gemini-video-mcp-desktop`）を入力して「作成」。
5. 表示される **クライアントID** と **クライアントシークレット** を控える。

> **なぜ「デスクトップアプリ」型か**: この種類だけが `http://127.0.0.1:ポート/...` への
> ループバックリダイレクトを標準で許可します。本 MCP は初回認可時に空きポートを自動選択して
> コールバックを受けるため、デスクトップアプリ型が必須です。

### 3. Generative Language API を有効化

1. 「**API とサービス**」→「**ライブラリ**」。
2. 「**Generative Language API**」（＝ `generativelanguage.googleapis.com`）を検索して有効化。
   - これが Gemini Omni Flash（動画生成）を叩く API です。

### 4. 環境変数を設定

| 変数 | 説明 |
|------|------|
| `GEMINI_VIDEO_CLIENT_ID` | 手順2で作成した OAuth クライアントID（必須） |
| `GEMINI_VIDEO_CLIENT_SECRET` | 手順2のクライアントシークレット（必須） |
| `GOOGLE_CLOUD_PROJECT` または `GEMINI_VIDEO_PROJECT_ID` | 使用するプロジェクトID（クォータ帰属。必須） |
| `GEMINI_VIDEO_MODEL` | 既定モデルの指定。**設定するとこのモデルが最優先**され、`generate_video` / `edit_video` の `model` 引数を渡しても無視してこの値を使う。未設定なら `model` 引数、無ければ `gemini-omni-flash-preview`。別名 `omni-flash` 等も可 |
| `GEMINI_VIDEO_ASPECT_RATIO` | 既定アスペクト比。既定 `16:9`（他に `9:16`） |
| `GEMINI_VIDEO_OUT_DIR` | `out_path` 省略時の出力先。既定 `~/gemini-videos` |
| `GEMINI_VIDEO_TIMEOUT` | API タイムアウト秒。既定 `600`（動画は時間がかかるため長め） |
| `GEMINI_VIDEO_REDIRECT_PORT` | 任意。初回認可のローカルポート（未指定なら空きポートを自動選択） |

### モデルの指定と優先順位

使用モデルは次の優先順で決まります。

1. **環境変数 `GEMINI_VIDEO_MODEL`（最優先）** — 設定されている場合、`generate_video` /
   `edit_video` の `model` 引数を**無視して常にこの値**を使います（`list_models()` の
   `forced_by_env: true` で確認可）。引数で別モデルを渡した場合は戻り値の `note` で通知します。
2. `generate_video` / `edit_video` の `model` 引数（環境変数が未設定のときのみ有効）。
3. どちらも無ければ既定の `gemini-omni-flash-preview`。

別名（エイリアス）も使えます: `omni-flash` / `gemini-omni-flash` / `omni-flash-preview` /
`omni-flash-latest` → いずれも `gemini-omni-flash-preview` に解決されます。

将来の GA / 上位版を見越した**候補モデル**（`gemini-omni-flash`, `gemini-omni-flash-001`,
`gemini-omni-pro-preview`, `gemini-omni-pro` など）も、`GEMINI_VIDEO_MODEL` に指定すれば
そのまま API に送信されます（現時点では未提供の可能性があります）。候補一覧は
`list_models()` の `candidate_models` で確認できます。

### 5. 初回認証

登録後に `generate_video`（または `reauthorize`）を実行すると **ブラウザが自動で開き**、
Google ログイン画面が表示されます。自分のアカウントでログイン・許可すると、トークンが
`~/.gemini_video_mcp_token.json` に保存され、以降は refresh token で自動更新されます。

## ツール

| ツール | 説明 |
|--------|------|
| `auth_status()` | 認証・プロジェクト・モデルの準備状況を確認する |
| `reauthorize()` | ブラウザで再ログインしてトークンを取り直す |
| `list_models()` | 利用可能なモデル・別名・アスペクト比・task 一覧を返す |
| `generate_video(prompt, out_path?, model?, aspect_ratio?, task?, input_images?, input_videos?, project_id?)` | テキスト/画像/動画から動画を生成する（`input_videos` 指定で動画→動画） |
| `edit_video(prompt, previous_interaction_id?, input_videos?, out_path?, ...)` | 動画を編集する。前の生成結果（`previous_interaction_id`）または手持ち動画（`input_videos`）を編集 |

### スキル（ツール）の説明一覧

MCP クライアント（Amazon Quick / Claude Desktop 等）に公開される 5 つのツール（スキル）と、その引数・戻り値の詳細です。

#### `auth_status()`
認証・プロジェクト・モデルの準備状況を確認する。

- **引数**: なし
- **戻り値**:
  - `ready`: 動画生成の準備が整っているか
  - `auth`: 認証状態（`valid` / `expired` / `needs_reauth`）
  - `client_configured`: OAuth クライアント（ID/SECRET）が設定済みか
  - `project_id`: 解決されたプロジェクトID
  - `model`: 現在の既定モデル
  - `available_models`: 選択可能なモデル一覧
  - `message`: 状態の説明と次のアクション

#### `reauthorize()`
ブラウザで Google に再ログインしてトークンを取り直す。

- ブラウザが自動で開き、Google のログイン画面が表示される。認可を完了すると新しいトークンが保存される。
- 認証エラー（401 等）や Refresh Token 失効時に実行する。
- **引数**: なし

#### `list_models()`
利用可能な動画生成モデルの一覧と別名を返す。

- **引数**: なし
- **戻り値**:
  - `models`: サポート対象モデル一覧
  - `candidate_models`: 将来の GA / 上位版を見越した候補モデル（現時点では未提供の可能性あり）
  - `aliases`: モデルの別名（エイリアス）
  - `default`: 既定モデル
  - `forced_by_env`: 環境変数 `GEMINI_VIDEO_MODEL` によるモデル固定が有効か
  - `aspect_ratios`: 有効なアスペクト比（`16:9` / `9:16`）
  - `tasks`: 有効な task 種別
  - `note`: モデル選択・優先順位の補足

#### `generate_video(prompt, out_path?, model?, aspect_ratio?, task?, input_images?, input_videos?, project_id?)`
Gemini Omni Flash で動画を生成する。テキストのみ（text_to_video）、参照画像あり（image_to_video / reference_to_video）、動画→動画（edit）に対応。音声付きの MP4 が生成される。

- **引数**:
  - `prompt`（必須）: 生成したい動画の説明。`input_images` 指定時はその画像をどう使うかの指示。
  - `out_path`: 出力先ファイルパス（.mp4）。省略時は `GEMINI_VIDEO_OUT_DIR` に自動命名で保存。※ Amazon Quick でプレビューするには許可フォルダ内のパスを指定すること。
  - `model`: 使用モデル。別名 `omni-flash` 可。環境変数 `GEMINI_VIDEO_MODEL` 設定時はこの引数は無視。
  - `aspect_ratio`: アスペクト比。`16:9`（既定・横）または `9:16`（縦）。
  - `task`: 動作の明示指定（任意）。`text_to_video` / `image_to_video` / `reference_to_video` / `edit`。未指定ならモデルがプロンプト・入力から推測。
  - `input_images`: 参照画像のパス配列（任意）。1枚なら画像→動画、複数なら被写体参照など。高解像度画像＋具体的な動きの指示を推奨。
  - `input_videos`: 入力動画のパス配列（任意）。動画→動画（編集）に使う。※ 複数動画の同時参照は非対応（通常1本）。
  - `project_id`: プロジェクトID上書き（任意・クォータ帰属用）。
- **戻り値**: `success` / `videos` / `out_path` / `count` / `interaction_id` / `model` / `aspect_ratio` / `project_id`
  - `interaction_id` は `edit_video` の `previous_interaction_id` に渡してステートフル編集できる。

#### `edit_video(prompt, previous_interaction_id?, out_path?, model?, aspect_ratio?, input_images?, input_videos?, project_id?)`
動画を編集する。編集対象は次の2通りのいずれかで指定する。

1. **ステートフル編集**（前の生成結果を継続編集）: `previous_interaction_id` に `generate_video` / `edit_video` が返した `interaction_id` を渡すと、前の動画を再アップロードせずに編集が適用される。
2. **手持ち動画の編集**（動画→動画）: `input_videos` に手元の動画ファイルパスを渡すと、その動画を素材に編集した動画を生成する。

いずれの場合も task は `edit` として送信される。`previous_interaction_id` と `input_videos` の少なくとも一方を指定すること。

- **引数**:
  - `prompt`（必須）: 変更したい内容の説明。編集はシンプルなプロンプトが有効。「他はそのまま（Keep everything else the same）」を添えると一貫性を保ちやすい。
  - `previous_interaction_id`: 前回の生成/編集で返った `interaction_id`（ステートフル編集時）。
  - `out_path`: 出力先ファイルパス（.mp4）。省略時は `GEMINI_VIDEO_OUT_DIR` に自動命名で保存。
  - `model`: 使用モデル（任意）。環境変数 `GEMINI_VIDEO_MODEL` 設定時は無視。
  - `aspect_ratio`: アスペクト比（任意・`16:9` / `9:16`）。省略時は前の設定を引き継ぐ。
  - `input_images`: 追加の参照画像パス配列（任意）。
  - `input_videos`: 編集対象の入力動画パス配列（任意・動画→動画）。※ 複数動画の同時参照は非対応（通常1本）。
  - `project_id`: プロジェクトID上書き（任意）。
- **戻り値**: `success` / `videos` / `out_path` / `count` / `interaction_id` / `previous_interaction_id` / `model` / `project_id`

### 使い方の例

- **テキストから**: `generate_video(prompt="A marble rolling fast on a chain reaction style track, continuous smooth shot.")`
- **縦動画**: `generate_video(prompt="A futuristic city with neon lights...", aspect_ratio="9:16")`
- **画像から**: `generate_video(prompt="turn this into realistic footage, using the drawing only as a guide for movement", input_images=["/path/to/fish.jpg"], task="image_to_video")`
- **被写体参照（複数画像）**: `generate_video(prompt="A cat playfully batting at a ball of yarn.", input_images=["/path/cat.png", "/path/yarn.png"])`
- **動画から動画（手持ち動画の編集）**: `generate_video(prompt="When the person touches the mirror, make the mirror ripple like liquid. Keep everything else the same.", input_videos=["/path/to/source.mp4"], task="edit")`
  - あるいは `edit_video(prompt="この動画をアニメ調にして。他はそのまま", input_videos=["/path/to/source.mp4"])`
- **ステートフル編集（モデル生成動画の継続編集）**: `generate_video(...)` の戻り値 `interaction_id` を `edit_video(prompt="空を夕焼けに", previous_interaction_id=<id>)` に渡す

> **画像→動画のコツ**: 高解像度の画像を使い、カメラの動き・被写体の動き・環境効果など
> 具体的な動きを指示すると良い結果になります。「動かして」のような曖昧な指示は避けてください。

> **動画→動画（動画編集）のコツと制約**:
> - `input_videos` に編集したい動画を1本渡します（`task="edit"` 推奨）。編集はシンプルなプロンプトが
>   最も効果的です。特定の要素だけ変えたいときは「他はそのまま（Keep everything else the same）」を
>   添えると一貫性を保てます（例: 「電話を見えなくして。他はそのまま」）。
> - **複数動画の同時参照は非対応**です。1本のみ渡してください。
> - **リージョン制約**: EEA（欧州経済領域）・スイス・英国では**アップロードした動画の編集は非対応**です
>   （`previous_interaction_id` を使ったモデル生成動画の編集は可能）。
> - 音声リファレンスの入力、動画の延長・フレーム間補間（最初と最後のフレームから中間を生成）、
>   YouTube 動画の入力ソース利用は非対応です。
> - 大きな動画は base64 でそのまま送信するためペイロード上限に達する可能性があります
>   （公式では 4MB 超は Files API 経由が推奨。現状の本 MCP は base64 直接送信）。

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

## JSON での登録例

MCP クライアント（Amazon Quick / Claude Desktop 等）の設定ファイルに、以下のように
`mcpServers` エントリを追加します。`env` の値は自分の OAuth クライアント・プロジェクトに置き換えてください。

### ローカルのソースから起動する場合

```json
{
  "mcpServers": {
    "gemini-video": {
      "command": "uvx",
      "args": [
        "--from",
        "/Users/tatsuya.naiki/PycharmProjects/gemini-video-mcp",
        "gemini-video-mcp"
      ],
      "env": {
        "GEMINI_VIDEO_CLIENT_ID": "xxxxxxxx.apps.googleusercontent.com",
        "GEMINI_VIDEO_CLIENT_SECRET": "GOCSPX-xxxxxxxx",
        "GOOGLE_CLOUD_PROJECT": "your-gcp-project-id",
        "GEMINI_VIDEO_MODEL": "gemini-omni-flash-preview",
        "GEMINI_VIDEO_ASPECT_RATIO": "16:9",
        "GEMINI_VIDEO_OUT_DIR": "/Users/tatsuya.naiki/PycharmProjects/gemini-videos"
      }
    }
  }
}
```

### GitHub から直接起動する場合

```json
{
  "mcpServers": {
    "gemini-video": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/tsukumonasu/gemini-video-mcp",
        "gemini-video-mcp"
      ],
      "env": {
        "GEMINI_VIDEO_CLIENT_ID": "xxxxxxxx.apps.googleusercontent.com",
        "GEMINI_VIDEO_CLIENT_SECRET": "GOCSPX-xxxxxxxx",
        "GOOGLE_CLOUD_PROJECT": "your-gcp-project-id",
        "GEMINI_VIDEO_MODEL": "gemini-omni-flash-preview"
      }
    }
  }
}
```

> **補足**
> - `env` に必要なのは最低限 `GEMINI_VIDEO_CLIENT_ID` / `GEMINI_VIDEO_CLIENT_SECRET` /
>   `GOOGLE_CLOUD_PROJECT`（または `GEMINI_VIDEO_PROJECT_ID`）の3つ。残りは任意（既定値あり）。
> - `GEMINI_VIDEO_MODEL` を指定すると、そのモデルが**最優先**で使われます（`generate_video` /
>   `edit_video` の `model` 引数より優先）。上の例では既定と同じ `gemini-omni-flash-preview` を
>   明示していますが、別名 `omni-flash` や将来の候補モデルも指定できます。固定したくなければ省略可（既定が使われます）。
> - `GEMINI_VIDEO_OUT_DIR` は Amazon Quick の許可フォルダ内（例 `/Users/tatsuya.naiki/PycharmProjects/...`）に
>   すると、生成した動画をそのままプレビューできます。既定の `~/gemini-videos` は許可フォルダ外のため非推奨。
> - シークレットを設定ファイルに直書きしたくない場合は、シェルの環境変数として export しておき
>   `env` から該当キーを省くこともできます（クライアントが親プロセスの環境変数を引き継ぐ場合）。

## つまずきポイント

| 症状 | 原因・対処 |
|------|-----------|
| ログイン時「アクセスをブロック」 | OAuth同意画面が「外部」でテストユーザー未登録 → セットアップ手順1-6で自分を追加 |
| `redirect_uri_mismatch` | クライアントが「デスクトップアプリ」型でない → セットアップ手順2で作り直す |
| `403 PERMISSION_DENIED` | Generative Language API 未有効化 → セットアップ手順3を実施 |
| `reauthorize` が `Address already in use` | 前回の OAuth ローカルサーバーがポートを占有。占有ポートを解放（`lsof -ti :<port> \| xargs kill -9`）→ `rm ~/.gemini_video_mcp_token.json` → 手動起動で再ログイン |

## 注記

Gemini Omni Flash は執筆時点で**プレビュー**です。`interactions` は Gemini Developer API の
比較的新しいサーフェスで、SDK の便利フィールド `interaction.output_video` は SDK 専用のため、
本 MCP は REST の `steps` 配列（`model_output` の `content` 内 `type=video`）から動画を取り出します。
API の仕様変更に追従できるよう、レスポンス抽出（`api.extract_videos`）は防御的に実装しています。

## ライセンス

MIT
