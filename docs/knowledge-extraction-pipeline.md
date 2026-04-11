# Knowledge Extraction Pipeline

Status: v0.1 scaffolding on `claude/knowledge-extraction-pipeline-KCgFV`

Related repos:
- `kayaman/mcp-bookmarks` — this Rust compiler (below)
- `kayaman/blogmarks` — `wiki/` Astro content collection + site

## Goal

Turn the `blogmarks-links` / SQLite bookmark corpus into a sage-wiki-style
knowledge base: one Markdown article per topic, interlinked via `[[wikilinks]]`,
with typed ontology edges between topics. Published as an Astro content
collection in the companion `blogmarks` repo.

## Design influences

Combines the strengths of two OSS tools:

| Stage             | From          | What we reuse                                       |
|-------------------|---------------|-----------------------------------------------------|
| Source ingestion  | (existing)    | The CrewAI pipeline already fills `aiContent`       |
| Topic discovery   | sage-wiki     | LLM-picked topics, grouped by the tag taxonomy      |
| Article synthesis | sage-wiki     | One topic → one article with wikilinks              |
| Ontology          | sage-wiki     | 8 typed relation kinds                              |
| TS/Astro output   | fieldtheory   | Inspired export shape; Astro content collection UX  |

## Pipeline

```
  blogmarks-links           ~/.mcp-bookmarks/bookmarks.db
  (DynamoDB)                (SQLite)
        \                   /
         \                 /
          v               v
     source::load (aiContent, aiSummary, aiTags)
                   │
                   v
     cluster::discover_topics   (per-tag LLM pass)
                   │
                   v
     synthesize::compile_all    (per-topic LLM pass, prompt-cached corpus)
                   │
                   v
     ontology::extract          (single LLM pass over all topic summaries)
                   │
                   v
     wikilink::rewrite          (auto-link mentions of known slugs)
                   │
                   v
     render::write_collection   (Astro markdown with YAML frontmatter)
                   │
                   v
         wiki/src/content/topics/*.md
```

## Running

```bash
export ANTHROPIC_API_KEY=...
cd rust/topic-compiler

# against the live DynamoDB corpus
DYNAMODB_MODE=true AWS_DEFAULT_REGION=us-east-1 \
  cargo run --release -- compile \
    --source dynamodb \
    --out ../../../blogmarks/wiki/src/content/topics

# against the local SQLite bookmark DB
cargo run --release -- compile \
  --source sqlite \
  --out ../../../blogmarks/wiki/src/content/topics
```

## Non-goals for v0.1

- Rebuilding ontology from disk (`graph` subcommand is stubbed)
- Incremental compilation (each run is a full rebuild)
- Modifying `src/mcp_bookmarks/server.py` — the Rust binary is self-contained
  so the MCP `compile_knowledge_base` tool can be wired in a follow-up.
