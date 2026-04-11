# topic-compiler

A sage-wiki-style LLM compiler that turns the bookmark corpus (SQLite or DynamoDB)
into an Astro content collection of interlinked topic articles.

See [`../../docs/knowledge-extraction-pipeline.md`](../../docs/knowledge-extraction-pipeline.md) for the full design.

## Run

```bash
export ANTHROPIC_API_KEY=sk-ant-...

# against local SQLite (mcp-bookmarks default)
cargo run --release -- compile \
  --source sqlite \
  --out ../../../blogmarks/wiki/src/content/topics

# against the live blogmarks DynamoDB corpus
AWS_DEFAULT_REGION=us-east-1 \
  cargo run --release -- compile \
    --source dynamodb \
    --out ../../../blogmarks/wiki/src/content/topics
```

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--source` | `sqlite` | `sqlite` or `dynamodb` |
| `--out` | — | Target directory (content collection path) |
| `--min-bookmarks` | `5` | Skip tags with fewer than N bookmarks |
| `--only-tag` | — | Restrict compilation to one tag slug |
| `--no-ontology` | `false` | Skip the ontology-extraction LLM pass |

## Env

- `ANTHROPIC_API_KEY` (required)
- `ANTHROPIC_MODEL` (default: `claude-opus-4-6`)
- `BOOKMARKS_DB_PATH` (for `--source sqlite`)
- `DYNAMODB_LINKS_TABLE` (default: `blogmarks-links`)
- `AWS_*` (standard AWS SDK credentials)
