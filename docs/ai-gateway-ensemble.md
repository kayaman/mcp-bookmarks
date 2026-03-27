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

## Resposta

Objeto JSON com `candidates` (por modelo), `answer` final, `rationale`, `winner_model`, `chosen_index`, ou `error` / `partial` se o juiz não devolver JSON válido.

## Custo e quotas

São **N+1** chamadas LLM por pedido. O servidor regista uso (`mcp_ensemble_with_judge` / `rest_ensemble_judge`) e respeita `MCP_MONTHLY_USAGE_LIMIT` se configurado.
