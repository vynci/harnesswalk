# deploy

Docker Compose for the learner path. The API image is built from `deploy/api.Dockerfile` in two
stages: a `node:24` stage builds `web/` with pnpm when it has a `package.json` (and is skipped
while `web/` is a placeholder), then the uv stage copies `web/dist` in, so the api serves the
web app at `/` (non-root user, `harnesswalk serve`). pnpm is pinned (the `packageManager` field
of `web/package.json`, else the `PNPM_VERSION` build argument), corepack never prompts, the
stage fails when the build leaves no `dist/index.html`, and `.dockerignore` keeps every `.env`
file out of the build, so local `VITE_*` values never ship. Image tags are pinned:
`pgvector/pgvector:0.8.7-pg17`, `ollama/ollama:0.40.8` and `caddy:2.11.7-alpine` (the last
also by digest, as are the Dockerfile's `node`, `python` and `uv` bases). The image is
multi-arch (`linux/arm64` and `linux/amd64`). The public sandbox at https://harnesswalk.vynci.dev
runs the `prod` profile on AWS: see [The public site](#the-public-site-harnesswalkvyncidev).

## Profiles

| Profile | Services | Model | Use it when |
|---|---|---|---|
| `dev` | `api`, `postgres` (pgvector), `ollama` | Local Ollama container | Learning on a laptop |
| `homelab` | `api`, `postgres` | An Ollama or vLLM server elsewhere, set `HARNESSWALK_OLLAMA_URL` | A GPU box on your network serves the model |
| `prod` | `api-prod`, `caddy` | The site's Anthropic key, plus `fake-small` | The EC2 origin behind CloudFront; `make prod-up` runs it locally on the fake provider |

```sh
docker compose -f deploy/docker-compose.yml --profile dev up --build      # or: make up
docker compose -f deploy/docker-compose.yml --profile homelab up --build
```

The api container listens on `127.0.0.1:8000`, keeps its SQLite stores in the `harness-data`
volume (`/data`), and runs as the non-root user `harnesswalk`. The `dev` profile starts an
empty Ollama; pull the model once with
`docker compose -f deploy/docker-compose.yml exec ollama ollama pull llama3.1:8b`. To try the
stack without a model, set `HARNESSWALK_PROVIDER=fake` and `HARNESSWALK_FIXTURE=plain_answer`.

The semantic, episodic and knowledge stores keep their vectors in the `postgres` service with
pgvector (`HARNESSWALK_VECTORS=pgvector` in compose, `HARNESSWALK_VECTORS=sqlite` to opt out);
their records stay in SQLite under `/data`. Embeddings are local: the api uses the Ollama
model `nomic-embed-text` when it is pulled
(`docker compose -f deploy/docker-compose.yml exec ollama ollama pull nomic-embed-text`), and
hash embeddings otherwise. The api indexes the pack's documents on start-up.

Without Docker, `make run` starts the same API from the working tree, and serves `web/dist`
when `make web-build` has built it.

On macOS, Ollama in a container cannot use the GPU. Running Ollama natively and pointing
`HARNESSWALK_OLLAMA_URL` at `http://host.docker.internal:11434` with the `homelab` profile is
usually faster.

## Rules

- No secrets in compose files. Keys come from the environment (`.env.example` lists the names).
- Ports bind to `127.0.0.1` only, except Caddy's 80 and 443 in the `prod` profile (TLS in front,
  and only CloudFront can reach 443).
- The Postgres password default is for a local laptop only. Set `POSTGRES_PASSWORD` anywhere
  else.
- Data lives in named volumes (`pgdata`, `harness-data`, `ollama-models`).

## The public site (harnesswalk.vynci.dev)

The public sandbox is the `prod` profile on one EC2 instance behind CloudFront, in AWS account
111910761410, us-east-1. Three CDK stacks in `deploy/aws` (TypeScript, pnpm, `aws-cdk-lib`
2.273.0 pinned) describe it, and the scripts in `deploy/aws/scripts` move code onto it. No
Lambda, no Fargate, no RDS.

```
viewer ──https──> CloudFront  harnesswalk.vynci.dev  (HarnesswalkEdge)
                    ├─ /* ──────────────> S3 bucket harnesswalk-web-111910761410 (OAC, private)
                    │                      viewer-request function: client routes -> /index.html
                    └─ /v1/* /healthz /readyz (no cache, all viewer headers but Host,
                       X-Origin-Verify added, viewer-ip function sets X-Harnesswalk-Viewer-Ip)
                         └─https──> origin.harnesswalk.vynci.dev  Elastic IP  (HarnesswalkOrigin)
                                      default VPC, public subnet in us-east-1a
                                      t4g.small, Amazon Linux 2023 arm64, Docker compose --profile prod
                                      caddy :443 (Let's Encrypt over HTTP-01 on :80)
                                        403 without X-Origin-Verify, 404 off the API paths
                                        └─> api-prod :8000 (127.0.0.1 on the host), /data on its own EBS volume
GitHub Actions ──OIDC──> harnesswalk-github-deploy role, ECR harnesswalk/api  (HarnesswalkCi)
```

| Stack | What it holds | Outputs the owner needs |
|---|---|---|
| `HarnesswalkEdge` | Private S3 bucket (block public access, OAC, TLS only, versioned), CloudFront distribution (price class 100, HTTP/2 and 3, TLS 1.2_2021, the ACM certificate, IPv4 only for now: [IPv6](#ipv6)), two CloudFront Functions | `DistributionDomainName` (the CNAME target), `DistributionId`, `WebBucketName` |
| `HarnesswalkOrigin` | The instance in the account's default VPC (172.31.0.0/16, public subnet in us-east-1a; `--context createVpc=true` makes a VPC 10.79.0.0/24 of its own instead), security group (443 from the CloudFront origin-facing prefix list only, 80 from anywhere, no 22), the instance (IMDSv2, hop limit 1, 20 GB gp3 root, tag `harnesswalk:role=origin`), the 20 GB gp3 data volume (retained), Elastic IP, the SSM document `harnesswalk-deploy`, log group `/harnesswalk/prod` (30 days), four alarms, SNS topic | `ElasticIp` (the A record), `InstanceId`, `AlarmTopicArn`, `DataVolumeId` |
| `HarnesswalkCi` | ECR `harnesswalk/api` (scan on push, last 10 images, every tag immutable except `latest`), the GitHub OIDC provider, role `harnesswalk-github-deploy` trusted by `repo:vynci/harnesswalk:ref:refs/heads/main` only | `DeployRoleArn`, `RepositoryUri` |

The stacks share nothing but the Edge exports (the bucket ARN and the distribution ID): Ci
finds the origin by its tag, so the instance can be replaced without touching Ci. The default
VPC comes from a context lookup committed in `deploy/aws/cdk.context.json` (the account's VPC
quota was full, five of five, when this was written); with `createVpc=true` the stack creates
its own VPC, which needs a free VPC in the quota.

SSM parameters (all under `/harnesswalk/prod/`, written by `put-secrets.sh` and the deploy
scripts, never by CloudFormation): `HARNESSWALK_SECRET`, `ANTHROPIC_API_KEY`, `ORIGIN_VERIFY`
(SecureString), `IMAGE_TAG` (String, the tag the box runs) and, optionally,
`HARNESSWALK_PUBLIC_BUDGET_USD` (String).

The container's environment (CONTRACT section 7): `HARNESSWALK_MODE=public`,
`HARNESSWALK_DATA_DIR=/data`, `HARNESSWALK_SECRET`, `ANTHROPIC_API_KEY`,
`HARNESSWALK_TRUST_PROXY=1`, `HARNESSWALK_LOG_FORMAT=json`, `HARNESSWALK_VECTORS=sqlite`,
`HARNESSWALK_WEB_DIST` empty (the same as unset), and optionally
`HARNESSWALK_PUBLIC_BUDGET_USD`. The image still carries `web/dist` for single-box runs, but
Caddy only forwards the API paths, so on the site the web app always comes from S3.

Caddy in front of the API: the viewer's address is `X-Harnesswalk-Viewer-Ip` (set by the
viewer-ip CloudFront Function, which overwrites any value the viewer sent) and nothing else;
Caddy passes it on as the single `X-Forwarded-For` hop the API trusts and removes
`X-Harnesswalk-Viewer-Ip`, `CloudFront-Viewer-Address` and `X-Forwarded-Host` (it sets
`X-Forwarded-Proto: https`). Its log (the error entries reach CloudWatch, stream `caddy`) drops
`X-Origin-Verify`, `X-Harnesswalk-Api-Key`, `X-Harnesswalk-Provider-Key`, `Cookie` and
`Authorization`, and masks every viewer address to its /24 (IPv6 /64).

### How the origin boots and deploys

User data (`deploy/aws/lib/origin/user-data.sh`) runs once per instance. It installs Docker and
a pinned, checksummed compose plugin, then enables `harnesswalk.service`. On every boot the unit:

1. `harnesswalk-attach`: attaches the data volume to this instance (it waits while an old
   instance still holds it), formats it the first time only, mounts it at
   `/srv/harnesswalk/data` and hands it to uid 10001 (the image's user).
2. `harnesswalk-up`: rewrites `/srv/harnesswalk/compose.env` (mode 0600) from SSM, logs in to
   ECR, pulls `harnesswalk/api:$IMAGE_TAG`, copies `docker-compose.yml` and the `Caddyfile` out
   of that image, and runs `docker compose --profile prod up -d --wait`.

`harnesswalk-up` refuses to write `compose.env` when `HARNESSWALK_SECRET`, `ANTHROPIC_API_KEY`
or `ORIGIN_VERIFY` reads back empty. The compose file and the Caddyfile always come from the
image being deployed, so a rollback rolls them back too.

A deploy is `deploy-origin.sh <tag>`: it sets `IMAGE_TAG`, finds the one running instance
tagged `harnesswalk:role=origin`, and sends it the SSM document `harnesswalk-deploy` with that
tag. The document is the only command the deploy role (and the deploy steps below) may run on
the box: it takes one parameter, `tag`, which SSM checks against `^[0-9a-f]{7,40}$` before
anything runs, runs `/usr/local/sbin/harnesswalk-up <tag>` (which also checks that `IMAGE_TAG`
names that tag, so a reboot comes back on it) and waits up to 60 s for `/readyz`. The script
prints the previous tag. No `AWS-RunShellScript` anywhere: a shell on the box is Session
Manager (`aws ssm start-session`), which the deploy role cannot open. Tags are git shas only;
`latest` and `<sha>-dirty-<time>` images cannot be deployed.

### First deploy

The integration pass runs this with the owner, who pastes the DNS records into GoDaddy. From
the repo root, with `AWS_PROFILE=default`:

1. **Certificate.** `deploy/aws/scripts/request-cert.sh`. It prints the validation CNAME
   (`name`, `value`, and the GoDaddy Host field) and writes `certArn` to
   `deploy/aws/cdk.context.json`. Commit that file.
2. **DNS step 1 (owner).** In GoDaddy, DNS for vynci.dev, add the validation CNAME (Host is the
   name without `.vynci.dev`, Points to is the value without the trailing dot, TTL 1 hour). Wait
   for `ISSUED`: run `request-cert.sh` again, it prints the status.
3. **Secrets.** In your shell, never in a file:
   `export HARNESSWALK_SECRET="$(openssl rand -hex 32)"`, load `ANTHROPIC_API_KEY` from
   wherever the owner keeps it, then `deploy/aws/scripts/put-secrets.sh`. It generates
   `ORIGIN_VERIFY` the first time and prints only names.
4. **Check.** `make deploy-test deploy-synth deploy-diff` (creates only, three stacks; the
   Origin stack creates no VPC, it uses the default VPC from `cdk.context.json`).
5. **Stacks.** If the account already has the GitHub OIDC provider (an account holds one per
   URL; the deploy user cannot list them, so look in the console under IAM, Identity
   providers), add `"githubOidcProviderArn":
   "arn:aws:iam::111910761410:oidc-provider/token.actions.githubusercontent.com"` to
   `deploy/aws/cdk.context.json`. Then `deploy/aws/scripts/deploy-stacks.sh all`: Edge (with
   `ORIGIN_VERIFY` as a NoEcho parameter read from SSM), then Origin, then Ci. The instance boots
   and waits for an image.
6. **DNS step 2 (owner), right away.** In GoDaddy: `CNAME harnesswalk -> <DistributionDomainName>`
   and `A origin.harnesswalk -> <ElasticIp>`, TTL 1 hour (`cdk deploy` printed both). The A
   record must resolve before Caddy first starts, or its Let's Encrypt attempt backs off; check
   with `dig +short origin.harnesswalk.vynci.dev`.
7. **Alarms.** Subscribe the owner's email to the `AlarmTopicArn` output (console, or
   `aws sns subscribe --topic-arn <arn> --protocol email --notification-endpoint <email>`), then
   confirm the email AWS sends.
8. **Image.** `deploy/aws/scripts/build-push.sh` (arm64 with buildx, tags `<sha>` and `latest`).
9. **Origin.** `deploy/aws/scripts/deploy-origin.sh` (the instance is found by its tag; the
   command is the `harnesswalk-deploy` document). Caddy gets its certificate over HTTP-01 on
   port 80 within a minute; `aws logs tail /harnesswalk/prod --log-stream-names caddy` shows
   `certificate obtained successfully`.
10. **Web.** `deploy/aws/scripts/deploy-web.sh`.
11. **Smoke.** `deploy/aws/scripts/smoke.sh`: `/healthz`, `/readyz`, capabilities (public mode,
    three models), one `fake-small` turn followed to `turn_end` over SSE. Then by hand: one
    Haiku and one Sonnet turn in the browser, and the forged-address check: 31 `fake-small`
    turns from one machine, each with a different forged `X-Harnesswalk-Viewer-Ip`,
    `CloudFront-Viewer-Address` and `X-Forwarded-For`, must still hit the per-address limit
    on the 31st (429 with `Retry-After`), because the limit keys on the address CloudFront saw.
    It locks that machine out of new turns for an hour, so run it from a phone hotspot or
    after the browser turns:

    ```sh
    jar="$(mktemp)"
    for i in $(seq 1 31); do
      curl -s -o /dev/null -w "$i %{http_code}\n" -b "$jar" -c "$jar" https://harnesswalk.vynci.dev/v1/turns \
        -H 'Content-Type: application/json' -d '{"message":"hi","model":"fake-small"}' \
        -H "X-Harnesswalk-Viewer-Ip: 198.51.100.$i" -H "CloudFront-Viewer-Address: 198.51.100.$i:443" \
        -H "X-Forwarded-For: 198.51.100.$i"
    done   # 1 to 30: 202, 31: 429
    ```
12. **GitHub.** Set the repository variables below; after the owner pushes `main`, every green
    `ci` run on `main` deploys through `.github/workflows/deploy.yml`.

Repository variables (Settings, Secrets and variables, Actions, Variables), all from stack
outputs (four; there is no instance variable, the scripts find the origin by its tag):

| Variable | From |
|---|---|
| `HARNESSWALK_DEPLOY_ROLE_ARN` | `HarnesswalkCi.DeployRoleArn` |
| `HARNESSWALK_ECR_REPOSITORY` | `HarnesswalkCi.RepositoryUri` |
| `HARNESSWALK_WEB_BUCKET` | `HarnesswalkEdge.WebBucketName` |
| `HARNESSWALK_DISTRIBUTION_ID` | `HarnesswalkEdge.DistributionId` |

The deploy workflow pins every action to a full commit SHA, gives `id-token: write` only to the
`web`, `api` and `rollback` jobs, deploys only runs of `ci` on pushes to this repository's
`main` (never a fork's), and builds the web app before it assumes the role
(`deploy-web.sh --build-only`, then `--skip-build`). If the variable `HARNESSWALK_INSTANCE_ID`
was set by an earlier version of this runbook, delete it.

### Daily operation

- **Logs.** `aws logs tail /harnesswalk/prod --follow --log-stream-names api` (JSON, one line
  per request: request id, tenant hash, route, status, duration; never message content or
  keys) and `--log-stream-names caddy`. A shell on the box: `aws ssm start-session --target
  "$(aws ec2 describe-instances --filters Name=tag:harnesswalk:role,Values=origin
  Name=instance-state-name,Values=running --query 'Reservations[0].Instances[0].InstanceId'
  --output text)"` (or the `InstanceId` output), then `sudo docker compose -f /srv/harnesswalk/docker-compose.yml --env-file
  /srv/harnesswalk/compose.env --profile prod ps`.
- **Budget.** The shared hosted budget is 15 USD per UTC day (`public.hosted_budget_usd_per_day`,
  or `HARNESSWALK_PUBLIC_BUDGET_USD` in the container). Public mode never shows dollars:
  capabilities give `budget.status` and `spent_fraction`. The ledger is `budgets.db` on the data
  volume, so it survives redeploys. When it is spent the Claude models grey out until 00:00
  UTC and `fake-small` keeps working. Dollars are never served in public mode
  (`GET /v1/ops/budget` is dev only); to read them, open a session on the box and query
  `/srv/harnesswalk/data/budgets.db` with sqlite3 (`sudo dnf install -y sqlite`). To change the
  budget without a release: `aws ssm put-parameter --name
  /harnesswalk/prod/HARNESSWALK_PUBLIC_BUDGET_USD --type String --overwrite --value 10`, then
  `deploy-origin.sh <current tag>` (delete the parameter to go back to the config value).
- **Alarms** (all to the SNS topic): `harnesswalk-origin-5xx-rate` (more than 5 % of API
  responses 5xx over two 5-minute periods, at least 10 requests; from the JSON log),
  `harnesswalk-edge-5xx-rate` (CloudFront 5xx rate above 5 %, which also catches an origin
  CloudFront cannot reach), `harnesswalk-origin-cpu` (above 80 % for 15 minutes; a t4g.small
  bursts on credits) and `harnesswalk-origin-status-check`.
- **Health.** `curl -s https://harnesswalk.vynci.dev/readyz` (stores open, provider health for the
  default model, secret present).

### Upgrades

- **Code.** Push to `main`; CI, then the deploy workflow (web and api in parallel, then the
  smoke). By hand: `make deploy-web` and `make deploy-api`.
- **Infrastructure.** `make deploy-diff`, then `deploy/aws/scripts/deploy-stacks.sh <stack>`.
- **AMI or user data.** Both replace the instance (`userDataCausesReplacement`; the AMI is pinned
  in `cdk.json`, find a newer one with `aws ec2 describe-images --owners amazon --filters
  Name=name,Values='al2023-ami-2023*-arm64'`). The new instance boots and waits until
  CloudFormation has terminated the old one, then attaches the same data volume and starts.
  Expect two to five minutes of 502s. For a clean stop first, open a session on the box
  (Daily operation) and run `sudo systemctl stop harnesswalk`. Snapshot the data volume before
  (below). A deploy while two tagged instances run (mid-replacement) refuses to guess; run it
  again once CloudFormation has terminated the old one.
- **CloudFront origin timeout.** The distribution uses 60 s, the most CloudFront allows without
  a quota. The API sends an SSE keepalive every 15 s, so streams never hit it. To raise it,
  request "Response timeout per origin" (L-AECE9FA7) in Service Quotas (up to 180 s), then set
  `readTimeout` in `edge-stack.ts`. This account's quota already reads 120 s.

### IPv6

The distribution is IPv4 only for v1 (`enableIpv6: false` in `edge-stack.ts`, no AAAA
record). The per-address limits (turns per hour, concurrent streams) key on the viewer's
address, and one IPv6 subscriber holds a whole /64, so per-/128 buckets would let one visitor
rotate past every limit. The API buckets IPv6 by /64 since main 53a8bc7 (`rate_key` in
`harnesswalk/control/public.py`). To turn IPv6 on once that is deployed and checked: set
`enableIpv6: true`, `make deploy-diff` (one in-place distribution change),
`deploy-stacks.sh edge` (no DNS change: the `harnesswalk` CNAME already answers AAAA queries
through the distribution's name once it has IPv6), and run the forged-address check above from
an IPv6 network. The origin stays IPv4 (CloudFront reaches it over IPv4).

### Rollback

`make deploy-rollback TAG=<sha>` (or the deploy workflow's manual run with a `tag`): the same
path as a deploy, through `harnesswalk-deploy` on the tagged instance. The last 10
images stay in ECR: `deploy/aws/scripts/rollback.sh` with no tag lists them. The compose file and
Caddyfile roll back with the image; the data volume is untouched; the web app does not roll
back by itself (run `deploy-web.sh` from the matching commit when the API change was not
backward compatible). S3 keeps old object versions for 30 days.

### Secret rotation

- **`HARNESSWALK_SECRET`** signs the anonymous-tenant cookie. Rotating it signs **every**
  visitor out: each gets a fresh, empty tenant, and the old tenants are swept at their TTL.
  `export HARNESSWALK_SECRET="$(openssl rand -hex 32)"`, `put-secrets.sh`, then
  `deploy-origin.sh <current tag>` (it rewrites `compose.env`).
- **`ANTHROPIC_API_KEY`**: rotate in the Anthropic console, `put-secrets.sh` with the new key,
  `deploy-origin.sh <current tag>`, then revoke the old key.
- **`ORIGIN_VERIFY`**: `put-secrets.sh --rotate-origin-verify`, then
  `deploy-stacks.sh edge` and `deploy-origin.sh <current tag>` right after each other.
  Requests in between get 403 from Caddy (a minute or two while CloudFront propagates).

### Data volume snapshots

The data volume (`DataVolumeId`) holds every SQLite store, including the budget ledger.
Snapshot it before any instance replacement:
`aws ec2 create-snapshot --volume-id <DataVolumeId> --description "harnesswalk data $(date -u +%F)"
--tag-specifications 'ResourceType=snapshot,Tags=[{Key=project,Value=harnesswalk}]'` (the
owner policy allows snapshots of the volume tagged `harnesswalk:role=data` only, and tags only
while creating a snapshot).
To restore, create a volume from the snapshot in the same AZ (us-east-1a), point the stack at it
(replace the `DataVolume` resource or swap the volumes by hand while the service is stopped) and
reboot. Deleting the stack leaves the volume and the bucket in place (retained), and a new
stack makes a new empty volume. Scheduled snapshots (Data Lifecycle Manager) are deferred.

### Instance replacement

Change the AMI or user data and deploy Origin (above), or terminate the instance and deploy
Origin again. The Elastic IP and the data volume stay, so DNS and data are unchanged. Caddy's
certificate lives in a Docker volume on the root disk, so a new instance gets a fresh one from
Let's Encrypt on boot.

### Cost (us-east-1 list prices, monthly)

| Item | Estimate |
|---|---|
| t4g.small on demand (730 h × 0.0168 USD) | 12.26 USD |
| EBS gp3, 20 GB root + 20 GB data (0.08 USD per GB) | 3.20 USD |
| Public IPv4 address (Elastic IP, 0.005 USD per hour) | 3.65 USD |
| CloudFront (always-free tier: 1 TB out, 10 M requests), 2 functions (2 M free invocations) | 0 USD at sandbox traffic |
| S3 (a few MB, versioned) and ECR (10 images, about 1 GB) | about 0.20 USD |
| CloudWatch Logs (about 1 GB ingested, 30-day retention), 2 custom metrics, 4 alarms | about 1.50 USD |
| SSM parameters (standard tier, AWS managed key), SNS email | 0 USD |
| VPC (the default VPC; no NAT, no endpoints), SSM document | 0 USD |
| **Infrastructure total** | **about 21 USD** |
| Anthropic usage, capped by the shared budget | up to 15 USD per day (bring-your-own-key turns not counted) |

### IAM the deploy user needs outside CDK

`aws-cdk-deploy` deploys the stacks through the CDK bootstrap roles. The scripts also call AWS
directly. Probed on 2026-11-01: ECR (push, describe), EC2 describe, CloudFormation describe and
CloudWatch Logs read are allowed; the actions below were denied, and the policy grants them on
the site's resources only: the certificate request only for harnesswalk.vynci.dev with DNS
validation, KMS only for the /harnesswalk/prod parameters, Run Command only the
`harnesswalk-deploy` document on the instance tagged `harnesswalk:role=origin` (no
`AWS-RunShellScript`), Session Manager on that instance with only the user's own sessions to
end or resume, snapshots only of the volume tagged `harnesswalk:role=data` (tags only while
creating a snapshot). The distribution ID is unknown before the first deploy, so that statement
is scoped by account until then; narrow it to the ID afterwards. Create it as a customer
managed policy (for example `harnesswalk-deploy-owner`) and attach it to the user: it is about
3.3 KB without whitespace, over the 2 KB limit of an inline user policy.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid": "HarnesswalkCertificateRequest", "Effect": "Allow", "Action": "acm:RequestCertificate", "Resource": "*", "Condition": {"ForAllValues:StringEquals": {"acm:DomainNames": ["harnesswalk.vynci.dev"]}, "StringEquals": {"acm:ValidationMethod": "DNS"}}},
    {"Sid": "HarnesswalkCertificate", "Effect": "Allow", "Action": ["acm:DescribeCertificate", "acm:AddTagsToCertificate"], "Resource": "arn:aws:acm:us-east-1:111910761410:certificate/*"},
    {"Sid": "HarnesswalkCertificateList", "Effect": "Allow", "Action": "acm:ListCertificates", "Resource": "*"},
    {"Sid": "HarnesswalkParameters", "Effect": "Allow", "Action": ["ssm:GetParameter", "ssm:PutParameter"], "Resource": "arn:aws:ssm:us-east-1:111910761410:parameter/harnesswalk/prod/*"},
    {"Sid": "HarnesswalkParameterKey", "Effect": "Allow", "Action": ["kms:Encrypt", "kms:Decrypt"], "Resource": "*", "Condition": {"StringEquals": {"kms:ViaService": "ssm.us-east-1.amazonaws.com"}, "StringLike": {"kms:EncryptionContext:PARAMETER_ARN": "arn:aws:ssm:us-east-1:111910761410:parameter/harnesswalk/prod/*"}}},
    {"Sid": "HarnesswalkRunOnOrigin", "Effect": "Allow", "Action": ["ssm:SendCommand", "ssm:StartSession"], "Resource": "arn:aws:ec2:us-east-1:111910761410:instance/*", "Condition": {"StringEquals": {"ssm:resourceTag/harnesswalk:role": "origin"}}},
    {"Sid": "HarnesswalkDeployDocument", "Effect": "Allow", "Action": "ssm:SendCommand", "Resource": "arn:aws:ssm:us-east-1:111910761410:document/harnesswalk-deploy"},
    {"Sid": "HarnesswalkSessionDocument", "Effect": "Allow", "Action": "ssm:StartSession", "Resource": "arn:aws:ssm:us-east-1:111910761410:document/SSM-SessionManagerRunShell"},
    {"Sid": "HarnesswalkOwnSessions", "Effect": "Allow", "Action": ["ssm:TerminateSession", "ssm:ResumeSession"], "Resource": "arn:aws:ssm:us-east-1:111910761410:session/${aws:username}-*"},
    {"Sid": "HarnesswalkCommandResults", "Effect": "Allow", "Action": ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations", "ssm:DescribeInstanceInformation"], "Resource": "*"},
    {"Sid": "HarnesswalkInvalidate", "Effect": "Allow", "Action": ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation", "cloudfront:GetDistribution"], "Resource": "arn:aws:cloudfront::111910761410:distribution/*"},
    {"Sid": "HarnesswalkWebBucket", "Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::harnesswalk-web-111910761410"},
    {"Sid": "HarnesswalkWebObjects", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], "Resource": "arn:aws:s3:::harnesswalk-web-111910761410/*"},
    {"Sid": "HarnesswalkAlarmEmail", "Effect": "Allow", "Action": ["sns:Subscribe"], "Resource": "arn:aws:sns:us-east-1:111910761410:harnesswalk-alarms"},
    {"Sid": "HarnesswalkSnapshotDataVolume", "Effect": "Allow", "Action": "ec2:CreateSnapshot", "Resource": "arn:aws:ec2:us-east-1:111910761410:volume/*", "Condition": {"StringEquals": {"aws:ResourceTag/harnesswalk:role": "data"}}},
    {"Sid": "HarnesswalkSnapshot", "Effect": "Allow", "Action": "ec2:CreateSnapshot", "Resource": "arn:aws:ec2:us-east-1::snapshot/*"},
    {"Sid": "HarnesswalkSnapshotTags", "Effect": "Allow", "Action": "ec2:CreateTags", "Resource": "arn:aws:ec2:us-east-1::snapshot/*", "Condition": {"StringEquals": {"ec2:CreateAction": "CreateSnapshot"}}},
    {"Sid": "HarnesswalkOidcCheck", "Effect": "Allow", "Action": ["iam:ListOpenIDConnectProviders", "iam:GetOpenIDConnectProvider"], "Resource": "*"}
  ]
}
```

The S3 object actions and `ec2:CreateSnapshot` could not be probed before the bucket and volume
exist; they are in the policy in case the user lacks them. GitHub Actions never uses this user:
it assumes the `HarnesswalkCi` role, whose policy the stack defines (S3 on the bucket,
invalidation on the distribution, ECR push on `harnesswalk/api`, `IMAGE_TAG` read and write,
`ec2:DescribeInstances`, and `ssm:SendCommand` of `harnesswalk-deploy` only, only to an instance
tagged `harnesswalk:role=origin`).

### The prod profile on a laptop

`make prod-up` builds the image from the checkout and runs the same two services with
`deploy/docker-compose.prod-local.yml` on top: the fake provider, a named volume for the
stores, Caddy's internal CA on `127.0.0.1:8443` (and `:8080`), the api on `127.0.0.1:8100`,
json-file logs. It makes throwaway `HARNESSWALK_SECRET` and `ORIGIN_VERIFY` values with openssl
in a 0600 file outside the repo (`$TMPDIR/harnesswalk-prod-local.env`, or `PROD_LOCAL_ENV=`)
and never prints them. `make prod-smoke` runs `smoke.sh` through Caddy; `make prod-down` stops
it (`V=1` drops the volumes).

### Load test

`make loadtest` (after `make prod-up`; `brew install k6`) recreates the api with
`deploy/loadtest/compose.loadtest.yml` (every turn answered by a fake fixture with a 2 s model
latency) and runs `deploy/loadtest/loadtest.js`:

- `rate_limit`: one simulated IP sends 31 turns; the first 30 get 202, the 31st gets 429 with
  `Retry-After`.
- `sse_fanout`: 50 viewers, each its own simulated IP, start one turn each at once and follow it
  over SSE to `turn_end`; `turn_latency_ms` is POST to `turn_end`.

Meanwhile it samples the api's RSS every second and runs `harnesswalk sweep` every 10 s inside
the container. `SCENARIOS=`, `VIEWERS=` and `LOADTEST_MODEL=` adjust it; results land in
`LOADTEST_OUT` (`k6-summary.json`, `rss.csv`, `sweeps.log`). The simulated IP travels in
`X-Harnesswalk-Viewer-Ip`, the header CloudFront's viewer-ip function sets on the site.
