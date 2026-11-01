#!/usr/bin/env bash
# Write the three SecureString parameters the origin reads at boot (and Edge reads at deploy):
#   /harnesswalk/prod/HARNESSWALK_SECRET   from $HARNESSWALK_SECRET   (openssl rand -hex 32)
#   /harnesswalk/prod/ANTHROPIC_API_KEY    from $ANTHROPIC_API_KEY    (the site's own key)
#   /harnesswalk/prod/ORIGIN_VERIFY        generated here the first time
#
#   HARNESSWALK_SECRET=... ANTHROPIC_API_KEY=... deploy/aws/scripts/put-secrets.sh [--dry-run]
#       [--rotate-origin-verify]
#
# Idempotent: a parameter whose stored value already equals the new one is left alone, and
# ORIGIN_VERIFY is only generated when it does not exist (or with --rotate-origin-verify).
# Values travel through a 0600 temp file (file://), never the command line, and are never
# printed. Changing HARNESSWALK_SECRET signs every anonymous tenant out (deploy/README.md).
source "$(dirname "$0")/lib.sh"
need aws openssl

ROTATE=0
for arg in "$@"; do if [ "$arg" = "--rotate-origin-verify" ]; then ROTATE=1; fi; done

[ -n "${HARNESSWALK_SECRET:-}" ] || die "set HARNESSWALK_SECRET (openssl rand -hex 32) in the environment"
[ "${#HARNESSWALK_SECRET}" -ge 32 ] || die "HARNESSWALK_SECRET must be at least 32 characters"
[ -n "${ANTHROPIC_API_KEY:-}" ] || die "set ANTHROPIC_API_KEY in the environment (the site's key)"

tmpdir="$(mktemp -d)"
chmod 700 "$tmpdir"
trap 'rm -rf "$tmpdir"' EXIT

# current NAME: the stored value (decrypted) or empty when the parameter does not exist.
current() {
  aws ssm get-parameter --with-decryption --name "$1" \
    --query Parameter.Value --output text 2>/dev/null || true
}

# put NAME VALUE: write when different. Prints only the name and what happened.
put() {
  local name="$1" value="$2" file="$tmpdir/value" now
  now="$(current "$name")"
  if [ -n "$now" ] && [ "$now" = "$value" ]; then
    log "unchanged  $name"
    return
  fi
  printf '%s' "$value" >"$file"
  chmod 600 "$file"
  if [ "$DRY_RUN" = "1" ]; then
    log "+ (dry run) aws ssm put-parameter --name $name --type SecureString --overwrite --value file://<redacted>"
  else
    aws ssm put-parameter --name "$name" --type SecureString --overwrite \
      --value "file://$file" --query Version --output text >/dev/null
    log "$([ -n "$now" ] && echo updated || echo created)    $name"
  fi
  rm -f "$file"
}

put "$HW_SSM_PREFIX/HARNESSWALK_SECRET" "$HARNESSWALK_SECRET"
put "$HW_SSM_PREFIX/ANTHROPIC_API_KEY" "$ANTHROPIC_API_KEY"

origin_verify="$(current "$HW_SSM_PREFIX/ORIGIN_VERIFY")"
if [ -z "$origin_verify" ] || [ "$ROTATE" = "1" ]; then
  put "$HW_SSM_PREFIX/ORIGIN_VERIFY" "$(openssl rand -hex 32)"
  if [ "$ROTATE" = "1" ]; then
    log "ORIGIN_VERIFY rotated: run deploy-stacks.sh edge, then deploy-origin.sh (runbook: rotation)"
  fi
else
  log "unchanged  $HW_SSM_PREFIX/ORIGIN_VERIFY (exists; --rotate-origin-verify to replace)"
fi
