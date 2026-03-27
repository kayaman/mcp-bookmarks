# AI Gateway: ensemble + LLM-as-judge

## Objetivo

Chamar **vários modelos** em paralelo através de um endpoint **compatível com OpenAI** (`POST …/v1/chat/completions`) — por exemplo o teu AI Gateway — e usar um **modelo juiz** para escolher ou fundir a melhor resposta.

## Configuração

| Variável | Descrição |
|----------|-----------|
| `ENSEMBLE_ENABLED` | `true` para ativar (evita gasto acidental). |
| `AI_GATEWAY_BASE_URL` ou `OPENAI_BASE_URL` | Base com `/v1` (ex.: `https://api.openai.com/v1` ou URL do gateway). |
| `AI_GATEWAY_API_KEY` ou `OPENAI_API_KEY` | Bearer token. |
| `ENSEMBLE_MODELS` | Modelos por omissão, separados por vírgula. |
| `JUDGE_MODEL` | Modelo juiz (default `gpt-4o-mini`). |

## Interfaces

- **MCP:** tool `ensemble_with_judge(task, models?, judge_model?)`.
- **REST:** `POST /api/ensemble` com JSON `{"task":"...","models":["id1","id2"],"judge_model":"..."}` (`models` opcional se `ENSEMBLE_MODELS` estiver definido).
- **Painel web:** com o servidor em execução, abre `GET /ai-gateway` (ex.: `http://localhost:8000/ai-gateway`) para testar o fluxo no browser. O painel chama `GET /api/ai-gateway/status` (metadados sem segredos) e `POST /api/ensemble`.
- **Autenticação REST:** se `MCP_API_KEYS` estiver definido, o painel precisa da mesma chave que os outros endpoints: campo opcional no formulário (guardado só em `sessionStorage` neste separador) como `Authorization: Bearer …` ou `X-API-Key`. A chave do gateway LLM (`OPENAI_API_KEY` / `AI_GATEWAY_API_KEY`) nunca é exposta pela API de estado.

## Resposta

Objeto JSON com `candidates` (por modelo), `answer` final, `rationale`, `winner_model`, `chosen_index`, ou `error` / `partial` se o juiz não devolver JSON válido.

## Custo e quotas

São **N+1** chamadas LLM por pedido. O servidor regista uso (`mcp_ensemble_with_judge` / `rest_ensemble_judge`) e respeita `MCP_MONTHLY_USAGE_LIMIT` se configurado.
