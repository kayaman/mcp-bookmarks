# Production Demo Guides

Live endpoint: `https://mcp.example.com` (replace with your deployed `var.mcp_hostname`)

| Client | Transport | Guide |
|---|---|---|
| Claude Code CLI | SSE (`/sse`) | [claude-code.md](claude-code.md) |
| Cursor IDE | SSE (`/sse`) | [cursor.md](cursor.md) |
| ChatGPT Custom Connector | Streamable HTTP (`/mcp`) | [chatgpt.md](chatgpt.md) |

## Shared demo flow (5 minutes)

Same steps work in every client — compares UX, not behavior:

1. `save_bookmark("https://martinfowler.com/articles/2025-llm-agent.html")` → UUID bookmark_id
2. Read resource `bookmarks://taxonomy` → tag list
3. Prompt `save_and_tag(<url>)` → full extract → tag → summarize pipeline
4. `search_bookmarks(query="agents")` → confirm item appears
5. Inspect via `read_bookmark(<id>)` or AWS console → confirm DynamoDB write

## Infra apply checklist (operator)

```bash
# Set required secrets first
export TF_VAR_mcp_api_keys="demo-key-1,demo-key-2:org-id"
export TF_VAR_anthropic_api_key="sk-ant-..."

cd terraform/
terraform plan -var="enable_alb=true" -var="ecs_desired_count=1" \
               -var="route53_zone_id=Z0123456789ABCDEFGHIJ" -out=mcp.plan
terraform apply mcp.plan

# After apply:
terraform output mcp_public_url    # → https://<your var.mcp_hostname>
```

Push a container image to ECR before setting `ecs_desired_count=1`:

```bash
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin <ecr-uri>
docker build -t mcp-bookmarks .
docker tag mcp-bookmarks:latest <ecr-uri>:latest
docker push <ecr-uri>:latest
# Then terraform apply with the image URI
```
