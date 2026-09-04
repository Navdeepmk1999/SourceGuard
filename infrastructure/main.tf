terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # State contains resolved secret values and resource metadata in plaintext.
  # Configure a remote backend with encryption + locking before running this
  # anywhere but a single workstation; local state is gitignored, not safe.
  #
  # backend "s3" {
  #   bucket       = "sourceguard-tfstate"
  #   key          = "backend/terraform.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(
      {
        Project     = var.project_name
        Environment = var.environment
        ManagedBy   = "terraform"
      },
      var.tags
    )
  }
}

locals {
  name = "${var.project_name}-${var.environment}"
}

# Filtered to AZs that actually support Fargate-capable subnets; taking a
# blind slice of all AZs occasionally lands on one that opts out.
data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${local.name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-igw" }
}

resource "aws_subnet" "public" {
  count = length(var.public_subnet_cidrs)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${local.name}-public-${count.index + 1}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb-sg"
  description = "Public ingress to the load balancer"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP from the internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_ingress_cidrs
  }

  ingress {
    description = "HTTPS from the internet (used once a certificate is attached)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_ingress_cidrs
  }

  egress {
    description = "Forward to backend tasks"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-alb-sg" }
}

resource "aws_security_group" "tasks" {
  name        = "${local.name}-tasks-sg"
  description = "Backend Fargate tasks"
  vpc_id      = aws_vpc.main.id

  # Port 8000 is reachable ONLY from the load balancer's security group, not
  # from the internet. The tasks sit in public subnets (so they can reach
  # Supabase/Upstash/Groq without the cost of a NAT gateway), which means an
  # open 0.0.0.0/0 rule here would expose the API directly and let callers
  # bypass the ALB entirely.
  ingress {
    description     = "Application traffic from the ALB only"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  # Outbound is unrestricted: tasks must reach Supabase, Upstash, Groq,
  # Together, LangSmith, ECR, and CloudWatch - all public endpoints whose
  # IP ranges are not stable enough to enumerate.
  egress {
    description = "Outbound to managed services and AWS APIs"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-tasks-sg" }
}

# ---------------------------------------------------------------------------
# Application Load Balancer
# ---------------------------------------------------------------------------

resource "aws_lb" "main" {
  name               = "${local.name}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  tags = { Name = "${local.name}-alb" }
}

resource "aws_lb_target_group" "backend" {
  name     = "${local.name}-tg"
  port     = var.container_port
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id
  # "ip" is required for Fargate: tasks register by ENI address, and there is
  # no EC2 instance to attach as a target.
  target_type = "ip"

  health_check {
    enabled             = true
    path                = var.health_check_path
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # Give in-flight SSE streams (POST /api/v1/query/stream) time to finish
  # before a deregistering task is killed. The default 300s is longer than
  # any query needs and slows deploys.
  deregistration_delay = 30

  tags = { Name = "${local.name}-tg" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
}

# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Used by the ECS agent (not the application) to pull the image and write
# logs before the container starts.
resource "aws_iam_role" "task_execution" {
  name               = "${local.name}-task-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The managed policy above does NOT grant secret reads, so injecting secrets
# via the task definition's `secrets` block fails at task start without this.
data "aws_iam_policy_document" "secrets_access" {
  count = length(var.secret_arns) > 0 ? 1 : 0

  statement {
    effect = "Allow"
    actions = [
      "ssm:GetParameters",
      "secretsmanager:GetSecretValue",
    ]
    resources = values(var.secret_arns)
  }
}

resource "aws_iam_role_policy" "secrets_access" {
  count = length(var.secret_arns) > 0 ? 1 : 0

  name   = "${local.name}-secrets-access"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.secrets_access[0].json
}

# Assumed by the application itself. Deliberately empty: the backend talks
# only to Supabase, Upstash, and HTTP AI providers, so it needs no AWS API
# permissions. It exists as the attachment point for any future grant, and
# keeps application permissions separate from the agent's.
resource "aws_iam_role" "task" {
  name               = "${local.name}-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

# ---------------------------------------------------------------------------
# ECS: cluster, task definition, service
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${local.name}-backend"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name}-backend"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "backend"
      image     = var.container_image
      essential = true

      portMappings = [
        {
          containerPort = var.container_port
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "ENVIRONMENT", value = var.environment },
        { name = "CORS_ALLOWED_ORIGINS", value = var.cors_allowed_origins },
        { name = "SUPABASE_URL", value = var.supabase_url },
      ]

      # Resolved by the ECS agent at task start; never rendered into the
      # task definition JSON or visible in the console.
      secrets = [
        for env_name, arn in var.secret_arns : {
          name      = env_name
          valueFrom = arn
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.backend.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }

      # Mirrors backend/Dockerfile's HEALTHCHECK. urllib rather than curl:
      # neither curl nor wget is present in python:3.11-slim.
      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.container_port}/health', timeout=4).status == 200 else 1)\""
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 10
      }
    }
  ])
}

resource "aws_ecs_service" "backend" {
  name            = "${local.name}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.public[*].id
    security_groups = [aws_security_group.tasks.id]
    # Required in a public subnet with no NAT gateway: without a public IP
    # the task cannot reach ECR to pull its image and will fail to start.
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = var.container_port
  }

  # Without this, the service can register targets before the listener
  # exists and the first deployment flaps.
  depends_on = [aws_lb_listener.http]

  # Rolling deploy that keeps the old task serving until the new one passes
  # its health check.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  lifecycle {
    # CI updates the image by registering a new task definition revision;
    # ignoring it here stops Terraform from reverting a deploy it did not
    # perform on the next apply.
    ignore_changes = [task_definition]
  }
}
