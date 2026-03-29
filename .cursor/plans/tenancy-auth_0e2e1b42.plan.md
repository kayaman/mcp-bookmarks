---
name: tenancy-auth
overview: Implement multi-tenant isolation with organization_id resolution from JWT/API keys, DynamoDB key patterns, and consistent tenant filtering across MCP tools and REST API.
todos:
  - id: tenant-model
    content: Define Tenant model and add to models.py
    status: pending
  - id: api-key-tenants
    content: Extend API key config to map keys to org_id/user_id/scopes
    status: pending
  - id: dynamo-tenant
    content: Refactor dynamodb.py to accept Tenant param instead of env vars
    status: pending
  - id: sqlite-tenant
    content: Add tenant_id column to SQLite tables with migration
    status: pending
  - id: thread-tenant
    content: Thread tenant context through all MCP tools and REST routes
    status: pending
  - id: fix-resources
    content: Fix bookmarks:// resources to use correct DB backend and tenant filter
    status: pending
  - id: gsi-org
    content: Add orgId-savedAt GSI via Terraform
    status: pending
isProject: false
---

# Multi-Tenancy and Authentication

## Current State

- `[auth.py](mcp-bookmarks/src/mcp_bookmarks/auth.py)` exists with `TenantAuthMiddleware` and `require_api_key` -- uses `MCP_API_KEYS` env var
- `[dynamodb.py](mcp-bookmarks/src/mcp_bookmarks/dynamodb.py)` has `DYNAMODB_ORG_ID` env var and `_tenant_filter_expr()` helper
- `DYNAMODB_USER_ID` is a fixed env var -- no per-request tenant resolution
- REST API in `api.py` uses SQLite only (doesn't switch to DynamoDB)
- MCP tools in `server.py` do use DynamoDB when `DYNAMODB_MODE=true`
- No JWT validation, no Cognito/Auth0 integration in mcp-bookmarks

## Implementation

### 1. Tenant model

Define the tenant hierarchy:

```python
# models.py
class Tenant(BaseModel):
    organization_id: str
    user_id: str | None = None  # optional sub-tenant
```

Every data operation receives a `Tenant` context. All DynamoDB queries filter by `organization_id`.

### 2. Auth middleware (API keys + JWT)

Extend `[auth.py](mcp-bookmarks/src/mcp_bookmarks/auth.py)`:

- **API keys**: `MCP_API_KEYS` maps `key_hash -> {org_id, user_id, scopes}` (currently just validates key exists)
  - Store as JSON: `{"sha256_of_key": {"org_id": "org1", "scopes": ["read", "write"]}}`
  - Resolve tenant from matched key
- **JWT** (future): validate Cognito/Auth0 token, extract `org_id` from claims
  - Add `MCP_JWT_ISSUER` and `MCP_JWT_AUDIENCE` env vars
  - Use `python-jose` or `PyJWT` for validation

### 3. DynamoDB key patterns

Update `[dynamodb.py](mcp-bookmarks/src/mcp_bookmarks/dynamodb.py)`:

- Replace `DYNAMODB_USER_ID` / `DYNAMODB_ORG_ID` env vars with per-request tenant from auth
- Key pattern: items written with `organization_id` attribute
- All queries use `_tenant_filter_expr()` -- already exists but currently uses env var
- Modify `_base_link_filter()` to accept `tenant: Tenant` parameter instead of reading env

### 4. SQLite tenant isolation

Update `[db.py](mcp-bookmarks/src/mcp_bookmarks/db.py)`:

- Add `tenant_id` column to `bookmarks` and `tags` tables (migration)
- All queries include `WHERE tenant_id = ?`
- For personal/single-user mode: default tenant `"default"`

### 5. Consistent tenant threading

- MCP tools in `server.py`: extract tenant from MCP session/context or from API key header
- REST routes in `api.py`: extract tenant from `TenantAuthMiddleware`
- Pass tenant through to all `db` / `dynamodb` method calls
- **Critical**: resources (`bookmarks://taxonomy`, `bookmarks://recent`) must also filter by tenant -- currently they don't use DynamoDB at all

### 6. GSI changes (DynamoDB)

- Current GSI: `userId-savedAt-index` -- may need `orgId-savedAt-index` for org-level queries
- Add via Terraform in `[terraform/dynamodb.tf](mcp-bookmarks/terraform/dynamodb.tf)`

## Key Files

- `[src/mcp_bookmarks/auth.py](mcp-bookmarks/src/mcp_bookmarks/auth.py)` - auth middleware
- `[src/mcp_bookmarks/dynamodb.py](mcp-bookmarks/src/mcp_bookmarks/dynamodb.py)` - tenant filtering
- `[src/mcp_bookmarks/db.py](mcp-bookmarks/src/mcp_bookmarks/db.py)` - add tenant_id column
- `[src/mcp_bookmarks/server.py](mcp-bookmarks/src/mcp_bookmarks/server.py)` - thread tenant to tools
- `[src/mcp_bookmarks/api.py](mcp-bookmarks/src/mcp_bookmarks/api.py)` - thread tenant to routes
- `[terraform/dynamodb.tf](mcp-bookmarks/terraform/dynamodb.tf)` - add org GSI

