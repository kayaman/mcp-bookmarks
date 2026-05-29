-- Ilhas de conhecimento: um vetor store lógico por usuário/time (island).
-- Rode após provisionar o RDS (Terraform) e conectar como user bookmarks:
--   CREATE EXTENSION IF NOT EXISTS vector;

-- Dimensão do embedding: ajuste ao modelo (ex. 1536 text-embedding-3-small)
-- CREATE TABLE knowledge_islands (
--   island_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
--   tenant_id     text NOT NULL,
--   name          text NOT NULL,
--   created_at    timestamptz NOT NULL DEFAULT now()
-- );

-- CREATE TABLE knowledge_chunks (
--   id            bigserial PRIMARY KEY,
--   island_id     uuid NOT NULL REFERENCES knowledge_islands(island_id) ON DELETE CASCADE,
--   bookmark_id   text,
--   content       text NOT NULL,
--   embedding     vector(1536),
--   metadata      jsonb DEFAULT '{}'::jsonb,
--   created_at    timestamptz NOT NULL DEFAULT now()
-- );

-- CREATE INDEX ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Na aplicação: sempre filtrar por tenant_id + island_id antes da busca vetorial.
