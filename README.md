# YouTube Transcript MCP Server

> [!NOTE]
> このプロジェクトは初学者によりバイブコーディングされています。

YouTube の URL または動画 ID から、字幕・メタデータを取得する MCP サーバーです。
Claude Desktop / Claude Code などから、動画の要約、翻訳、ノート化、内容確認に使えます。

## 主な機能

- YouTube URL / 11 文字の動画 ID に対応
- 手動字幕を優先し、なければ自動生成字幕にフォールバック
- 字幕言語は `ja` / `en` / `ko` を優先（自動選択）、タイムスタンプ付与は任意指定
- Markdown 形式で字幕を出力
- `yt-dlp` が使える場合はタイトル、投稿者、投稿日なども取得
- ローカルキャッシュ、stdio、Streamable HTTP に対応

## 提供ツール

| ツール | 用途 |
| --- | --- |
| `youtube_get_transcript` | 字幕を取得し、Markdown 形式で返す |
| `youtube_get_video_info` | 字幕を取得せず、動画メタデータだけを JSON で返す |

## セットアップ

### 前提

- Python 3.10 以上
- [uv](https://docs.astral.sh/uv/) が利用できること

### インストール

```bash
cd yt-transcript-mcp
uv sync --extra ytdlp
```

`yt-dlp` はメタデータ取得用です。字幕だけ取得できればよい場合は、次の最小構成でも動きます。

```bash
uv sync
```

## クライアント設定

### Claude Desktop

`~/.claude/claude_desktop_config.json` に追加します。

```json
{
  "mcpServers": {
    "yt-transcript-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/yt-transcript-mcp",
        "python",
        "server.py"
      ]
    }
  }
}
```

### Claude Code

プロジェクト単位で追加する場合:

```bash
claude mcp add youtube -- uv run --directory /path/to/yt-transcript-mcp python server.py
```

ユーザー全体に追加する場合:

```bash
claude mcp add --scope user youtube -- uv run --directory /path/to/yt-transcript-mcp python server.py
```

## 使い方

MCP クライアントで YouTube URL を含む依頼をします。

```text
この動画を要約して: https://www.youtube.com/watch?v=xxxxx
```

### `youtube_get_transcript`

| パラメータ | デフォルト | 説明 |
| --- | --- | --- |
| `url` | 必須 | YouTube URL または 11 文字の動画 ID |
| `include_timestamps` | `false` | `true` にすると各行に `[MM:SS]` を付ける |

字幕言語は `ja` / `en` / `ko` の順で自動選択し、見つからない場合は利用可能な字幕にフォールバックします。メタデータ（タイトル・投稿者など）は常に付与されます。

出力例:

```markdown
# 動画タイトル

- Author: チャンネル名
- URL: https://www.youtube.com/watch?v=xxxxx
- Video ID: xxxxx
- Transcript language: ja
- Transcript source: auto-generated
- Upload date: 2025-01-15
- Duration: 12m30s

## Transcript

こんにちは、今日は...
```

非常に長い動画（文字起こしが約 200,000 文字を超える場合）は、先頭約 200,000 文字までで打ち切られ、末尾にその旨の注記が付きます。このツールは先頭部分のみを返します。

> [!NOTE]
> 長尺動画の任意区間や続きを取得する必要が出た場合は、区間指定の専用ツール（例: `youtube_get_transcript_segment(url, start_seconds)`）の追加を検討します。現時点では未実装です。

### `youtube_get_video_info`

動画の概要だけ確認したい場合に使います。

```json
{
  "video_id": "xxxxx",
  "title": "動画タイトル",
  "author": "チャンネル名",
  "channel_url": "https://www.youtube.com/...",
  "upload_date": "20250115",
  "duration_seconds": 750,
  "description": "動画の説明文...",
  "view_count": 12345
}
```

`yt-dlp` が使えない場合や取得に失敗した場合は、`metadata_error` に理由が入ります。

## キャッシュ

トランスクリプトはローカルにキャッシュされます。

- デフォルト保存先: `yt-transcript-mcp/.transcript_cache/`
- 有効期間: 180 日
- 保存先変更: `CACHE_DIR=/path/to/cache`

例:

```bash
CACHE_DIR=/tmp/yt-transcript-cache uv run python server.py
```

## Streamable HTTP で起動する

stdio がデフォルトです。Web クライアントやリモート環境から接続したい場合は、Streamable HTTP で起動します。

```bash
MCP_TRANSPORT=streamable-http API_KEY=your-secret PORT=8000 uv run python server.py
```

| 環境変数 | デフォルト | 説明 |
| --- | --- | --- |
| `MCP_TRANSPORT` | `stdio` | `streamable-http` を指定すると HTTP サーバーとして起動 |
| `API_KEY` | 未設定 | 設定すると Bearer token 認証を有効化 |
| `PORT` | `8000` | HTTP サーバーのポート |
| `CACHE_DIR` | `.transcript_cache` | キャッシュ保存先 |

認証を有効にした場合:

```bash
curl -H "Authorization: Bearer your-secret" http://localhost:8000/mcp
```

> [!NOTE]
> リモート公開する場合は HTTPS 終端と認証を必ず用意してください。
> SSE transport は MCP 仕様更新により非推奨のため、このサーバーでは Streamable HTTP を使います。

## トラブルシューティング

### 字幕が取れない

- 動画に字幕がない可能性があります
- 字幕言語は `ja` / `en` / `ko` を優先し、なければ利用可能な字幕に自動フォールバックします
- プライベート動画、年齢制限動画、地域制限動画は取得できない場合があります

### メタデータが `Unknown` になる

- `yt-dlp` がインストールされていない可能性があります
- `uv sync --extra ytdlp` を実行してください
- `youtube_get_video_info` の JSON に `metadata_error` がある場合は、`type` と `stderr` を確認してください
- YouTube 側の制限や一時的な取得失敗でも `Unknown` になることがあります

### 出力が長すぎる

- 出力は約 200,000 文字までに抑えられ、超過分は先頭で打ち切られます（末尾に注記が付きます）
- 約 4 時間を超えるような超長尺動画では後半が取得できません

## 開発

```bash
uv sync --extra ytdlp --dev
uv run python -m unittest discover -s tests
uv run poe check
```

| コマンド | 内容 |
| --- | --- |
| `uv run python -m unittest discover -s tests` | テストを実行 |
| `uv run poe format` | Ruff でフォーマット |
| `uv run poe lint` | Ruff の lint を `--fix` 付きで実行 |
| `uv run poe type-check` | mypy を実行 |
| `uv run poe check` | format / lint / type-check をまとめて実行 |

## 今後の拡張案

- [ ] 字幕がない動画向けの Whisper 連携
- [ ] プレイリスト URL の一括処理
- [ ] キャッシュの明示的な削除・更新オプション
- [ ] スライドや表などを扱うためのフレーム取得
