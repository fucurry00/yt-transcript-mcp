# YouTube Transcript MCP Server

> [!NOTE]
> このプロジェクトは初学者によりバイブコーディングされています。

YouTube の URL または動画 ID から、字幕・メタデータを取得する MCP サーバーです。
Claude Desktop / OpenAI Codex などから、動画の要約、翻訳、ノート化、内容確認に使えます。

## 主な機能

- YouTube URL / 11 文字の動画 ID に対応
- 手動字幕を優先し、なければ自動生成字幕にフォールバック
- 字幕言語は `ja` / `en` / `ko` を優先（自動選択）、タイムスタンプ付与は任意指定
- Markdown 形式で字幕を出力
- タイトル、投稿者、投稿日などのメタデータも取得
- ローカルキャッシュに対応（stdio 接続）

## 提供ツール

| ツール | 用途 |
| --- | --- |
| `youtube_get_transcript` | 字幕を取得し、Markdown 形式で返す |
| `youtube_get_video_info` | 動画メタデータのみを JSON で返す |
| `youtube_get_frame` | 指定時刻のフレームを 1 枚、画像で返す |

## セットアップ

### 前提

- Python 3.10 以上
- [uv](https://docs.astral.sh/uv/) が利用できること

### インストール

```bash
cd yt-transcript-mcp
uv sync
```

これだけで `yt-dlp` を含む依存が揃います。システムに `yt-dlp` を別途インストールする必要はありません（`uv run` 配下では venv 内の `yt-dlp` が PATH 上のものより優先されます）。

> [!IMPORTANT]
> `yt-dlp` は YouTube 側の仕様変更に追従し続けることで動いているツールです。`uv.lock` に固定したまま放置するといずれメタデータ取得が壊れます。壊れたら（あるいは定期的に）次を実行してください。
>
> ```bash
> uv lock --upgrade-package yt-dlp && uv sync
> ```

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

### OpenAI Codex

`~/.codex/config.toml` に追加します。

```toml
[mcp_servers.yt-transcript-mcp]
command = "uv"
args = ["run", "--directory", "/path/to/yt-transcript-mcp", "python", "server.py"]
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

タイムスタンプは、インライン出力ではデフォルト OFF（`include_timestamps=true` で ON）ですが、書き出される `.md` ファイルは常に `[MM:SS]` 付きです。

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
- Transcript file: /abs/path/.transcript_cache/xxxxx.md

## Transcript

こんにちは、今日は...
```

`Transcript file` はタイムスタンプ付き全文（`[MM:SS] テキスト`）を書き出した `.md` の絶対パスです。stdio 接続でクライアントとサーバーが同一マシンにいる前提で、続きの確認・特定語の検索・区間の抜き出しは、このファイルに対してクライアント側の `Read` / `Grep` を使えば専用ツールなしで完結します（`Grep` の結果に `[MM:SS]` が含まれるので、そのまま `youtube_get_frame` の `timestamp` に渡せます）。

非常に長い動画（文字起こしが約 200,000 文字を超える場合）は、先頭約 200,000 文字までで打ち切られ、末尾に注記が付きます。全文は上記 `.md` にあるので、続きはそのファイルを `Read` させます。

### `youtube_get_frame`

字幕だけでは分からない場面（スライド、画面上のコード、図表、字幕なしのデモ）で使います。

| パラメータ | デフォルト | 説明 |
| --- | --- | --- |
| `url` | 必須 | YouTube URL または 11 文字の動画 ID |
| `timestamp` | 必須 | 秒（`90`）、`MM:SS`（`01:30`）、`HH:MM:SS`（`00:01:30`） |

`youtube_get_transcript` に `include_timestamps=true` を渡すと各行に `[MM:SS]` が付くので、
その値をそのまま `timestamp` に渡せます。

動画全体はダウンロードしません。`yt-dlp` でストリーム URL を解決し、`ffmpeg` が HTTP range で
必要な範囲だけ読んで 1 フレームを JPEG で取得します。116 分の動画の任意の地点でも数秒です。
解像度は 720p 上限です（360p では画面上のコードが判読できず、720p なら本用途には十分なため）。

取得した JPEG は `.transcript_cache/{video_id}_frame_{timestamp}.jpg` に保存し、画像とあわせて
その絶対パス（`Frame saved to: ...`）を返します。同じ地点の再取得はディスクから返るので、
コストの高い `yt-dlp` 解決 + `ffmpeg` デコードをスキップします。パスがあるので、クライアント側で
アセットとしてコピー・埋め込み・参照もできます。

> [!NOTE]
> 画像はクライアント UI 上では折りたたまれて表示さることがありますが、モデルには渡っています。

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

トランスクリプトとフレームはローカルにキャッシュされます。

- デフォルト保存先: `yt-transcript-mcp/.transcript_cache/`
  - `{video_id}_{langs}.json`: 生の字幕エントリ（TTL 180 日）
  - `{video_id}.md`: タイムスタンプ付き全文（`Read`/`Grep` 用）
  - `{video_id}_frame_{timestamp}.jpg`: 取得済みフレーム
- 有効期間: 180 日（`.json` のみ判定。`.md` / `.jpg` は上書き・再生成されます）
- 保存先変更: `CACHE_DIR=/path/to/cache`

例:

```bash
CACHE_DIR=/tmp/yt-transcript-cache uv run python server.py
```

## トラブルシューティング

### 字幕が取れない

- 動画に字幕がない可能性があります
- 字幕言語は `ja` / `en` / `ko` を優先し、なければ利用可能な字幕に自動フォールバックします
- プライベート動画、年齢制限動画、地域制限動画は取得できない場合があります

### メタデータが `Unknown` になる

- `youtube_get_video_info` の JSON に `metadata_error` がある場合は、`type` と `stderr` を確認してください
- `type` が `yt_dlp_not_found` なら依存が入っていません。`uv sync` を実行してください
- それ以外（`yt_dlp_failed` など）で `stderr` が YouTube の仕様変更を示している場合は、`yt-dlp` が古い可能性があります。`uv lock --upgrade-package yt-dlp && uv sync` で更新してください
- YouTube 側の制限や一時的な取得失敗でも `Unknown` になることがあります

### 出力が長すぎる

- インラインの出力は約 200,000 文字までに抑えられ、超過分は先頭で打ち切られます（末尾に注記が付きます）
- ただし全文は `Transcript file` の `.md` に書き出されているので、後半は打ち切りとは関係なくそのファイルを `Read`/`Grep` で参照できます

## 開発

```bash
uv sync --dev
uv run python -m unittest discover -s tests
uv run ruff format . && uv run ruff check --fix . && uv run mypy .
```

| コマンド | 内容 |
| --- | --- |
| `uv run python -m unittest discover -s tests` | テストを実行 |
| `uv run ruff format .` | Ruff でフォーマット |
| `uv run ruff check --fix .` | Ruff の lint を `--fix` 付きで実行 |
| `uv run mypy .` | mypy を実行 |
| `uv lock --upgrade-package yt-dlp && uv sync` | `yt-dlp` を最新に更新して lock を書き換える |

### 依存の方針

**すべて uv に一本化し、システムへの別途インストールを前提にしない。** 他者の環境へ移したときに「私の環境では動く」を起こさないための方針です。

- **`youtube-transcript-api`** — 純粋な Python 依存。`uv.lock` で固定。
- **`yt-dlp`** — 必須依存。システム版（brew など）があってもそちらは使いません。ただし YouTube の変更に追従し続けることで動くツールなので、**固定しっぱなしにしないこと**が重要です（`uv lock --upgrade-package yt-dlp && uv sync`）。ここだけは「固定＝安全」が成り立ちません。
- **`imageio-ffmpeg`** — `youtube_get_frame` のフレーム抽出に使用。静的バイナリを同梱しており、brew なしで uv 管理下に置けます。ffmpeg 単体のみで `ffprobe` は付きませんが、フレーム抽出は ffmpeg だけで完結するため問題ありません。

### ツール description は短く保つ

MCP クライアントはツール発見時に `description` と引数スキーマをそのまま会話コンテキストへ流し込みます。
説明が長いほど有効コンテキストを圧迫するため、`@mcp.tool(description=...)` には「何を返すか」と「引数の意味」だけを書き、
仕様の詳細（出力例、打ち切り、フォールバック順など）はこの README 側に置いてください。

## 今後の拡張案

- [ ] 字幕がない動画向けの Whisper 連携
- [ ] プレイリスト URL の一括処理
- [ ] キャッシュの明示的な削除・更新オプション
