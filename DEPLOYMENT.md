# Deployment

**Primary strategy: Render (backend) + Vercel (frontend), on managed
Supabase and Upstash.** Zero fixed cost, automatic TLS, and deploys on
`git push`.

The AWS/Terraform path built in Module 12 is retained in `infrastructure/`
and documented in [Alternative Enterprise Deployment (AWS)](#alternative-enterprise-deployment-aws)
at the bottom of this document. It is the migration target for enterprise
scale, not the live deployment.

```
        Browser
           │
           ├──────────────► Vercel  ── Next.js frontend (edge, HTTPS)
           │
           └──────────────► Render  ── FastAPI backend (Docker, HTTPS)
                                          │
                                          ├──► Supabase (Postgres + pgvector + Auth)
                                          ├──► Upstash (Redis, rate limiting)
                                          └──► Groq / Together / LangSmith
```

The browser talks to **both** origins directly — Vercel does not proxy API
calls. That is why the backend's CORS allow-list must name the Vercel origin
exactly, and why `NEXT_PUBLIC_API_URL` must be a publicly reachable URL.

---

## ⚠️ Critical: use Session Mode (port 5432), never Transaction Mode (6543)

**`DATABASE_URL` must use a session-mode connection.** Supabase offers a
transaction-mode pooler on port **6543**; using it will silently break tenant
isolation.

### Why

SourceGuard sets the RLS tenant context as a **session-scoped** PostgreSQL
variable (`app/api/deps.py`):

```python
# `false` = SESSION scope, deliberately not transaction scope
await session.execute(
    text("SELECT set_config(:name, :user_id, false)"),
    {"name": TENANT_SETTING, "user_id": str(user_id)},
)
```

Session scope is required because several endpoints commit mid-request —
`create_workspace` commits then refreshes; `stream_query` saves the user turn,
generates, then saves the assistant turn. A *transaction*-scoped setting is
discarded at each commit, so every query after the first would run with no
tenant context and, under enforced RLS, correctly return nothing.

A **transaction-mode pooler returns the backend connection to the pool after
every transaction.** That produces two failures:

| Failure | Consequence |
| --- | --- |
| The session variable does not persist across the request | Queries after the first commit see no tenant context — requests break |
| A backend carrying a stale variable is handed to another client | **One tenant's context can apply to another tenant's query** |

The second is a silent cross-tenant data exposure. It would not raise an
error, and no test in the suite would catch it — the suite runs on SQLite,
which has no RLS and no pooler.

### What to use

| Connection | Port | Session state | Use? |
| --- | --- | --- | --- |
| Direct connection | 5432 | Preserved | ✅ Yes |
| Supavisor **session** pooler | 5432 | Preserved | ✅ Yes — preferred |
| Supavisor **transaction** pooler | 6543 | **Not preserved** | ❌ **Never** |

In the Supabase dashboard → **Connect**, copy the **Session pooler** string
(port 5432). Do not copy the Transaction pooler string.

> Note that session-mode pooling multiplexes less aggressively than
> transaction mode, so the connection ceiling returns as a scaling
> consideration. That is the correct trade: a connection limit is a capacity
> problem you can measure and plan for, whereas a tenant-isolation bug is
> unacceptable at any scale. If connection pressure ever becomes real, the fix
> is to make the tenant variable transaction-scoped and remove mid-request
> commits — **not** to switch to port 6543.

Verify after deploying:

```sql
SELECT current_setting('app.current_user_id', true);  -- set within a request
```

---

## Prerequisites

- GitHub repository (Render and Vercel both deploy from it)
- Supabase project
- Upstash Redis database
- Render account (free tier)
- Vercel account (Hobby tier)

No AWS account, no domain, and no TLS certificate are required — Render and
Vercel both provide HTTPS automatically.

---

## Step 1 — Provision the managed data services

### Supabase (Postgres + pgvector + Auth)

From the dashboard collect:

- **Session-mode connection string** (port **5432** — see the warning above).
  Convert it to the async driver: replace `postgresql://` with
  `postgresql+asyncpg://`.
- **Project URL** → `SUPABASE_URL`. Not a secret; used to build the JWKS
  endpoint for ES256 JWT verification.
- **Publishable key** → the frontend's `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`.

### Upstash (Redis)

Copy the connection URI → `REDIS_URL`. Use the `rediss://` (TLS) form.

> If Redis is unreachable the rate limiter **fails open** by design — the API
> keeps serving, but Denial-of-Wallet protection is off. See `DESIGN.md`,
> Module 9.

---

## Step 2 — Bootstrap the database and provision the restricted role

Run **once**, from your local machine, pointed at Supabase. This creates the
tables, enables `pgvector`, provisions the restricted `sourceguard_app` role,
and installs the RLS policies on all five tenant tables.

It needs the **admin (superuser)** connection because it performs DDL:

```bash
cd backend
source venv/bin/activate

ADMIN_DATABASE_URL="postgresql+asyncpg://postgres:<pw>@<host>:5432/postgres" \
APP_DB_PASSWORD="$(openssl rand -base64 24)" \
  python -m app.db.init_db
```

Record the generated `APP_DB_PASSWORD` — it becomes part of the runtime
`DATABASE_URL`.

### The runtime connection must NOT be a superuser

PostgreSQL exempts `SUPERUSER` and `BYPASSRLS` roles from **every** RLS
policy, unconditionally — no table-level flag overrides this, `FORCE`
included. So the runtime connection must use the restricted role that
`init_db` just created, **not** Supabase's default `postgres` role:

```
DATABASE_URL=postgresql+asyncpg://sourceguard_app:<APP_DB_PASSWORD>@<host>:5432/postgres
```

Verify from a running instance:

```sql
SELECT current_user, rolsuper, rolbypassrls
FROM pg_roles WHERE rolname = current_user;
-- rolsuper and rolbypassrls must BOTH be false
```

If either is true, tenant isolation silently degrades to application-layer
checks only, and the database stops enforcing anything.

---

## Step 3 — Deploy the backend to Render

Render builds directly from `backend/Dockerfile` — the same image used
locally and in CI.

1. **New → Web Service**, connect the GitHub repository.
2. **Runtime:** Docker
3. **Root Directory:** `backend`
4. **Dockerfile Path:** `Dockerfile`
5. **Health Check Path:** `/health`
6. **Instance Type:** Free

### Port binding — the one Render-specific adjustment

`backend/Dockerfile` ends with a fixed port, because that is what Docker
Compose and ECS expect:

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Render assigns a port via the `PORT` environment variable. Rather than
modifying the Dockerfile, override the start command in the Render service
settings (**Settings → Docker Command**):

```
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Alternatively, set the environment variable `PORT=8000` so Render's assigned
port matches the container's fixed one. The command override is the more
robust option, since it works regardless of what Render assigns.

> This step has not been validated against a live Render account. If the
> service builds but the health check never passes, a port mismatch is the
> first thing to check — the container will be running and simply not
> listening where Render is probing.

### Environment variables

Set these under **Environment**:

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://sourceguard_app:<pw>@<host>:**5432**/postgres` |
| `REDIS_URL` | `rediss://...` from Upstash |
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` |
| `CORS_ALLOWED_ORIGINS` | Vercel URL — set after Step 4 |
| `ENVIRONMENT` | `production` |
| `GROQ_API_KEY` | Optional; unset ⇒ deterministic offline mock generation |
| `TOGETHER_API_KEY` | Optional; unset ⇒ deterministic offline mock embeddings |
| `LANGSMITH_API_KEY` | Optional, for tracing |
| `LANGCHAIN_TRACING_V2` | Optional, `true` to enable tracing |

Do **not** set `ADMIN_DATABASE_URL` or `APP_DB_PASSWORD` in Render — they are
bootstrap-only credentials with DDL rights, and the running service has no
need for them.

### Verify

```bash
curl https://<your-service>.onrender.com/health
# {"status":"ok","app":"SourceGuard","environment":"production"}
```

### Optional: Blueprint deploys

To manage the service declaratively instead of through the dashboard, commit
a `render.yaml` at the repository root:

```yaml
services:
  - type: web
    name: sourceguard-backend
    runtime: docker
    rootDir: backend
    dockerfilePath: ./Dockerfile
    dockerCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /health
    plan: free
    envVars:
      - key: ENVIRONMENT
        value: production
      - key: DATABASE_URL
        sync: false      # set in the dashboard; never commit secrets
      - key: REDIS_URL
        sync: false
      - key: SUPABASE_URL
        sync: false
      - key: CORS_ALLOWED_ORIGINS
        sync: false
```

`sync: false` means the value is set in the dashboard and never stored in the
repository.

---

## Step 4 — Deploy the frontend to Vercel

```bash
cd frontend
vercel --prod
```

Or connect the repository in the Vercel dashboard with **Root Directory** set
to `frontend`.

Set these under **Project Settings → Environment Variables**:

| Variable | Value |
| --- | --- |
| `NEXT_PUBLIC_API_URL` | `https://<your-service>.onrender.com/api/v1` |
| `NEXT_PUBLIC_SUPABASE_URL` | `https://<project-ref>.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Supabase publishable key |

> **`NEXT_PUBLIC_*` are inlined at BUILD time, not read at runtime.** Changing
> one requires a **redeploy**, not a restart. Setting them after a build has
> already run leaves `undefined` baked into the client bundle — the app loads
> and simply cannot reach the API.

Note that Render's URL is `https://`, so there is no mixed-content problem —
this is the single largest advantage over the AWS path, whose ALB terminates
HTTP only and therefore cannot serve a Vercel frontend without an ACM
certificate and a domain.

---

## Step 5 — Close the CORS loop

The backend's allow-list is exact, with no wildcards. Set
`CORS_ALLOWED_ORIGINS` in Render to the Vercel production URL:

```
CORS_ALLOWED_ORIGINS=https://sourceguard.vercel.app
```

Add preview deployments as a comma-separated list if you want them to reach
the API. Render restarts the service on an environment change.

---

## Step 6 — Verify the deployment

Work through all six before considering it live:

1. **Health** — `curl https://<service>.onrender.com/health` returns 200.
2. **Auth** — sign up and log in through the Vercel URL; the browser console
   shows no CORS errors.
3. **RLS is enforced** — the most important check:
   ```sql
   SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;
   -- both must be false
   ```
4. **Tenant isolation** — create a workspace as one user, then confirm a
   second account cannot see or access it (expect an empty list and a 404).
5. **Ingestion** — upload a PDF containing a table; confirm the chunk count is
   non-zero and the table survived as Markdown.
6. **Streaming** — run a query and confirm `token`, `verification`, and `done`
   events arrive incrementally rather than in one delayed block.

---

## Redeploying

Both platforms deploy on push to the default branch:

```bash
git push origin main
```

Render rebuilds the Docker image and performs a rolling restart. Vercel
rebuilds the Next.js app. GitHub Actions runs tests and builds in parallel but
does **not** gate either deploy — CI and CD are separate. To make CI gate
deploys, disable auto-deploy on both platforms and trigger via deploy hooks
from the workflow.

---

## Cost and free-tier limitations

| Service | Tier | Cost |
| --- | --- | --- |
| Render web service | Free | $0 |
| Vercel | Hobby | $0 |
| Supabase | Free | $0 |
| Upstash | Free | $0 |
| **Total** | | **$0** |

Known free-tier constraints:

- **Render spins down after ~15 minutes of inactivity**, with a 30–60 second
  cold start on the next request. For SourceGuard this is worse than usual:
  the query endpoint is an SSE stream, so a cold start appears as a stream
  that hangs before the first token — on precisely the request a first-time
  visitor makes. A cheap paid instance removes this.
- **Supabase free projects pause after ~1 week of inactivity** and need
  manual resume.
- **Vercel Hobby is non-commercial only.**

---

## Not included

- **CI-gated deploys.** CI tests and builds; both platforms auto-deploy on
  push independently.
- **Custom domains.** Both platforms support them; the default `.onrender.com`
  and `.vercel.app` hostnames are used.
- **Autoscaling.** Render's free tier is a single fixed instance.

---
---

# Alternative Enterprise Deployment (AWS)

The Terraform configuration in `infrastructure/` provisions the backend on
**AWS ECS Fargate behind an Application Load Balancer**. It is retained in the
repository as the migration target for enterprise scale, and as demonstrated
IaC capability — **it is not the live deployment.**

**Status:** `terraform fmt`, `init`, and `validate` all pass against the real
`hashicorp/aws` provider schema. It has **never been applied** to a live AWS
account, so runtime behavior — task startup, target health, secret
resolution — is unverified.

### When AWS is worth the cost

Choose this path when you need any of:

- **Network isolation.** A VPC with security groups means the compute is not
  addressable from the internet at all — traffic reaches tasks only through
  the ALB's security group. Render exposes a public HTTPS endpoint where
  application auth is the entire perimeter.
- **Auditable, least-privilege IAM.** "This role may read exactly these five
  SSM parameter ARNs" is verifiable and CloudTrail-logged. Platform
  environment variables are not.
- **Precise, metric-driven scaling.** SourceGuard's two workloads want
  different signals — uploads are CPU/memory-heavy, queries are long-lived
  I/O-bound streams. ECS target-tracking can scale on either, and the two
  could be split into separate services.
- **Compliance requirements** — VPC flow logs, CloudTrail, configurable log
  retention, data residency guarantees.

Running cost is roughly **$57/month** (ALB ~$16, two Fargate tasks ~$36,
CloudWatch ~$5), or ~$35 at `desired_count = 1`. It does not reach zero: the
ALB bills hourly regardless of traffic.

### ⚠️ The ALB is HTTP-only

`infrastructure/main.tf` provisions an **HTTP (port 80) listener only**,
because an HTTPS listener requires an ACM certificate, which requires a domain
you own — neither of which Terraform can invent.

**A Vercel frontend cannot call an `http://` backend.** Vercel serves over
HTTPS and browsers block mixed active content, so every API call fails
silently in the console. Completing [Add TLS](#aws-step-5--add-tls-required)
is mandatory for this path, not optional. Render sidesteps this entirely by
providing HTTPS automatically.

### AWS Step 1 — Push the image to ECR

```bash
AWS_REGION=us-east-1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REPO="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/sourceguard-backend"

aws ecr create-repository --repository-name sourceguard-backend --region "$AWS_REGION" || true
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# --platform is required on Apple Silicon: Fargate runs linux/amd64, and an
# arm64 image fails at task start with an exec format error.
docker build --platform linux/amd64 -t "$REPO:$(git rev-parse --short HEAD)" ./backend
docker push "$REPO:$(git rev-parse --short HEAD)"
```

### AWS Step 2 — Store secrets in SSM Parameter Store

Secrets are injected at task start and never enter the task definition,
Terraform state, or the ECS console.

```bash
for kv in \
  "DATABASE_URL=postgresql+asyncpg://sourceguard_app:<pw>@<host>:5432/postgres" \
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

The **session-mode port 5432 requirement and the non-superuser role
requirement apply identically here** — see the warnings at the top of this
document. They are properties of the application, not of the hosting platform.

### AWS Step 3 — Apply the Terraform

```bash
cd infrastructure
cp terraform.tfvars.example terraform.tfvars
# Edit: container_image, secret_arns, supabase_url, cors_allowed_origins.

terraform init
terraform plan      # review before applying — this creates billable resources
terraform apply

terraform output api_base_url
curl "$(terraform output -raw health_check_url)"
```

Without Terraform installed, run it through Docker:

```bash
docker run --rm -v "$PWD":/ws -w /ws \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  hashicorp/terraform:1.9 init
```

**State:** `terraform.tfstate` holds resolved secret values in plaintext and is
gitignored. Switch to the encrypted S3 backend commented at the top of
`main.tf` before anyone else runs this.

Debug a failing deploy with:

```bash
aws logs tail "$(terraform output -raw cloudwatch_log_group)" --follow
```

### AWS Step 4 — Initialize the database schema

Identical to [Step 2](#step-2--bootstrap-the-database-and-provision-the-restricted-role)
of the primary guide. Terraform provisions compute, not schema.

<a name="aws-step-5--add-tls-required"></a>
### AWS Step 5 — Add TLS (required)

1. Point a domain (e.g. `api.example.com`) at Route 53 or your DNS provider.
2. Request an ACM certificate **in the same region as the ALB**; validate via
   DNS.
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

4. Replace the HTTP listener's `default_action` with a redirect to HTTPS.
5. CNAME the domain to the ALB DNS name, set `NEXT_PUBLIC_API_URL` to
   `https://api.example.com/api/v1`, and **redeploy** the frontend.

### AWS Step 6 — Redeploying

The ECS service sets `lifecycle.ignore_changes = [task_definition]`, so
Terraform will not revert a deploy it did not perform:

```bash
docker build --platform linux/amd64 -t "$REPO:$(git rev-parse --short HEAD)" ./backend
docker push "$REPO:$(git rev-parse --short HEAD)"

aws ecs update-service \
  --cluster "$(terraform -chdir=infrastructure output -raw ecs_cluster_name)" \
  --service "$(terraform -chdir=infrastructure output -raw ecs_service_name)" \
  --force-new-deployment
```

`deployment_minimum_healthy_percent = 100` keeps the old task serving until
the new one passes its health check. Prefer immutable commit-SHA tags over
`:latest` — with `:latest` you cannot tell which revision is running or roll
back to a known-good one.

### AWS Teardown

```bash
cd infrastructure && terraform destroy
```

Destroys everything Terraform created. It does **not** touch Supabase,
Upstash, Vercel, the ECR repository, or the SSM parameters — remove those
separately.

### Not included in the AWS path

- **Terraform for the frontend.** Vercel is configured through its own
  dashboard/CLI; the Vercel Terraform provider is not used.
- **CI-driven deploys.** `.github/workflows/ci.yml` tests and builds; it does
  not push images or run `terraform apply`.
- **Autoscaling, WAF, multi-environment workspaces.** Single environment,
  fixed `desired_count`.
