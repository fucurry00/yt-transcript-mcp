# YouTube Transcript MCP Server

[!NOTE]: このプロジェクトは初学者によりバイブコーディングされています。

YouTube動画のURLを貼るだけで、文字起こし（字幕）を自動取得するMCPサーバー。
Claude（Claude Desktop / Claude Code）からGemini、NotebookLMのように動画内容を扱えるようになる。

## 機能

- YouTube URL or Video ID → トランスクリプト取得（watch / shorts / embed / youtu.be 形式に対応）
- 手動字幕を優先、なければ自動生成字幕にフォールバック
- YAML frontmatter付きMarkdown形式で出力（Obsidianと互換）
- 多言語対応（デフォルト: 日本語 → 英語 → 韓国語）
- タイムスタンプ付き出力オプション

## 出力例

```markdown
---
title: "動画タイトル"
author: "チャンネル名"
url: https://www.youtube.com/watch?v=xxxxx
video_id: xxxxx
transcript_language: ja
transcript_source: auto-generated
upload_date: 2025-01-15
duration: 12m30s
---

## Description

動画の説明文...

## Transcript

こんにちは、今日は...
```

## セットアップ

### 1. 依存関係インストール

```bash
cd yt-transcript-mcp
uv pip install -e .

# メタデータ取得（タイトル・著者・日時・概要）に必要
uv pip install yt-dlp
```

### 2. Claude Desktop (claude.ai) で使う

`~/.claude/claude_desktop_config.json` に追加:

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

### 3. Claude Code で使う

```bash
# プロジェクトに追加
claude mcp add youtube -- python /path/to/yt-transcript-mcp/server.py

# またはグローバルに
claude mcp add --scope user youtube -- uv run --directory /path/to/yt-transcript-mcp python server.py
```

### 4. リモートサーバにデプロイする

Web版Claude（claude.ai）やChatGPTなどのWebクライアントから使うには、fly.io等の外部サーバにホストする必要がある。ローカルのstdioとは異なり、HTTPSで公開されたエンドポイントに接続する形になる。

| `MCP_TRANSPORT`    | プロトコル      | エンドポイント         | 用途                                       |
| ------------------ | --------------- | ---------------------- | ------------------------------------------ |
| (未設定 / `stdio`) | stdio           | -                      | Claude Desktop / Claude Code（デフォルト） |
| `streamable-http`  | Streamable HTTP | `https://your-app/mcp` | Web版Claude等のリモートクライアント        |

> **Note:** SSEトランスポートは2025年3月のMCP仕様更新で非推奨になりました。リモート接続にはStreamable HTTPを使用してください。リモートデプロイ時はHTTPS必須（fly.io等がエッジでTLS終端）。

#### fly.io へのデプロイ例

```bash
fly launch
fly secrets set API_KEY=your-secret MCP_TRANSPORT=streamable-http
fly deploy
```

`fly.toml` の設定例:

```toml
[env]
  MCP_TRANSPORT = "streamable-http"
  FASTMCP_HOST = "0.0.0.0"
  PORT = "8080"

[[services]]
  internal_port = 8080
  protocol = "tcp"

  [[services.ports]]
    handlers = ["tls", "http"]
    port = 443
```

#### 起動オプション

```bash
# Streamable HTTP モード + Bearer 認証
FASTMCP_HOST=0.0.0.0 API_KEY=your-secret MCP_TRANSPORT=streamable-http uv run python server.py
```

接続確認:

```bash
curl -H "Authorization: Bearer your-secret" https://your-app.fly.dev/mcp
```

## 使い方

設定後、Claudeに対してURLを貼るだけ:

```
この動画を要約して: https://www.youtube.com/watch?v=xxxxx
```

Claudeが自動的に `youtube_get_transcript` ツールを呼び出し、トランスクリプトを取得して要約してくれる。

### パラメータ

| パラメータ           | デフォルト           | 説明                                                                  |
| -------------------- | -------------------- | --------------------------------------------------------------------- |
| `url`                | (必須)               | YouTube URL or 動画ID（watch / shorts / embed / youtu.be / 11文字ID） |
| `languages`          | `["ja", "en", "ko"]` | 優先言語リスト                                                        |
| `include_timestamps` | `false`              | `[MM:SS]` タイムスタンプを含める                                      |
| `include_metadata`   | `true`               | タイトル・著者等のメタデータを含める                                  |

## アーキテクチャ

```
youtube-transcript-api でトランスクリプト取得
  （手動字幕 → 自動生成字幕 → 言語問わず最初の字幕の順で試行）

yt-dlp --dump-json でメタデータ取得
  （タイトル・著者・投稿日・再生時間・概要）

Markdown + YAML frontmatter で出力
  → Claude がコンテキストとして受け取る
```

### トランスクリプト取得に youtube-transcript-api を使う理由

- `yt-dlp` より大幅に軽量（動画ダウンロード機能なし）
- 字幕取得に特化したAPI
- Python ライブラリとして直接呼べる（subprocess不要）
- 手動字幕 / 自動生成字幕の切り替えが簡単

### メタデータ取得に yt-dlp を使う理由

- タイトル・著者・投稿日・再生時間・概要を一度に取得できる
- youtube-transcript-api はトランスクリプト専用でメタデータを提供しない

## トラブルシューティング

### 字幕が取れない

- 動画に字幕が設定されていない可能性がある
- `languages` パラメータで動画の言語を指定してみる
- プライベート動画・年齢制限動画は取得不可

### メタデータが "Unknown" になる

- `yt-dlp` がインストールされていない場合、メタデータは取得されない
- `uv pip install yt-dlp` で解決

## 今後の拡張案

- [ ] ツール docstring の軽量化: `Useful for:` や `Args:` の詳細説明を Claude Code skill に移管し、MCPツール側の description を最小限にする
- [ ] Whisper連携: 字幕がない動画 → 音声DL → Whisperで文字起こし
- [ ] バッチ処理: プレイリストURL → 複数動画の一括取得
- [ ] キャッシュ: 同じ動画の再取得を避ける（SQLiteなど）
- [ ] フレーム取得: 表やスライドなどの視覚情報 → より豊富な情報源 (ただ、使用トークンが増える可能性アリ)
- [ ] web版からの使用: デプロイ可能な設計
