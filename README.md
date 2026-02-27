# YouTube Transcript MCP Server

YouTube動画のURLを貼るだけで、文字起こし（字幕）を自動取得するMCPサーバー。
Claude（claude.ai / Claude Code）からGemini、NotebookLMのように動画内容を扱えるようになる。

## 機能

- YouTube URL or Video ID → トランスクリプト取得
- 手動字幕を優先、なければ自動生成字幕にフォールバック
- YAML frontmatter付きMarkdown形式で出力（Obsidianと互換）
- 多言語対応（デフォルト: 日本語 → 英語）
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

## Transcript

こんにちは、今日は...
```

## セットアップ

### 1. 依存関係インストール

```bash
cd youtube-transcript-mcp
uv pip install -e .

# yt-dlp もフォールバックとして入れておくと安心
uv pip install yt-dlp
```

### 2. Claude Desktop (claude.ai) で使う

`~/.claude/claude_desktop_config.json` に追加:

```json
{
  "mcpServers": {
    "youtube": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/youtube-transcript-mcp",
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
claude mcp add youtube -- python /path/to/youtube-transcript-mcp/server.py

# またはグローバルに
claude mcp add --scope user youtube -- python /path/to/youtube-transcript-mcp/server.py
```

## 使い方

設定後、Claudeに対してURLを貼るだけ:

```
この動画を要約して: https://www.youtube.com/watch?v=xxxxx
```

Claudeが自動的に `youtube_get_transcript` ツールを呼び出し、トランスクリプトを取得して要約してくれる。

### パラメータ

| パラメータ           | デフォルト     | 説明                                 |
| -------------------- | -------------- | ------------------------------------ |
| `url`                | (必須)         | YouTube URL or 動画ID                |
| `languages`          | `["ja", "en"]` | 優先言語リスト                       |
| `include_timestamps` | `false`        | `[MM:SS]` タイムスタンプを含める     |
| `include_metadata`   | `true`         | タイトル・著者等のメタデータを含める |

## アーキテクチャ

```
youtube-transcript-api (軽量・推奨)
         ↓ 失敗時
      yt-dlp (フォールバック)
         ↓
   Markdown + YAML frontmatter で出力
         ↓
   Claude がコンテキストとして受け取る
```

### なぜ youtube-transcript-api を優先するか

- `yt-dlp` より大幅に軽量（動画ダウンロード機能なし）
- 字幕取得に特化したAPI
- Python ライブラリとして直接呼べる（subprocess不要）
- 手動字幕 / 自動生成字幕の切り替えが簡単

### なぜ yt-dlp をフォールバックに残すか

- 一部の動画で youtube-transcript-api が取得できないケースがある
- メタデータ取得（タイトル、著者、再生時間等）に強い
- 将来的に音声ダウンロード → Whisperで文字起こし というパスも追加可能

## トラブルシューティング

### 字幕が取れない

- 動画に字幕が設定されていない可能性がある
- `languages` パラメータで動画の言語を指定してみる
- プライベート動画・年齢制限動画は取得不可

### メタデータが "Unknown" になる

- `yt-dlp` がインストールされていない場合、メタデータの取得精度が下がる
- `pip install yt-dlp` で改善

## 今後の拡張案

- [ ] Whisper連携: 字幕がない動画 → 音声DL → Whisperで文字起こし
- [ ] バッチ処理: プレイリストURL → 複数動画の一括取得
- [ ] キャッシュ: 同じ動画の再取得を避ける（SQLiteなど）
- [ ] Obsidian直接保存: 取得結果を指定ディレクトリに自動保存
