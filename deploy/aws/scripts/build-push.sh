#!/usr/bin/env bash
# Build the api image for the origin (linux/arm64) with buildx and push it to ECR as
# <git sha> and latest.
#
#   deploy/aws/scripts/build-push.sh [--dry-run]
#   PLATFORMS=linux/arm64,linux/amd64 deploy/aws/scripts/build-push.sh   # multi-arch
#
# Idempotent: when the sha tag is already in ECR it only moves `latest` there (no rebuild).
# Every tag but `latest` is immutable in ECR, so a pushed sha always names the image that was
# tested. A dirty working tree is refused (the tag would lie) unless ALLOW_DIRTY=1, which tags
# <sha>-dirty-<UTC time>; such an image can be pulled for a look but never deployed (the
# harnesswalk-deploy document only takes a bare sha). Prints the tag on stdout.
source "$(dirname "$0")/lib.sh"
need aws docker git

PLATFORMS="${PLATFORMS:-linux/arm64}"

sha="$(git_sha)"
if [ -z "${GITHUB_ACTIONS:-}" ] && [ -n "$(git -C "$HW_REPO_ROOT" status --porcelain)" ]; then
  [ "${ALLOW_DIRTY:-0}" = "1" ] || die "the working tree is dirty; commit first (or ALLOW_DIRTY=1 to tag $sha-dirty-<time>)"
  sha="$sha-dirty-$(date -u +%Y%m%d%H%M%S)"
fi
tag="${IMAGE_TAG:-$sha}"

repo_uri="${HARNESSWALK_ECR_REPOSITORY:-}"
if [ -z "$repo_uri" ]; then
  repo_uri="$(output HARNESSWALK_ECR_REPOSITORY HarnesswalkCi RepositoryUri)"
fi
registry="${repo_uri%%/*}"
repo_name="${repo_uri#*/}"

exists="$(aws ecr describe-images --repository-name "$repo_name" --image-ids imageTag="$tag" \
  --query 'length(imageDetails)' --output text 2>/dev/null || echo 0)"

if [ "$DRY_RUN" = "1" ]; then
  log "+ (dry run) aws ecr get-login-password | docker login --username AWS --password-stdin $registry"
else
  aws ecr get-login-password | docker login --username AWS --password-stdin "$registry" >/dev/null
fi

if [ "$exists" = "1" ]; then
  log "$repo_uri:$tag is already in ECR; moving latest to it"
  run docker buildx imagetools create --tag "$repo_uri:latest" "$repo_uri:$tag"
else
  run docker buildx build \
    --platform "$PLATFORMS" \
    --file "$HW_REPO_ROOT/deploy/api.Dockerfile" \
    --build-arg "GIT_SHA=$sha" \
    --tag "$repo_uri:$tag" \
    --tag "$repo_uri:latest" \
    --provenance=false \
    --push \
    "$HW_REPO_ROOT"
fi
if [ "$DRY_RUN" = "1" ]; then log "(dry run) would push $repo_uri:$tag"; else log "pushed $repo_uri:$tag"; fi
printf '%s\n' "$tag"
