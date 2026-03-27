# Pipeline: tópicos frequentes → rascunho de artigo

## Estado no repositório (MVP)

- **`blogmarks-crew suggest-topics`** — Com `uv sync --extra crew` e chave LLM: lê `GET /api/bookmarks/{id}` (texto já guardado) e devolve **sugestões de slugs** em Markdown (sem persistir `topic_mentions`; uso humano ou cópia para tags).
- **`blogmarks-crew agents`** — Agrupa temas a partir de **lista de URLs** (sem conteúdo da página), útil para outlines rápidos.
- **`blogmarks-crew enrich`** — Grava URL, etiqueta e resume via tools REST (passo anterior ao pipeline de artigos).

## Etapas (desenho completo)

1. **Extração por bookmark** — Job ou CrewAI: input = `aiContent` + URL; output = lista de tópicos normalizados (slug). Persistir em tabela `topic_mentions` ou campos no item DynamoDB (lista + scores). *Ainda não há tabela dedicada no SQLite/Dynamo deste pacote.*
2. **Agregação** — Job diário: `topic_slug` → contagens 7d/30d e exemplos de `bookmark_id`.
3. **Rascunho** — Agente com prompt restrito: só cita trechos e links dos bookmarks do utilizador; saída Markdown em registo `draft_articles` com estado `pending_review`. **Revisão humana obrigatória** antes de publicar.

## Integração com este repositório

- **REST**: `GET /api/bookmarks/{id}` expõe conteúdo para agentes externos.
- **MCP**: `read_bookmark`, `search_bookmarks`, `semantic_search_bookmarks` (SQLite) alimentam o modelo antes de gerar rascunho.

## Riscos

- Alucinação e atribuição: exigir citação por frase; limitar comprimento.
- Direitos sobre texto agregado: opt-in explícito para geração de rascunhos.
