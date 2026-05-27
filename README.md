# YouTube Transcript MCP Server

> [!NOTE]
> このプロジェクトは初学者によりバイブコーディングされています。

YouTube の URL または動画 ID から、字幕・メタデータを取得する MCP サーバーです。
Claude Desktop / Claude Code などの MCP クライアントから、動画の要約、翻訳、ノート化、内容確認に使えます。

## 主な機能

- YouTube URL / 動画 ID からトランスクリプトを取得
- `watch` / `shorts` / `embed` / `live` / `v` / `youtu.be` / 11 文字の動画 ID に対応
- `m.youtube.com` / `music.youtube.com` などの YouTube サブドメインに対応
- 手動字幕を優先し、なければ自動生成字幕にフォールバック
- 優先言語を指定可能（デフォルト: 日本語 → 英語 → 韓国語）
- Markdown 形式で出力
- タイムスタンプ付き出力に対応
- `yt-dlp` が使える環境ではタイトル、投稿者、投稿日、概要などのメタデータも取得
- 取得済みトランスクリプトをローカルキャッシュ（180 日）に保存
- stdio と Streamable HTTP の 2 つの transport に対応

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

MCP クライアントで、YouTube URL を含む依頼をします。

```text
この動画を要約して: https://www.youtube.com/watch?v=xxxxx
```

クライアントが必要に応じて `youtube_get_transcript` を呼び出し、取得したトランスクリプトをもとに回答します。

### `youtube_get_transcript`

| パラメータ | デフォルト | 説明 |
| --- | --- | --- |
| `url` | 必須 | YouTube URL または 11 文字の動画 ID |
| `languages` | `["ja", "en", "ko"]` | 優先する字幕言語のリスト |
| `include_timestamps` | `false` | `true` にすると各行に `[MM:SS]` を付ける |
| `include_metadata` | `true` | `true` にすると `yt-dlp` でタイトル、投稿者などを取得する。`false` の場合は `yt-dlp` を呼ばず、最小限のメタデータだけを出力する |
| `start_seconds` | `null` | 取得する字幕範囲の開始秒。未指定の場合は冒頭から取得する |
| `end_seconds` | `null` | 取得する字幕範囲の終了秒。未指定の場合は末尾まで取得する |
| `max_chars` | `200000` | 最終 Markdown 出力の最大文字数。`10000` から `200000` まで指定可能 |

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

## Description

動画の説明文...

## Transcript

こんにちは、今日は...
```

長い動画で出力が `max_chars` を超える場合、字幕行の途中では切らず、末尾に続き取得用の `## Continuation` が追加されます。

```markdown
## Continuation

- Transcript truncated: true
- next_start_seconds: 1234.56
- max_chars: 200000
- Suggested next call: youtube_get_transcript(url="https://www.youtube.com/watch?v=xxxxx", start_seconds=1234.56, max_chars=200000)
```

続きだけ取得したい場合は、`start_seconds` に `next_start_seconds` の値を指定します。特定区間だけを扱いたい場合は、`start_seconds` と `end_seconds` を組み合わせます。

### `youtube_get_video_info`

動画の概要だけ確認したい場合に使います。`yt-dlp` が利用できる環境では、以下のような JSON を返します。

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

`yt-dlp` が見つからない、タイムアウトする、YouTube 側で取得に失敗するなどの場合は、`title` と `author` が `Unknown` になり、`metadata_error` に理由が入ります。

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

## アーキテクチャ

```text
YouTube URL / video ID
  -> video_id を抽出
  -> youtube-transcript-api で字幕取得
       手動字幕 -> 自動生成字幕 -> 利用可能な最初の字幕
  -> 必要に応じて yt-dlp --dump-json でメタデータ取得
  -> Markdown または JSON で返却
```

### `youtube-transcript-api` を使う理由

- 字幕取得に特化していて軽量
- 動画本体をダウンロードしない
- Python ライブラリとして直接呼び出せる
- 手動字幕 / 自動生成字幕の選択がしやすい

### `yt-dlp` を使う理由

- タイトル、投稿者、投稿日、再生時間、概要をまとめて取得できる
- `youtube-transcript-api` は字幕専用で、動画メタデータを提供しない

## トラブルシューティング

### 字幕が取れない

- 動画に字幕がない可能性があります
- `languages` に動画の字幕言語を指定してください
- プライベート動画、年齢制限動画、地域制限動画は取得できない場合があります

### メタデータが `Unknown` になる

- `yt-dlp` がインストールされていない可能性があります
- `uv sync --extra ytdlp` を実行してください
- `youtube_get_video_info` の JSON に `metadata_error` がある場合は、`type` と `stderr` を確認してください
- YouTube 側の制限や一時的な取得失敗でも `Unknown` になることがあります

### 出力が長すぎる

- 出力は `max_chars` で指定した文字数までに抑えられます（最大 200,000 文字）
- 長すぎる場合は `## Continuation` の `next_start_seconds` を使って続きを取得してください
- 必要な区間が分かっている場合は、`start_seconds` / `end_seconds` で範囲指定してください

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

- [ ] ツール docstring の軽量化
- [ ] 字幕がない動画向けの Whisper 連携
- [ ] プレイリスト URL の一括処理
- [ ] キャッシュの明示的な削除・更新オプション
- [ ] スライドや表などを扱うためのフレーム取得
