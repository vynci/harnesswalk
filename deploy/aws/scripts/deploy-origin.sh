#!/usr/bin/env bash
# Point the origin at an image tag and restart it: set /harnesswalk/prod/IMAGE_TAG, then send
# the harnesswalk-deploy SSM document (the only command the deploy role may run) to the
# instance tagged harnesswalk:role=origin. The document runs /usr/local/sbin/harnesswalk-up TAG
# (it rewrites compose.env from SSM, pulls the tag, takes the compose file and Caddyfile out of
# the image, runs `docker compose --profile prod up -d --wait`) and waits for /readyz on the box.
#
#   deploy/aws/scripts/deploy-origin.sh [TAG] [--dry-run]     # TAG defaults to the git sha
#
# TAG must be a git sha (the document's allowedPattern ^[0-9a-f]{7,40}$): `latest` and
# `<sha>-dirty` images cannot be deployed. The instance is found by its tag on every run, so a
# replaced instance needs no configuration change.
# Idempotent: the same tag again is a no-op restart of nothing (compose keeps running
# containers whose config did not change). Prints the previous tag for a rollback.
source "$(dirname "$0")/lib.sh"
need aws jq

tag=""
for arg in "$@"; do case "$arg" in --*) ;; *) tag="$arg" ;; esac; done
tag="${tag:-$(git_sha)}"
[[ "$tag" =~ ^[0-9a-f]{7,40}$ ]] || die "the tag must be a git sha (7 to 40 of 0-9a-f), got: $tag"

# The one running instance tagged as the origin. Two during an instance replacement: wait for
# CloudFormation to terminate the old one.
instance="$(aws ec2 describe-instances \
  --filters "Name=tag:harnesswalk:role,Values=origin" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text || true)"
read -r -a found_ids <<<"$instance"
if [ "${#found_ids[@]}" -ne 1 ]; then
  if [ "$DRY_RUN" = "1" ] && [ "${#found_ids[@]}" -eq 0 ]; then
    instance="<instance tagged harnesswalk:role=origin>"
  else
    die "expected one running instance tagged harnesswalk:role=origin, found ${#found_ids[@]}: ${instance:-none}"
  fi
fi
repo_name=harnesswalk/api
[ -n "${HARNESSWALK_ECR_REPOSITORY:-}" ] && repo_name="${HARNESSWALK_ECR_REPOSITORY#*/}"  # URI -> name
param="$HW_SSM_PREFIX/IMAGE_TAG"

if [ "$DRY_RUN" != "1" ]; then
  found="$(aws ecr describe-images --repository-name "$repo_name" --image-ids imageTag="$tag" \
    --query 'length(imageDetails)' --output text 2>/dev/null || echo 0)"
  [ "$found" = "1" ] || die "no image $repo_name:$tag in ECR (build-push.sh first)"
fi

previous="$(aws ssm get-parameter --name "$param" --query Parameter.Value --output text 2>/dev/null || echo none)"
log "origin $instance: $previous -> $tag"
run aws ssm put-parameter --name "$param" --type String --overwrite --value "$tag" \
  --query Version --output text

if [ "$DRY_RUN" = "1" ]; then
  run aws ssm send-command --instance-ids "$instance" --document-name harnesswalk-deploy \
    --comment "harnesswalk deploy $tag" --parameters "tag=$tag"
  exit 0
fi
command_id="$(aws ssm send-command --instance-ids "$instance" --document-name harnesswalk-deploy \
  --comment "harnesswalk deploy $tag" --parameters "tag=$tag" \
  --query Command.CommandId --output text)"
log "ssm command $command_id (harnesswalk-deploy)"

status=Pending
for _ in $(seq 1 180); do
  sleep 5
  status="$(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" \
    --query Status --output text 2>/dev/null || echo Pending)"
  case "$status" in Pending|InProgress|Delayed) continue ;; *) break ;; esac
done
result="$(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" --output json)"
jq -r '.StandardOutputContent' <<<"$result" | tail -20 >&2
if [ "$status" != "Success" ]; then
  jq -r '.StandardErrorContent' <<<"$result" | tail -20 >&2
  die "deploy of $tag ended $status; roll back with: make deploy-rollback TAG=$previous"
fi
log "origin is ready on $tag (previous: $previous; rollback: make deploy-rollback TAG=$previous)"
printf '%s\n' "$previous"
