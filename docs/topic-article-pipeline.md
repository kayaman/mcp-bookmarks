# Pipeline: tópicos frequentes → rascunho de artigo

Documentação de desenho (MVP). Implementação completa fica para quando houver texto extraído estável e métricas de custo LLM.

## Etapas

1. **Extração por bookmark** — Job ou CrewAI: input = `aiContent` + URL; output = lista de tópicos normalizados (slug). Persistir em tabela `topic_mentions` ou campos no item DynamoDB (lista + scores).
2. **Agregação** — Job diário: `topic_slug` → contagens 7d/30d e exemplos de `bookmark_id`.
3. **Rascunho** — Agente com prompt restrito: só cita trechos e links dos bookmarks do utilizador; saída Markdown em registo `draft_articles` com estado `pending_review`. **Revisão humana obrigatória** antes de publicar.

## Integração com este repositório

- **CrewAI**: `uv sync --extra crew` e `blogmarks-crew agents --urls-file ...` (tópicos a partir de URLs/domínios). Para texto completo, estender tools para ler SQLite/MCP ou chamar REST autenticada.
- **MCP**: `read_bookmark`, `search_bookmarks`, `semantic_search_bookmarks` (SQLite) alimentam o modelo antes de gerar rascunho.

## Riscos

- Alucinação e atribuição: exigir citação por frase; limitar comprimento.
- Direitos sobre texto agregado: opt-in explícito para geração de rascunhos.
