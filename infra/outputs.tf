output "aws_region" {
  value = var.aws_region
}

output "cluster_name" {
  value = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  value = aws_eks_cluster.main.endpoint
}

output "cluster_security_group_id" {
  value = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
}

output "auto_mode_node_role_arn" {
  value = aws_iam_role.eks_auto_node.arn
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_ids" {
  value = values(aws_subnet.public)[*].id
}

output "app_subnet_ids" {
  value = values(aws_subnet.app)[*].id
}

output "db_subnet_ids" {
  value = values(aws_subnet.db)[*].id
}

output "web_ecr_repository_url" {
  value = aws_ecr_repository.web.repository_url
}

output "was_ecr_repository_url" {
  value = aws_ecr_repository.was.repository_url
}

output "rds_endpoint" {
  value = aws_db_instance.mysql.address
}

output "rds_port" {
  value = aws_db_instance.mysql.port
}

output "rds_db_name" {
  value = var.db_name
}

output "rds_master_secret_arn" {
  value     = aws_db_instance.mysql.master_user_secret[0].secret_arn
  sensitive = true
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --region ${var.aws_region} --name ${aws_eks_cluster.main.name}"
}

output "web_ecr_repository_name" {
  description = "GitHub Actions 변수에 등록할 WEB ECR Repository 이름"
  value       = aws_ecr_repository.web.name
}

output "was_ecr_repository_name" {
  description = "GitHub Actions 변수에 등록할 WAS ECR Repository 이름"
  value       = aws_ecr_repository.was.name
}

output "github_actions_ci_role_arn" {
  description = "GitHub Actions의 AWS_ROLE_ARN 변수에 등록할 IAM Role ARN"
  value       = try(aws_iam_role.github_actions_ci[0].arn, null)
}

output "github_actions_oidc_provider_arn" {
  description = "GitHub Actions IAM Role이 신뢰하는 OIDC Provider ARN"
  value       = var.enable_github_actions_ci ? local.github_actions_oidc_provider_arn : null
}

output "github_actions_ci_subject" {
  description = "GitHub Actions IAM Role 신뢰 정책에서 허용하는 Repository와 Branch subject"
  value       = var.enable_github_actions_ci ? local.github_actions_ci_subject : null
}
