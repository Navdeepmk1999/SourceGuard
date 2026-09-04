variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name prefix applied to every created resource."
  type        = string
  default     = "sourceguard"
}

variable "environment" {
  description = "Deployment environment (used in resource names and tags)."
  type        = string
  default     = "production"
}

# ---- Networking -----------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  description = <<-EOT
    CIDRs for the public subnets. At least two, in different AZs: an
    Application Load Balancer requires subnets in a minimum of two
    Availability Zones.
  EOT
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]

  validation {
    condition     = length(var.public_subnet_cidrs) >= 2
    error_message = "An ALB requires at least two subnets in different Availability Zones."
  }
}

variable "allowed_ingress_cidrs" {
  description = "CIDRs permitted to reach the load balancer. Narrow this for a private deployment."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# ---- Container / service --------------------------------------------------

variable "container_image" {
  description = <<-EOT
    Fully-qualified image URI for the backend, e.g.
    <account>.dkr.ecr.<region>.amazonaws.com/sourceguard-backend:<tag>.
    Built from backend/Dockerfile.
  EOT
  type        = string
}

variable "container_port" {
  description = "Port the backend listens on (uvicorn in backend/Dockerfile)."
  type        = number
  default     = 8000
}

variable "desired_count" {
  description = "Number of Fargate tasks to run."
  type        = number
  default     = 2
}

variable "task_cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU)."
  type        = string
  default     = "512"
}

variable "task_memory" {
  description = "Fargate task memory in MiB. Must be a valid pairing with task_cpu."
  type        = string
  default     = "1024"
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the backend log group."
  type        = number
  default     = 30
}

variable "health_check_path" {
  description = "ALB target group health check path (app/main.py exposes /health)."
  type        = string
  default     = "/health"
}

# ---- Application configuration -------------------------------------------

variable "cors_allowed_origins" {
  description = "Comma-separated origins the API accepts, i.e. the Vercel frontend URL."
  type        = string
  default     = "http://localhost:3000"
}

variable "supabase_url" {
  description = "Supabase project URL. Not a secret - used to build the JWKS endpoint."
  type        = string
  default     = ""
}

variable "secret_arns" {
  description = <<-EOT
    Map of container environment variable name -> SSM Parameter Store or
    Secrets Manager ARN. Injected via the task definition's `secrets` block
    so values are resolved at task start and never appear in the task
    definition, `terraform show`, or CloudWatch.

    Expected keys: DATABASE_URL, REDIS_URL, GROQ_API_KEY, TOGETHER_API_KEY,
    LANGSMITH_API_KEY.
  EOT
  type        = map(string)
  default     = {}
}

variable "tags" {
  description = "Additional tags merged onto every resource."
  type        = map(string)
  default     = {}
}
