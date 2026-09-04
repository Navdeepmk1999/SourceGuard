# Deployment

Hybrid topology: the Next.js frontend runs on Vercel's edge network, the
FastAPI backend runs as containers on AWS ECS Fargate behind an Application
Load Balancer, and both data stores are managed services.

```
        Browser
           │
           ├──────────────► Vercel (Next.js frontend)
           │                  static + SSR, NEXT_PUBLIC_* baked at build
           │
           └──────────────► AWS ALB  ──► ECS Fargate tasks (backend)
                                              │
                                              ├──► Supabase (Postgres + Auth/JWKS)
                                              ├──► Upstash (Redis, rate limiting)
                                              └──► Groq / Together / LangSmith
```

The browser talks to **both** origins directly — Vercel does not proxy API
calls. That is why the backend's CORS allow-list must name the Vercel origin
exactly, and why `NEXT_PUBLIC_API_URL` must be a publicly reachable URL.

---

## ⚠️ Read this before deploying: the ALB is HTTP-only

`infrastructure/main.tf` provisions an **HTTP (port 80) listener only**,
because an HTTPS listener requires an ACM certificate, which requires a
domain you own — neither of which Terraform can invent.

**A Vercel frontend cannot call an `http://` backend.** Vercel serves over
HTTPS, and browsers block mixed active content: every `fetch` from the app to
an `http://` ALB will fail in the console, not fall back. The stack will look
deployed and be unusable.

So for any real deployment, complete **[Step 7](#step-7-add-tls-required-for-vercel)**.
The HTTP listener is fine for `curl`-ing the API directly, and for a
localhost frontend during setup.

---

## Prerequisites

- AWS account + credentials (`aws configure`)
- Terraform ≥ 1.5 (or run via Docker, as below)
- A Supabase project and an Upstash Redis database
- A Vercel account

---

## Step 1 — Provision the managed data services

**Supabase** (Postgres + Auth). From the dashboard collect:

- The connection string → becomes `DATABASE_URL`. Convert it to the async
  driver this app uses: `postgresql+asyncpg://...` (not `postgresql://`).
  Prefer the **connection pooler** URI; Fargate tasks scale horizontally and
  will otherwise exhaust direct connections.
- The project URL → `SUPABASE_URL` (not a secret; used to build the JWKS
  endpoint for ES256 JWT verification).

**Upstash** (Redis). Copy the connection URI → `REDIS_URL`. Use the
`rediss://` (TLS) form.

> If Redis is unreachable the rate limiter **fails open** by design — the API
> keeps serving, but Denial-of-Wallet protection is off. See `DESIGN.md`,
> Module 9.

## Step 2 — Store secrets in SSM Parameter Store

Secrets are injected into the task at runtime and never enter the task
definition, Terraform state, or the ECS console.

```bash
for kv in \
  "DATABASE_URL=postgresql+asyncpg://..." \
  "REDIS_URL=rediss://..." \
  "GROQ_API_KEY=gsk_..." \
  "TOGETHER_API_KEY=..." \
  "LANGSMITH_API_KEY=lsv2_..."
do
  name="${kv%%=*}"; value="${kv#*=}"
  aws ssm put-parameter --name "/sourceguard/$name" \
    --type SecureString --value "$value" --overwrite
done
```

Then reference their ARNs in `terraform.tfvars` (see
`infrastructure/terraform.tfvars.example`).

## Step 3 — Build and push the backend image

Fargate pulls from ECR, so the image must live there.

```bash
AWS_REGION=us-east-1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REPO="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/sourceguard-backend"

aws ecr create-repository --repository-name sourceguard-backend --region "$AWS_REGION" || true
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# --platform is required when building on an Apple Silicon Mac: Fargate runs
# linux/amd64, and an arm64 image fails at task start with an exec format error.
docker build --platform linux/amd64 -t "$REPO:latest" ./backend
docker push "$REPO:latest"
```

## Step 4 — Apply the Terraform configuration

```bash
cd infrastructure
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: container_image, secret_arns, supabase_url,
# cors_allowed_origins.

terraform init
terraform plan      # review before applying - this creates billable resources
terraform apply
```

No Terraform installed? Run it through Docker instead:

```bash
docker run --rm -v "$PWD":/ws -w /ws \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  hashicorp/terraform:1.9 init
```

Note the outputs — `api_base_url` is what the frontend needs:

```bash
terraform output api_base_url        # http://<alb-dns>/api/v1
terraform output health_check_url
```

Verify the backend is live before touching the frontend:

```bash
curl "$(terraform output -raw health_check_url)"
# {"status":"ok","app":"SourceGuard","environment":"production"}
```

If this hangs or 503s, check target health and container logs:

```bash
aws logs tail "$(terraform output -raw cloudwatch_log_group)" --follow
```

**State:** `terraform.tfstate` holds resolved secret values in plaintext and
is gitignored. Before anyone else runs this, switch to the encrypted S3
backend commented at the top of `main.tf`.

## Step 5 — Initialize the database schema

Terraform provisions compute, not schema. Run the bootstrap **once** against
Supabase, using the **admin** (superuser) connection string:

```bash
cd backend
ADMIN_DATABASE_URL="postgresql+asyncpg://postgres:<pw>@<host>:5432/postgres" \
APP_DB_PASSWORD="$(openssl rand -base64 24)" \
  python -m app.db.init_db
```

This creates the tables, enables `pgvector`, provisions the restricted
`sourceguard_app` role, grants it CRUD (never DDL), and installs the RLS
policies on all five tenant tables with `FORCE ROW LEVEL SECURITY`.

### The runtime connection must NOT be a superuser

This is the single most important line in this document.

Postgres exempts `SUPERUSER` and `BYPASSRLS` roles from **every** RLS policy,
unconditionally — no table-level flag overrides it, `FORCE` included. So
`DATABASE_URL` must point at the restricted role that `init_db` just created,
**not** at Supabase's default `postgres` role:

```
DATABASE_URL=postgresql+asyncpg://sourceguard_app:<APP_DB_PASSWORD>@<host>:5432/postgres
```

Store that as the `DATABASE_URL` SSM parameter from [Step 2](#step-2--store-secrets-in-ssm-parameter-store).

Verify against the deployed backend's actual connection:

```sql
SELECT current_user, rolsuper, rolbypassrls
FROM pg_roles WHERE rolname = current_user;
-- rolsuper and rolbypassrls must BOTH be false
```

If either is true, tenant isolation silently degrades to application-layer
checks only, and the database stops enforcing anything.

## Step 6 — Deploy the frontend to Vercel

```bash
cd frontend
vercel --prod
```

Set these in **Project Settings → Environment Variables**. They are
`NEXT_PUBLIC_*`, so Vercel inlines them at **build** time — changing one
requires a **redeploy**, not just a restart:

| Variable | Value |
| --- | --- |
| `NEXT_PUBLIC_API_URL` | `https://<your-api-domain>/api/v1` (see Step 7) |
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Supabase publishable key |

Then close the CORS loop — the backend's allow-list is exact, with no
wildcards:

```bash
cd infrastructure
terraform apply -var="cors_allowed_origins=https://<your-app>.vercel.app"
```

Add Vercel preview deployments as a comma-separated list if you want them to
reach the API.

## Step 7 — Add TLS (required for Vercel)

Without this the frontend cannot call the backend at all (see the warning at
the top).

1. Point a domain (or subdomain, e.g. `api.example.com`) at Route 53 or your
   DNS provider.
2. Request an ACM certificate **in the same region as the ALB** and validate
   it via DNS.
3. Add an HTTPS listener to `infrastructure/main.tf`:

```hcl
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
}
```

4. Redirect HTTP → HTTPS by replacing the `default_action` on
   `aws_lb_listener.http` with a `redirect` action.
5. CNAME your domain to the ALB DNS name, then set `NEXT_PUBLIC_API_URL` to
   `https://api.example.com/api/v1` and redeploy the frontend.

---

## Redeploying the backend

The ECS service ignores `task_definition` changes (`lifecycle.ignore_changes`)
so Terraform will not revert a deploy it did not perform. Ship a new image
with:

```bash
docker build --platform linux/amd64 -t "$REPO:$(git rev-parse --short HEAD)" ./backend
docker push "$REPO:$(git rev-parse --short HEAD)"

aws ecs update-service \
  --cluster "$(terraform -chdir=infrastructure output -raw ecs_cluster_name)" \
  --service "$(terraform -chdir=infrastructure output -raw ecs_service_name)" \
  --force-new-deployment
```

`deployment_minimum_healthy_percent = 100` keeps the old task serving until
the new one passes its health check.

Prefer immutable tags (the commit SHA) over `:latest` — with `:latest` you
cannot tell which revision is running or roll back to a known-good one.

## Teardown

```bash
cd infrastructure && terraform destroy
```

Destroys everything Terraform created. It does **not** touch Supabase,
Upstash, Vercel, the ECR repository, or the SSM parameters — remove those
separately.

## Cost note

The always-on pieces are the ALB (~$16/mo before traffic) and the Fargate
tasks (2 × 0.5 vCPU / 1 GB by default). For a demo, set `desired_count = 1`.
Nothing here uses a NAT gateway — tasks run in public subnets with the API
reachable only through the ALB's security group, which avoids roughly another
$32/mo.

## Not included

- **Terraform for the frontend.** Vercel is configured through its own
  dashboard/CLI; the Vercel Terraform provider is not used.
- **CI-driven deploys.** `.github/workflows/ci.yml` tests and builds; it does
  not push images or call `terraform apply`. Deployment is manual by the
  steps above.
- **Autoscaling, WAF, and multi-environment workspaces.** Single environment,
  fixed `desired_count`.
