# shellcheck shell=bash
# shellcheck disable=SC2034  # the HW_* names are used by the scripts that source this file
# Shared helpers for the deploy scripts. Sourced, never run.
#
# Every script works the same way on a laptop (AWS_PROFILE=default) and in GitHub Actions (the
# OIDC role from aws-actions/configure-aws-credentials). Names of AWS resources come from the
# environment when it sets them (the workflow passes repository variables) and otherwise from
# the CloudFormation outputs of the three stacks.
#
# DRY_RUN=1 (or --dry-run on any script) prints every command that would change something and
# runs only the read-only ones. Secret values are never printed, in either mode.

set -euo pipefail

export AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_PAGER=""

HW_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HW_AWS_DIR="$(cd "$HW_SCRIPTS_DIR/.." && pwd)"
HW_REPO_ROOT="$(cd "$HW_AWS_DIR/../.." && pwd)"
HW_DOMAIN="${HARNESSWALK_DOMAIN:-harnesswalk.vynci.dev}"
HW_SSM_PREFIX="${HARNESSWALK_SSM_PREFIX:-/harnesswalk/prod}"
HW_CONTEXT_FILE="$HW_AWS_DIR/cdk.context.json"
DRY_RUN="${DRY_RUN:-0}"

for arg in "$@"; do
  if [ "$arg" = "--dry-run" ]; then DRY_RUN=1; fi
done

log() { printf '%s\n' "$*" >&2; }
die() { log "error: $*"; exit 1; }

need() {
  local cmd
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || die "$cmd is not installed"
  done
}

# run CMD...: run a command that changes something, or print it under DRY_RUN.
run() {
  if [ "$DRY_RUN" = "1" ]; then
    log "+ (dry run) $*"
  else
    "$@"
  fi
}

# output VAR STACK KEY: the value of $VAR when set, else the stack output KEY, else (dry run
# only) a placeholder. Prints the value.
output() {
  local var="$1" stack="$2" key="$3" value
  value="${!var:-}"
  if [ -z "$value" ]; then
    value="$(aws cloudformation describe-stacks --stack-name "$stack" \
      --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue | [0]" \
      --output text 2>/dev/null || true)"
    if [ "$value" = "None" ]; then value=""; fi
  fi
  if [ -z "$value" ]; then
    if [ "$DRY_RUN" = "1" ]; then
      value="<$stack.$key>"
    else
      die "$var is not set and stack $stack has no output $key (deploy the stack first)"
    fi
  fi
  printf '%s' "$value"
}

# git_sha: the commit being deployed, from the checkout. (Not GITHUB_SHA: on a workflow_run
# trigger that is the branch tip, not the commit CI tested; the workflow checks that one out.)
git_sha() {
  git -C "$HW_REPO_ROOT" rev-parse --short=12 HEAD
}
