# harnesswalk origin bootstrap (Amazon Linux 2023, arm64). The stack prepends the HW_* exports.
#
# Runs once per instance (cloud-init). It installs Docker and the compose plugin, writes three
# small programs and a systemd unit, and starts the unit. Every boot after that, the unit
# attaches and mounts the data volume, rewrites /srv/harnesswalk/compose.env from SSM, and runs
# `docker compose --profile prod up -d` with the compose file and Caddyfile taken out of the
# image being deployed (so a rollback rolls those back too).
#
# Never `set -x` here or in the programs below: compose.env holds secrets, and cloud-init
# output lands in /var/log and in the EC2 console output.
set -euo pipefail

COMPOSE_VERSION=v5.6.0
COMPOSE_SHA256=733ec76717ceb59052a9609b9dadfb523b2df8eab57a54212872d10a58078ea2

[ "$(uname -m)" = "aarch64" ] || { echo "harnesswalk: this bootstrap expects arm64" >&2; exit 1; }

dnf install -y docker jq xfsprogs
mkdir -p /etc/docker
cat >/etc/docker/daemon.json <<'JSON'
{ "live-restore": true, "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON
systemctl enable --now docker

mkdir -p /usr/libexec/docker/cli-plugins
curl -fsSL --retry 5 -o /tmp/docker-compose \
  "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-aarch64"
echo "${COMPOSE_SHA256}  /tmp/docker-compose" | sha256sum -c -
install -m 0755 /tmp/docker-compose /usr/libexec/docker/cli-plugins/docker-compose
rm -f /tmp/docker-compose

mkdir -p /srv/harnesswalk/data /srv/harnesswalk/caddy
chmod 0755 /srv/harnesswalk
cat >/etc/harnesswalk.env <<ENV
HW_REGION=${HW_REGION}
HW_ACCOUNT=${HW_ACCOUNT}
HW_DATA_VOLUME_ID=${HW_DATA_VOLUME_ID}
HW_SSM_PREFIX=${HW_SSM_PREFIX}
HW_IMAGE_TAG_PARAM=${HW_IMAGE_TAG_PARAM}
HW_ECR_REPOSITORY=${HW_ECR_REPOSITORY}
HW_LOG_GROUP=${HW_LOG_GROUP}
HW_ORIGIN_HOST=${HW_ORIGIN_HOST}
ENV

# 1. Attach (to this instance) and mount the data volume. A replaced instance waits here until
#    CloudFormation has terminated the old one and the volume is free.
cat >/usr/local/sbin/harnesswalk-attach <<'SCRIPT'
#!/bin/bash
set -euo pipefail
source /etc/harnesswalk.env
export AWS_REGION="$HW_REGION"
mnt=/srv/harnesswalk/data
mountpoint -q "$mnt" && exit 0
token="$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
iid="$(curl -fsS -H "X-aws-ec2-metadata-token: $token" http://169.254.169.254/latest/meta-data/instance-id)"
dev="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${HW_DATA_VOLUME_ID//-/}"
for i in $(seq 1 120); do
  [ -e "$dev" ] && break
  owner="$(aws ec2 describe-volumes --volume-ids "$HW_DATA_VOLUME_ID" \
    --query 'Volumes[0].Attachments[0].InstanceId' --output text)"
  if [ "$owner" = "None" ]; then
    aws ec2 attach-volume --volume-id "$HW_DATA_VOLUME_ID" --instance-id "$iid" --device /dev/sdf >/dev/null || true
  elif [ "$owner" != "$iid" ]; then
    echo "harnesswalk-attach: $HW_DATA_VOLUME_ID is still attached to $owner, waiting ($i)"
  fi
  sleep 10
done
[ -e "$dev" ] || { echo "harnesswalk-attach: $HW_DATA_VOLUME_ID never appeared" >&2; exit 1; }
if ! blkid "$dev" >/dev/null 2>&1; then
  echo "harnesswalk-attach: formatting the new data volume"
  mkfs.xfs -L hwdata "$dev"
fi
uuid="$(blkid -s UUID -o value "$dev")"
grep -q "$uuid" /etc/fstab || echo "UUID=$uuid $mnt xfs defaults,nofail 0 2" >>/etc/fstab
mount "$mnt"
chown 10001:10001 "$mnt"   # the api image's non-root user
SCRIPT

# 2. Refresh compose.env from SSM and bring the prod profile up on the tag in IMAGE_TAG.
cat >/usr/local/sbin/harnesswalk-up <<'SCRIPT'
#!/bin/bash
set -euo pipefail
source /etc/harnesswalk.env
export AWS_REGION="$HW_REGION"
cd /srv/harnesswalk
tag="$(aws ssm get-parameter --name "$HW_IMAGE_TAG_PARAM" --query Parameter.Value --output text 2>/dev/null || echo latest)"
registry="$HW_ACCOUNT.dkr.ecr.$HW_REGION.amazonaws.com"
image="$registry/$HW_ECR_REPOSITORY:$tag"
param() {
  aws ssm get-parameter --with-decryption --name "$HW_SSM_PREFIX/$1" --query Parameter.Value --output text
}
umask 077
tmp="$(mktemp /srv/harnesswalk/.compose.env.XXXXXX)"
{
  echo "HARNESSWALK_IMAGE='$image'"
  echo "HARNESSWALK_SECRET='$(param HARNESSWALK_SECRET)'"
  echo "ANTHROPIC_API_KEY='$(param ANTHROPIC_API_KEY)'"
  echo "ORIGIN_VERIFY='$(param ORIGIN_VERIFY)'"
  echo "HARNESSWALK_ORIGIN_HOST='$HW_ORIGIN_HOST'"
  echo "HARNESSWALK_LOG_GROUP='$HW_LOG_GROUP'"
  echo "AWS_REGION='$HW_REGION'"
  # Optional String parameter: the shared hosted budget in USD per UTC day.
  budget="$(aws ssm get-parameter --name "$HW_SSM_PREFIX/HARNESSWALK_PUBLIC_BUDGET_USD" \
    --query Parameter.Value --output text 2>/dev/null || true)"
  [ -z "$budget" ] || echo "HARNESSWALK_PUBLIC_BUDGET_USD='$budget'"
} >"$tmp"
mv "$tmp" compose.env
umask 022
aws ecr get-login-password | docker login --username AWS --password-stdin "$registry" >/dev/null
docker pull --quiet "$image" >/dev/null
cid="$(docker create "$image")"
docker cp "$cid:/app/deploy/docker-compose.yml" docker-compose.yml
docker cp "$cid:/app/deploy/caddy/Caddyfile" caddy/Caddyfile
docker rm "$cid" >/dev/null
docker compose --env-file compose.env --profile prod up -d --remove-orphans --wait --wait-timeout 180
docker image prune -af >/dev/null
echo "harnesswalk-up: running $tag"
SCRIPT
chmod 0755 /usr/local/sbin/harnesswalk-attach /usr/local/sbin/harnesswalk-up

cat >/etc/systemd/system/harnesswalk.service <<'UNIT'
[Unit]
Description=harnesswalk (docker compose, prod profile)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/srv/harnesswalk
ExecStartPre=/usr/local/sbin/harnesswalk-attach
ExecStart=/usr/local/sbin/harnesswalk-up
ExecStop=/usr/bin/docker compose --env-file /srv/harnesswalk/compose.env --profile prod down
Restart=on-failure
RestartSec=30
TimeoutStartSec=1800

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable harnesswalk.service
# Not --now-blocking: on the first boot the image may not be pushed yet; the unit retries
# every 30 s until deploy-origin.sh has set IMAGE_TAG and pushed the image.
systemctl start --no-block harnesswalk.service
