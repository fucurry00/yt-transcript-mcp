# Session Context

## User Prompts

### Prompt 1

Implement the following plan:

# Plan: SSE削除・streamable-http一本化

## Context

MCPのSSEトランスポートは2025年3月26日のMCP仕様更新で非推奨化された。
streamable-httpが現行標準であり、claude.ai・Claude Code双方が対応済み。
また、リモートデプロイ時はHTTPS必須（fly.io等がエッジでTLS終端し、内部はHTTP）。
SSEを残す理由がないため削除し、streamable-httpに一本化する。

## 変更ファイル

- `server.py`
- `README.md`

## server.py の変更

`__main__` ブロックから `elif transport == "sse":` の分岐を削除。

```python
# 変更前
if transport == "streamable-http":
    ...
elif transport == "sse":
    mcp.run(transport="sse")
else:
 ...

### Prompt 2

commit and push (in this repository, `gh` command line is allowd)

