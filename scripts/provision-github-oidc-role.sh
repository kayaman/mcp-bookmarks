#!/usr/bin/env sh
# Idempotent: ensure GitHub OIDC provider + scoped IAM role for the
# mcp-bookmarks repo's mcp-toggle workflow. Unlike the cds-services equivalent,
# this role is least-privilege: only ecs:UpdateService and DescribeServices
# on the blogmarks-prod-cluster. No CDK or Terraform privileges.
#
# Requires: aws CLI, credentials with IAM admin access.
# Optional env: GITHUB_REPOSITORY, GITHUB_OIDC_ROLE_NAME, DRY_RUN=1

set -eu

REPO="${GITHUB_REPOSITORY:-kayaman/mcp-bookmarks}"
ROLE_NAME="${GITHUB_OIDC_ROLE_NAME:-github-mcp-bookmarks-toggle}"
THUMBPRINT="${GITHUB_OIDC_THUMBPRINT:-6938fd4d98bab03faadb97b34396831e3780aea1}"
OIDC_URL="https://token.actions.githubusercontent.com"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
CLUSTER="blogmarks-prod-cluster"
SERVICE="blogmarks-prod-mcp"
REGION="${AWS_REGION:-us-east-1}"

echo "==> Account: ${ACCOUNT_ID}"
echo "==> Repository subject: repo:${REPO}:*"
echo "==> Role: ${ROLE_NAME}"
echo "==> Scoped to: cluster=${CLUSTER}, service=${SERVICE}"

have_oidc_provider() {
  aws iam list-open-id-connect-providers --output text 2>/dev/null | grep -Fq "token.actions.githubusercontent.com"
}

if ! have_oidc_provider; then
  echo "==> Creating OIDC provider ${OIDC_URL}"
  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "(dry-run) aws iam create-open-id-connect-provider ..."
  else
    aws iam create-open-id-connect-provider \
      --url "${OIDC_URL}" \
      --client-id-list sts.amazonaws.com \
      --thumbprint-list "${THUMBPRINT}" \
      >/dev/null
  fi
else
  echo "==> OIDC provider already exists"
fi

TRUST_FILE="$(mktemp)"
POLICY_FILE="$(mktemp)"
trap 'rm -f "${TRUST_FILE}" "${POLICY_FILE}"' EXIT INT TERM

cat >"${TRUST_FILE}" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:${REPO}:*"
        }
      }
    }
  ]
}
EOF

cat >"${POLICY_FILE}" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcsUpdateScopedService",
      "Effect": "Allow",
      "Action": [
        "ecs:UpdateService",
        "ecs:DescribeServices"
      ],
      "Resource": "arn:aws:ecs:${REGION}:${ACCOUNT_ID}:service/${CLUSTER}/${SERVICE}"
    },
    {
      "Sid": "EcsListAndWait",
      "Effect": "Allow",
      "Action": [
        "ecs:DescribeClusters",
        "ecs:ListServices",
        "ecs:DescribeTaskDefinition"
      ],
      "Resource": "*"
    }
  ]
}
EOF

if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  echo "==> Updating assume-role policy on ${ROLE_NAME}"
  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "(dry-run) aws iam update-assume-role-policy ..."
  else
    aws iam update-assume-role-policy \
      --role-name "${ROLE_NAME}" \
      --policy-document "file://${TRUST_FILE}"
  fi
else
  echo "==> Creating role ${ROLE_NAME}"
  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "(dry-run) aws iam create-role ..."
  else
    aws iam create-role \
      --role-name "${ROLE_NAME}" \
      --description "GitHub Actions OIDC mcp-toggle for ${REPO}" \
      --assume-role-policy-document "file://${TRUST_FILE}" \
      >/dev/null
  fi
fi

echo "==> Putting inline policy ScopedEcsToggle"
if [ "${DRY_RUN:-}" = "1" ]; then
  echo "(dry-run) aws iam put-role-policy ..."
else
  aws iam put-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-name ScopedEcsToggle \
    --policy-document "file://${POLICY_FILE}"
fi

echo ""
echo "Toggle role ARN:"
echo "  ${ROLE_ARN}"
echo ""
echo "Next: set this as a repo variable in GitHub:"
echo "  gh -R ${REPO} variable set AWS_TOGGLE_ROLE_ARN --body \"${ROLE_ARN}\""
