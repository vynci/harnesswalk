#!/usr/bin/env bash
# Point the origin at an image tag and restart it: set /harnesswalk/prod/IMAGE_TAG, then run
# /usr/local/sbin/harnesswalk-up on the instance with SSM Run Command (it rewrites compose.env
# from SSM, pulls the tag, takes the compose file and Caddyfile out of the image, runs
# `docker compose --profile prod up -d --wait`), then checks /readyz on the box.
#
#   deploy/aws/scripts/deploy-origin.sh [TAG] [--dry-run]     # TAG defaults to the git sha
#
# Idempotent: the same tag again is a no-op restart of nothing (compose keeps running
# containers whose config did not change). Prints the previous tag for a rollback.
source "$(dirname "$0")/lib.sh"
need aws jq

tag=""
for arg in "$@"; do case "$arg" in --*) ;; *) tag="$arg" ;; esac; done
tag="${tag:-$(git_sha)}"

instance="$(output HARNESSWALK_INSTANCE_ID HarnesswalkOrigin InstanceId)"
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

commands='["/usr/local/sbin/harnesswalk-up","for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/readyz && exit 0; sleep 2; done; exit 1"]'
if [ "$DRY_RUN" = "1" ]; then
  run aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "harnesswalk deploy $tag" --parameters "commands=$commands,executionTimeout=[\"900\"]"
  exit 0
fi
command_id="$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
  --comment "harnesswalk deploy $tag" \
  --parameters "{\"commands\":$commands,\"executionTimeout\":[\"900\"]}" \
  --query Command.CommandId --output text)"
log "ssm command $command_id"

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
