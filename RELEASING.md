# Releasing

Cutting a release is six steps. Do not skip; "I'll fix the changelog
later" produces a rotten changelog every time.

## Versioning

Semantic versioning (`MAJOR.MINOR.PATCH`):

- **PATCH** — bug fixes, doc-only PRs, dependency bumps that don't
  affect behavior.
- **MINOR** — new features, new capability flags, new ADRs, new
  Lambda/ECS surfaces that are off by default.
- **MAJOR** — breaking API change, error envelope shape change,
  capability removal.

The source of truth is `version` in [`pyproject.toml`](pyproject.toml).
Every other artifact (git tag, container image tag, CHANGELOG section
header) follows.

## Release checklist

### 1. Sweep `CHANGELOG.md`

`Unreleased` should already have one bullet per PR merged since the
last tag (the [contributor flow in `CONTRIBUTING.md`](CONTRIBUTING.md#proposing-a-change)
asks contributors to add one). Read through them:

- Rephrase for *users* of the project, not the PR title.
- Combine related bullets into a single line.
- Add a one-line summary at the top of the new section ("This release
  adds X, hardens Y, fixes Z.")

### 2. Bump the version

```bash
# In pyproject.toml — `version = "X.Y.Z"`
# Match the version in __init__.py if any.

# Then rename the changelog section:
#   ## Unreleased
# becomes:
#   ## vX.Y.Z — YYYY-MM-DD
# and add a fresh empty:
#   ## Unreleased
# above it.
```

Commit the changelog + version bump together:

```bash
git add CHANGELOG.md pyproject.toml
git commit -m "chore(release): vX.Y.Z"
```

### 3. Tag + push

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

The tag triggers nothing automatic today (no GitHub Release action is
configured); the tag exists for `terraform/`-driven image refs and
human navigation.

### 4. Build + push the container image

Use the same flow [`docs/go-live.md` § E](docs/go-live.md#e-build--push-the-container-image)
documents:

```bash
ECR_URI=$(cd terraform && terraform output -raw ecr_repository_url)
aws ecr get-login-password --region us-east-1 \
  | podman login --username AWS --password-stdin "${ECR_URI%/*}"

podman build -t mcp-bookmarks:latest .
podman tag mcp-bookmarks:latest "$ECR_URI:vX.Y.Z"
podman tag mcp-bookmarks:latest "$ECR_URI:latest"
podman push "$ECR_URI:vX.Y.Z"
podman push "$ECR_URI:latest"
```

### 5. Roll the deployment

```bash
# In terraform/terraform.tfvars:
#   mcp_container_image = "<ECR_URI>:vX.Y.Z"

cd terraform/
terraform plan -out=tfplan
terraform apply tfplan
```

ECS pulls the new image; the ALB target group drains the old task and
brings up the new one.

### 6. Smoke-verify

Run the verification matrix from
[`docs/runbook.md` § Startup verification](docs/runbook.md#startup-verification)
against the live host. If anything fails, follow
[`docs/go-live.md` § J](docs/go-live.md#j-rollback) for the rollback
recipe.

## Hot fixes

For a critical fix on a deployed release:

1. Cut a branch from the deployed tag (`vX.Y.Z`).
2. Apply the fix, write the test, update the changelog (bullet under
   `Unreleased` plus a new `vX.Y.Z+1 — YYYY-MM-DD` section).
3. Bump the patch version, tag, build, push, apply per the steps
   above.
4. Merge the hotfix branch back to `main` so the fix lands in the
   next normal release too.

## Yanking a bad release

If a release goes out broken:

1. Roll the ECS image back to the previous tag immediately
   ([`docs/go-live.md` § J](docs/go-live.md#j-rollback)).
2. Open a PR that reverts the breaking commit (`git revert`).
3. Cut a fresh patch release once the revert is merged.
4. Leave the bad git tag in place — rewriting tags makes the
   container image refs inconsistent. The CHANGELOG entry for the
   bad version should be edited in the *next* release's bullet list
   to call it out ("vX.Y.Z is broken; see #PR — superseded by
   vX.Y.Z+1").

## Reference

- [`docs/go-live.md`](docs/go-live.md) — first-deploy walkthrough
- [`docs/runbook.md`](docs/runbook.md) — verification + rollback
  recipes
- [`docs/infra.md` § Recovery objectives (RPO / RTO)](docs/infra.md#recovery-objectives-rpo--rto) —
  what's recoverable if a release loses data
