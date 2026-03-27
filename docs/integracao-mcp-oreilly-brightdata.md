# Integração: O’Reilly MCP + Bright Data MCP + mcp-bookmarks

O **mcp-bookmarks** não embute esses servidores no mesmo processo: a integração é **multi-MCP no cliente** (Cursor, Claude Desktop, Claude Code). O assistente usa as tools do **Bright Data** ou da **O’Reilly** para obter conteúdo e as tools **bookmarks** para gravar na tua base.

## Pré-requisitos

1. **`uv run mcp-bookmarks`** a correr (ou URL pública do teu deploy), com SSE em `http://localhost:8000/sse` (ou outra porta).
2. **Bright Data:** conta em [brightdata.com](https://brightdata.com) e **API token** ([área de utilizadores](https://brightdata.com/cp/setting/users)).
3. **O’Reilly:** gera um **personal access token** em [learning.oreilly.com/access-tokens](https://learning.oreilly.com/access-tokens/) (menu Profile → MCP Tokens). O endpoint oficial é **Streamable HTTP**: `https://api.oreilly.com/api/content-discovery/v1/mcp/` com header `Authorization: Bearer <token>`. Documentação: [learning.oreilly.com/apidocs/mcp/content](https://learning.oreilly.com/apidocs/mcp/content).

## Configuração no Cursor

1. **Settings → MCP** (ou edita `~/.cursor/mcp.json` conforme a tua versão do Cursor).
2. Junta três entradas: **bookmarks**, **Bright Data**, **O’Reilly** (quando tiveres URL/credenciais).

O exemplo versionado inclui **bookmarks**, **O’Reilly** (URL oficial) e **Bright Data**: [`.cursor/mcp.json.example`](../.cursor/mcp.json.example). **Não commits o token:** copia para `.cursor/mcp.json` (este ficheiro está no `.gitignore`) e substitui `YOUR_OREILLY_MCP_TOKEN`.

### Bright Data — duas formas válidas

**A) Alojado (sem `npx`)** — cópia o URL com o token (trata o ficheiro de config como segredo; não commits com token real):

```
https://mcp.brightdata.com/mcp?token=YOUR_API_TOKEN_HERE
```

No `mcp.json` do Cursor, muitas versões aceitam `"url": "…"` para servidores remotos (igual ao padrão SSE).

**B) Local com `npx`** (recomendado se não quiseres token na query string):

```json
"bright-data": {
  "command": "npx",
  "args": ["-y", "@brightdata/mcp"],
  "env": {
    "API_TOKEN": "your-token-here"
  }
}
```

Documentação oficial: [Bright Data — MCP overview](https://docs.brightdata.com/mcp-server/overview), pacote npm [`@brightdata/mcp`](https://www.npmjs.com/package/@brightdata/mcp), repositório [brightdata-com/brightdata-mcp](https://github.com/brightdata-com/brightdata-mcp).

Ferramentas úteis em modo rápido (tier gratuito com limite mensal): `search_engine`, `scrape_as_markdown`, `scrape_batch`, etc. Modo Pro e grupos extra: variáveis `PRO_MODE`, `GROUPS`, `TOOLS` — ver README do pacote.

### O’Reilly (oficial)

No `mcp.json` do Cursor (recomendado: **`.cursor/mcp.json`** local, fora do git):

```json
"oreilly": {
  "url": "https://api.oreilly.com/api/content-discovery/v1/mcp/",
  "headers": {
    "Authorization": "Bearer YOUR_TOKEN"
  }
}
```

Tool principal: **`search-oreilly-content`** (pesquisa na plataforma com filtros opcionais). Não suporta OAuth no servidor — só Bearer token.

**Claude Code:** `claude mcp add --transport http oreilly https://api.oreilly.com/api/content-discovery/v1/mcp/ --header "Authorization: Bearer YOUR_TOKEN"`

## Fluxos recomendados

### 1) Página Web difícil (anti-bot, paywall leve)

1. `save_bookmark(url)` → recebes `bookmark_id`.
2. Bright Data: `scrape_as_markdown` (ou equivalente) para a mesma URL.
3. `set_bookmark_body(bookmark_id, texto)` com o markdown/texto devolvido.
4. `get_tags` → `tag_bookmark` → `set_summary`.

Isto evita depender só do `extract_content` deste servidor quando o fetch directo falha.

### 2) Conteúdo O’Reilly + arquivo no blogmarks

1. Usa as tools do **MCP O’Reilly** para pesquisar e resumir (capítulos, vídeos, eventos).
2. Se existir **URL estável** que possas guardar (política da plataforma e TOS), `save_bookmark(url)` ou `save_and_tag(url)`.
3. **Não** armazenes cópias extensas de obras protegidas sem permissão; prefere **links**, **citações curtas** e **o teu resumo** em `set_summary`.

### 3) Pesquisa Web + gravação

1. Bright Data `search_engine` para resultados actuais.
2. Escolhe URL → `save_bookmark` → opcionalmente `scrape_as_markdown` + `set_bookmark_body` + tagging.

## Custos, quotas e ética

- **Bright Data:** uso contabilizado na conta; há tier gratuito com limite mensal — vê [preços / modos](https://github.com/brightdata-com/brightdata-mcp#-pricing--modes) no repositório oficial.
- **O’Reilly:** sujeito ao contrato e plano da plataforma.
- **Sites alvo:** respeita robots.txt, termos do site e direitos de autor; `set_bookmark_body` deve conter apenas texto que tenhas direito de guardar.

## Prompt de sistema (sugestão)

> Tens acesso ao servidor **bookmarks** (gravar links, tags, resumos, `set_bookmark_body`), ao **Bright Data** (web search e scrape) e, se configurado, ao **O’Reilly** (pesquisa na biblioteca O’Reilly). Para páginas que falhem com extract normal, usa Bright Data e depois `set_bookmark_body`. Para O’Reilly, cita fontes e guarda só URLs e resumos próprios conforme a política de conteúdo.

## Ver também

- [mcp-fetch-integrations.md](mcp-fetch-integrations.md) — Bright Data / Tavily (EN)
- [oreilly-mcp.md](oreilly-mcp.md) — padrão de prompt O’Reilly + compliance (EN)
