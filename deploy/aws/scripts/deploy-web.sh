#!/usr/bin/env bash
# Build web/ for production and publish it to the edge bucket.
#
#   deploy/aws/scripts/deploy-web.sh [--dry-run] [--skip-build | --build-only]
#
# --build-only builds and stops before any AWS call; --skip-build publishes an existing
# web/dist. The deploy workflow uses the two, so `pnpm install` and the build never run with
# the deploy role's credentials in the environment.
#
# The production build sets no VITE_* variable: the API is the same origin (CloudFront routes
# /v1/*), and the mock is off. A web/.env*.local file would bake a laptop's values into the
# bundle, so it is refused. Upload order: hashed /assets first (immutable, one year), then the
# other files (5 minutes), then index.html last (no-cache), each pass with --delete; then an
# invalidation of /index.html and / (client-side routes are rewritten to /index.html at the
# edge, so they share its cache entry). Re-running it uploads nothing that did not change.
source "$(dirname "$0")/lib.sh"

SKIP_BUILD=0
BUILD_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --build-only) BUILD_ONLY=1 ;;
  esac
done
[ "$SKIP_BUILD$BUILD_ONLY" != "11" ] || die "--skip-build and --build-only together do nothing"
if [ "$SKIP_BUILD" = "0" ]; then need pnpm; fi
if [ "$BUILD_ONLY" = "0" ]; then need aws; fi

web="$HW_REPO_ROOT/web"
dist="$web/dist"

shopt -s nullglob
locals=("$web"/.env*.local)
shopt -u nullglob
[ "${#locals[@]}" -eq 0 ] || die "refusing to build with ${locals[*]} present (it would ship local VITE_* values)"

if [ "$SKIP_BUILD" = "0" ]; then
  (
    cd "$web"
    unset VITE_HARNESSWALK_API_URL VITE_HARNESSWALK_MOCK VITE_HARNESSWALK_MOCK_STEP_MS VITE_HARNESSWALK_VALIDATE
    pnpm install --frozen-lockfile
    NODE_ENV=production pnpm build
  )
fi
[ -f "$dist/index.html" ] || die "no $dist/index.html (build first)"
if [ "$BUILD_ONLY" = "1" ]; then log "web built in $dist"; exit 0; fi

bucket="$(output HARNESSWALK_WEB_BUCKET HarnesswalkEdge WebBucketName)"
distribution="$(output HARNESSWALK_DISTRIBUTION_ID HarnesswalkEdge DistributionId)"

run aws s3 sync "$dist/assets" "s3://$bucket/assets" --delete \
  --cache-control "public, max-age=31536000, immutable"
run aws s3 sync "$dist" "s3://$bucket" --delete --exclude "assets/*" --exclude "index.html" \
  --cache-control "public, max-age=300"
run aws s3 cp "$dist/index.html" "s3://$bucket/index.html" \
  --cache-control "no-cache" --content-type "text/html; charset=utf-8"
run aws cloudfront create-invalidation --distribution-id "$distribution" \
  --paths "/index.html" "/" --query Invalidation.Id --output text
log "web published to s3://$bucket (distribution $distribution)"
