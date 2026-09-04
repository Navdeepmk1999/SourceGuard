output "alb_dns_name" {
  description = <<-EOT
    Public DNS name of the load balancer. This is the backend origin the
    Vercel frontend points at: set NEXT_PUBLIC_API_URL to
    http://<this>/api/v1 (or https:// once a certificate is attached).
  EOT
  value       = aws_lb.main.dns_name
}

output "api_base_url" {
  description = "Ready-to-use value for the frontend's NEXT_PUBLIC_API_URL."
  value       = "http://${aws_lb.main.dns_name}/api/v1"
}

output "health_check_url" {
  description = "Liveness probe URL, useful for verifying a deploy from the shell."
  value       = "http://${aws_lb.main.dns_name}${var.health_check_path}"
}

output "ecs_cluster_name" {
  description = "ECS cluster name (for `aws ecs update-service` deploys)."
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name (for `aws ecs update-service` deploys)."
  value       = aws_ecs_service.backend.name
}

output "task_definition_family" {
  description = "Task definition family; new revisions are registered against this."
  value       = aws_ecs_task_definition.backend.family
}

output "cloudwatch_log_group" {
  description = "Log group holding backend container logs."
  value       = aws_cloudwatch_log_group.backend.name
}

output "vpc_id" {
  description = "VPC the service runs in."
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs hosting the ALB and Fargate tasks."
  value       = aws_subnet.public[*].id
}

output "task_execution_role_arn" {
  description = "Role the ECS agent assumes to pull images and read secrets."
  value       = aws_iam_role.task_execution.arn
}

output "task_role_arn" {
  description = "Role the application container itself assumes."
  value       = aws_iam_role.task.arn
}
