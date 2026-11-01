#!/usr/bin/env bash
# Request, or find again, the ACM certificate for the site domain and print its DNS validation
# CNAME for GoDaddy. CloudFront only takes certificates from us-east-1, so the region is fixed.
#
#   deploy/aws/scripts/request-cert.sh [--dry-run]
#
# Idempotent: it reuses the ARN already in deploy/aws/cdk.context.json (key certArn), then an
# existing certificate for the domain, and only then requests a new one (with an idempotency
# token, so a retry within the hour returns the same certificate). It writes the ARN to
# cdk.context.json, where `cdk synth` reads it, and prints it on stdout.
source "$(dirname "$0")/lib.sh"
need aws jq

REGION=us-east-1
acm() { aws acm --region "$REGION" "$@"; }

arn="${HARNESSWALK_CERT_ARN:-}"
if [ -z "$arn" ] && [ -f "$HW_CONTEXT_FILE" ]; then
  arn="$(jq -r '.certArn // empty' "$HW_CONTEXT_FILE")"
fi
if [ -z "$arn" ]; then
  # Optional: needs acm:ListCertificates. Without it the idempotency token still dedupes.
  arn="$(acm list-certificates --certificate-statuses PENDING_VALIDATION ISSUED \
    --query "CertificateSummaryList[?DomainName=='$HW_DOMAIN'].CertificateArn | [0]" \
    --output text 2>/dev/null || true)"
  [ "$arn" = "None" ] && arn=""
fi
if [ -z "$arn" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    run acm request-certificate --domain-name "$HW_DOMAIN" --validation-method DNS \
      --idempotency-token harnesswalkcert
    exit 0
  fi
  arn="$(acm request-certificate --domain-name "$HW_DOMAIN" --validation-method DNS \
    --idempotency-token harnesswalkcert \
    --tags Key=project,Value=harnesswalk \
    --query CertificateArn --output text)"
  log "requested $arn"
else
  log "reusing $arn"
fi

# The validation record appears a few seconds after the request.
record=""
for _ in $(seq 1 30); do
  record="$(acm describe-certificate --certificate-arn "$arn" \
    --query 'Certificate.{status:Status,name:DomainValidationOptions[0].ResourceRecord.Name,value:DomainValidationOptions[0].ResourceRecord.Value}' \
    --output json)"
  if [ "$(jq -r '.name // empty' <<<"$record")" != "" ]; then break; fi
  sleep 2
done
name="$(jq -r '.name // empty' <<<"$record")"
value="$(jq -r '.value // empty' <<<"$record")"
status="$(jq -r '.status // empty' <<<"$record")"
[ -n "$name" ] || die "no validation record yet for $arn; run this again in a minute"

zone="${HW_DOMAIN#*.}"   # vynci.dev
host="${name%.}"
host="${host%."$zone"}"  # GoDaddy wants the host part only
log ""
log "certificate $arn"
log "status      $status"
log "validation CNAME (GoDaddy, DNS for $zone):"
log "  name   $name"
log "  value  $value"
log "  GoDaddy Host field: $host   Points to: ${value%.}   TTL: 1 hour"

if [ "$DRY_RUN" != "1" ]; then
  tmp="$(mktemp)"
  if [ -f "$HW_CONTEXT_FILE" ]; then cp "$HW_CONTEXT_FILE" "$tmp"; else echo '{}' >"$tmp"; fi
  jq --arg arn "$arn" '.certArn = $arn' "$tmp" >"$HW_CONTEXT_FILE"
  rm -f "$tmp"
  log "wrote certArn to $HW_CONTEXT_FILE"
fi
printf '%s\n' "$arn"
