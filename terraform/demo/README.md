# mcp-bookmarks — one-box demo (100% AWS, no ALB / no RDS)

The cheapest way to put the MCP server on the public internet with real HTTPS,
using only AWS. A single EC2 box runs the container in **SQLite mode**;
**CloudFront** terminates TLS and is the only thing allowed to reach the box.

```
client ──HTTPS──▶ CloudFront ──HTTP:8000──▶ EC2 (Elastic IP)
   (free *.cloudfront.net cert)         podman: mcp-bookmarks (SQLite on EBS)

SG inbound: :8000 from com.amazonaws.global.cloudfront.origin-facing  ← CloudFront only
No ALB · No RDS · No SSH (SSM Session Manager) · No third-party tunnel
```

## What it creates

| Resource | Why |
|---|---|
| `aws_instance` (t3.micro) | runs the container (pulled from GHCR); bootstrapped by `../../scripts/ec2-demo-userdata.sh` |
| `aws_eip` | stable origin DNS so CloudFront survives a stop/start |
| `aws_security_group` | inbound :8000 from the CloudFront prefix list only; all egress |
| `aws_iam_role` + instance profile | SSM Session Manager (shell with no SSH key / no port 22) |
| `aws_cloudfront_distribution` | free TLS, SSE-safe (CachingDisabled, no compression) |
| ACM cert + Route 53 records | **only if** `mcp_hostname` is set |

Uses the account's **default VPC** — nothing to pay for on the network side.

## Prerequisite: publish the image once

The box pulls a prebuilt image from GHCR instead of building on boot. So once:

1. Run the **Publish image** workflow (`.github/workflows/publish-image.yml`) — it
   pushes `ghcr.io/kayaman/mcp-bookmarks:latest`. It also runs automatically on pushes
   to `main` that touch `src/`, `Containerfile`, or `pyproject.toml`.
2. Make the GHCR package **public** (repo → Packages → mcp-bookmarks → Package
   settings → Change visibility → Public) so the box can pull anonymously — no
   registry credentials baked into user-data.

## Use

```bash
cd terraform/demo
cp terraform.tfvars.example terraform.tfvars   # defaults work as-is
terraform init
terraform apply
```

Then:

```bash
terraform output mcp_sse_endpoint          # https://<id>.cloudfront.net/sse
eval "$(terraform output -raw ssm_connect)"   # shell on the box (optional)
sudo tail -f /var/log/mcp-bootstrap.log       # watch first-boot pull (~10s)
```

Point any MCP client at the `mcp_sse_endpoint`. First boot pulls the image, so give
it a few seconds plus the CloudFront propagation window before the endpoint answers.

## Cost (us-east-1, rough)

| Item | ~Monthly |
|---|---|
| t3.micro on-demand 24/7 | ~$7.50 (≈$0 if you `aws ec2 stop-instances` between demos) |
| Public IPv4 / Elastic IP | ~$3.60 (AWS now charges for all public IPv4) |
| 12 GB gp3 | ~$1 |
| CloudFront | ~$0 — perpetual free tier (1 TB out + 10M requests/mo) |
| **Total** | **~$12/mo running, ~$5/mo stopped** |

## Notes / caveats

- **SSE through CloudFront** works because caching is disabled and compression is
  off; `origin_read_timeout` is 60s and FastMCP sends keepalive pings under that.
- **Data** lives on the EBS root at `/opt/mcp-bookmarks-data` — survives stop/start,
  lost on terminate. `terraform destroy` deletes the box and the DB; snapshot first
  if you care about the demo bookmarks.
- **No public IP at all?** Swap the public-origin setup for CloudFront **VPC origins**
  (keeps the EC2 private). More moving parts; out of scope for this minimal demo.
- This is a **demo** stack: SQLite, single AZ, no backups, no auth by default. For
  anything real, use the parent `terraform/` stack (ECS + DynamoDB + bearer auth).
