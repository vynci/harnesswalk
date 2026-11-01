#!/usr/bin/env bash
# Put the origin back on an earlier image tag (one of the last 10 in ECR).
#
#   deploy/aws/scripts/rollback.sh TAG [--dry-run]
#
# Same path as a deploy (deploy-origin.sh: IMAGE_TAG, then the harnesswalk-deploy document on
# the instance tagged harnesswalk:role=origin), so the compose file and Caddyfile roll back with
# the image. TAG must be a git sha. The data volume is not touched; the web app is not rolled back (run deploy-web.sh
# from the matching commit if the API change was not backward compatible).
source "$(dirname "$0")/lib.sh"
tag=""
for arg in "$@"; do case "$arg" in --*) ;; *) tag="$arg" ;; esac; done
if [ -z "$tag" ]; then
  log "usage: rollback.sh TAG   (recent tags below)"
  aws ecr describe-images --repository-name harnesswalk/api \
    --query 'reverse(sort_by(imageDetails,&imagePushedAt))[:10].[imagePushedAt,join(`,`,imageTags||[`-`])]' \
    --output text >&2 || true
  exit 2
fi
exec "$HW_SCRIPTS_DIR/deploy-origin.sh" "$tag" "$@"
