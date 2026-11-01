#!/usr/bin/env bash
# cdk deploy the stacks in order: HarnesswalkEdge, HarnesswalkOrigin, HarnesswalkCi.
#
#   deploy/aws/scripts/deploy-stacks.sh [edge|origin|ci|all] [--dry-run]
#
# Edge takes X-Origin-Verify as a NoEcho CloudFormation parameter read here from
# /harnesswalk/prod/ORIGIN_VERIFY (put-secrets.sh writes it); the value goes from SSM into the
# cdk process through the --parameters flag and is never printed. Later Edge
# deploys pass it again, so a rotated ORIGIN_VERIFY reaches CloudFront. The first deploy needs
# certArn in cdk.context.json and an ISSUED certificate.
source "$(dirname "$0")/lib.sh"
need aws pnpm

which="all"
for arg in "$@"; do case "$arg" in --*) ;; *) which="$arg" ;; esac; done
cd "$HW_AWS_DIR"
[ -d node_modules ] || pnpm install --frozen-lockfile

cdk() { if [ "$DRY_RUN" = "1" ]; then log "+ (dry run) cdk $*"; else pnpm exec cdk "$@"; fi; }

deploy_edge() {
  local verify
  verify="$(aws ssm get-parameter --with-decryption --name "$HW_SSM_PREFIX/ORIGIN_VERIFY" \
    --query Parameter.Value --output text 2>/dev/null || true)"
  if [ -z "$verify" ]; then
    [ "$DRY_RUN" = "1" ] || die "no $HW_SSM_PREFIX/ORIGIN_VERIFY (run put-secrets.sh first)"
    verify="<from SSM>"
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "+ (dry run) cdk deploy HarnesswalkEdge --exclusively --require-approval never --parameters HarnesswalkEdge:OriginVerify=<redacted> --outputs-file cdk.out/edge-outputs.json"
  else
    pnpm exec cdk deploy HarnesswalkEdge --exclusively --require-approval never \
      --parameters "HarnesswalkEdge:OriginVerify=$verify" --outputs-file cdk.out/edge-outputs.json
  fi
}

case "$which" in
  edge) deploy_edge ;;
  origin) cdk deploy HarnesswalkOrigin --exclusively --require-approval never --outputs-file cdk.out/origin-outputs.json ;;
  ci) cdk deploy HarnesswalkCi --exclusively --require-approval never --outputs-file cdk.out/ci-outputs.json ;;
  all)
    deploy_edge
    cdk deploy HarnesswalkOrigin --exclusively --require-approval never --outputs-file cdk.out/origin-outputs.json
    cdk deploy HarnesswalkCi --exclusively --require-approval never --outputs-file cdk.out/ci-outputs.json
    ;;
  *) die "usage: deploy-stacks.sh [edge|origin|ci|all]" ;;
esac
