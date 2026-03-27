# Blogmarks / mcp-bookmarks — infraestrutura AWS (Terraform)

> **Modo Cursor:** se estiver em *Plan mode*, troque para **Agent** para o assistente gravar os `.tf` automaticamente. Este README descreve o que será criado.

## Objetivo

- **Tags de custo** desde o primeiro recurso (`default_tags` + tags por recurso para Cost Explorer).
- **Orçamento** AWS Budgets em ~300 USD com alertas por e-mail (via SNS).
- **Dados estruturados:** DynamoDB (links + tags + stream), alinhado ao [lambda/template.yaml](../lambda/template.yaml).
- **Ilhas de conhecimento (vetores):** RDS PostgreSQL com extensão **pgvector** (uma base por ambiente; isolamento por `tenant_id` / `island_id` na aplicação — ver [docs/knowledge-islands-schema.sql](docs/knowledge-islands-schema.sql)).
- **Compute:** Lambda (processor stream) + ECS Fargate opcional (`ecs_desired_count = 0` para não pagar Fargate até ter imagem).

## Perfil AWS

```bash
export AWS_PROFILE=sua-conta-blogmarks
export AWS_REGION=us-east-1
cd terraform
terraform init
cp terraform.tfvars.example terraform.tfvars   # edite e-mail, budget_period_start e chaves
```

Antes do primeiro `terraform apply` com `enable_lambda_processor=true`, gere o pacote real do Lambda (dependências + `handler.py`):

```bash
./scripts/package-lambda.sh   # cria terraform/.build/lambda_processor.zip
```

Para `terraform validate` em clone limpo, o ficheiro `.build/lambda_processor.zip` tem de existir (o script acima cria-o; a pasta `.build/` está no `.gitignore`).

```bash
terraform plan
terraform apply
```

## Cost allocation tags (obrigatório no console)

Em **Billing → Cost allocation tags**, ative como *AWS-generated* ou *user-defined* conforme aparecer:

- `Project`, `Environment`, `Service`, `Product`, `ManagedBy`, `CostCenter`, `Owner`, `Component`

Sem isso, as tags existem nos recursos mas **não aparecem** nos relatórios de custo.

## Economia (~300 USD)

- Sem **NAT Gateway** (economia ~32 USD/mês): Fargate em subnet pública com IP público para tráfego de saída; RDS só em subnets privadas.
- **Endpoints:** S3 e DynamoDB gateway endpoints (sem custo de hora) para tráfego da VPC à AWS.
- **Fargate** com `desired_count = 0` até você publicar a imagem do `mcp-bookmarks` no ECR.
- **RDS** `db.t4g.micro` + gp3 20 GiB como padrão.

## Arquivos Terraform (a serem criados no repositório)

| Arquivo | Função |
|--------|--------|
| `versions.tf` | Versões Terraform / providers |
| `providers.tf` | AWS `default_tags` |
| `locals.tf` | Prefixos e tags locais |
| `variables.tf` | Variáveis (orçamento, RDS, ECS, e-mail) |
| `vpc.tf` | VPC, subnets, IGW, rotas, endpoints S3/DynamoDB |
| `security_groups.tf` | SG RDS + ECS |
| `rds.tf` | Subnet group, PostgreSQL 16, secret URL |
| `dynamodb.tf` | Tabelas links (stream) + tags |
| `dynamodb_usage_subscriptions.tf` | Tabelas usage events + subscriptions (Stripe) |
| `alb.tf` | ALB opcional (`enable_alb = true`) na frente do ECS |
| `sqs.tf` | DLQ |
| `lambda.tf` | IAM, função, event source mapping |
| `ecr.tf` | Repositório imagem MCP |
| `ecs.tf` | Cluster, roles, task definition, service |
| `budgets.tf` | Budget mensal + SNS |
| `outputs.tf` | Endpoints e nomes |

## Próximos passos

1. Criar conta AWS e usuário IAM com permissões adequadas (PowerUser ou política custom).
2. Ajustar `budget_period_start` no `terraform.tfvars` para o 1º dia do mês em que começar o acompanhamento (formato `YYYY-MM-01_00:00`).
3. `put-secret-value` ou variável para `anthropic_api_key` (ver `variables.tf`).
4. Build e push da imagem MCP para ECR; atualizar `mcp_container_image` e `ecs_desired_count = 1`.
5. Opcional: `enable_alb = true` para expor HTTP :80 (health check `GET /api/stats`). Stripe: `https://<alb_dns>/webhooks/stripe` (ou terminar TLS no proxy/CDN).
6. `dynamodb_org_id` preenche `DYNAMODB_ORG_ID` na task ECS para isolamento lógico de bookmarks.
