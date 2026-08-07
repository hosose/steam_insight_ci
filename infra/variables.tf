variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "us-east-2"
}

variable "project_name" {
  description = "리소스 이름에 사용할 프로젝트명"
  type        = string
  default     = "de-ai-07-eks-auto"
}

variable "environment" {
  description = "환경 구분"
  type        = string
  default     = "dev"
}

variable "vpc_cidr" {
  description = "VPC CIDR"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Multi-AZ 구성에 사용할 가용 영역 2개"
  type        = list(string)
  default     = ["us-east-2a", "us-east-2c"]

  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "이 구성은 정확히 2개의 가용 영역을 사용합니다."
  }
}

variable "public_subnet_cidrs" {
  description = "Public Subnet CIDR 목록"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "app_subnet_cidrs" {
  description = "EKS Auto Mode Node/Pod용 Private Subnet CIDR 목록"
  type        = list(string)
  default     = ["10.0.11.0/24", "10.0.12.0/24"]
}

variable "db_subnet_cidrs" {
  description = "RDS 전용 Private Subnet CIDR 목록"
  type        = list(string)
  default     = ["10.0.21.0/24", "10.0.22.0/24"]
}

variable "kubernetes_version" {
  description = "EKS Kubernetes 버전"
  type        = string
  default     = "1.35"
}

variable "cluster_endpoint_public_access_cidrs" {
  description = "EKS Public API 접근 CIDR. 운영에서는 관리자 공인 IP/32로 제한"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "db_instance_class" {
  description = "RDS MySQL 인스턴스 클래스"
  type        = string
  default     = "db.t3.micro"
}

variable "db_allocated_storage" {
  description = "RDS 초기 스토리지 용량(GB)"
  type        = number
  default     = 20
}

variable "db_name" {
  description = "애플리케이션 데이터베이스 이름"
  type        = string
  default     = "appdb"
}

variable "db_username" {
  description = "RDS 마스터 사용자명"
  type        = string
  default     = "dbadmin"
}

variable "additional_admin_role_arns" {
  description = "추가 EKS 관리자 IAM Role ARN 목록"
  type        = set(string)
  default     = []
}

# ============================================================
# GitHub Actions CI 설정
# ============================================================

variable "enable_github_actions_ci" {
  description = "GitHub Actions OIDC 및 ECR Push Role 생성 여부"
  type        = bool
  default     = true
}

# 중요:
# - 이 Terraform이 OIDC Provider를 생성했다면 계속 true로 유지한다.
# - AWS 계정에 Provider가 이미 있었고 이 Terraform State가 관리하지 않을 때만 false로 설정한다.
variable "create_github_oidc_provider" {
  description = "AWS 계정에 GitHub OIDC Provider를 새로 생성할지 여부. 기존 Provider를 다른 State가 관리하면 false"
  type        = bool
  default     = true
}

variable "github_owner" {
  description = "tf-k8s-ci 저장소를 소유한 GitHub 사용자명 또는 Organization명"
  type        = string
  default     = "YOUR_GITHUB_ID"

  validation {
    condition = (
      !var.enable_github_actions_ci ||
      (
        length(trimspace(var.github_owner)) > 0 &&
        !contains(["YOUR_GITHUB_ID", "GITHUB_ID", "CHANGE_ME", "REPLACE_ME"], upper(trimspace(var.github_owner))) &&
        can(regex("^[A-Za-z0-9][A-Za-z0-9-]{0,38}$", trimspace(var.github_owner)))
      )
    )
    error_message = "enable_github_actions_ci=true이면 github_owner를 실제 GitHub 사용자명 또는 Organization명으로 입력해야 합니다."
  }
}

variable "github_ci_repository" {
  description = "CI Source Repository 이름"
  type        = string
  default     = "tf-k8s-ci"

  validation {
    condition = (
      !var.enable_github_actions_ci ||
      can(regex("^[A-Za-z0-9._-]+$", trimspace(var.github_ci_repository)))
    )
    error_message = "github_ci_repository에는 GitHub Repository 이름만 입력해야 합니다. 예: tf-k8s-ci"
  }
}

variable "github_ci_branch" {
  description = "AWS CI Role 사용을 허용할 GitHub Branch"
  type        = string
  default     = "main"

  validation {
    condition = (
      !var.enable_github_actions_ci ||
      (
        length(trimspace(var.github_ci_branch)) > 0 &&
        !strcontains(trimspace(var.github_ci_branch), " ") &&
        !strcontains(trimspace(var.github_ci_branch), "~") &&
        !strcontains(trimspace(var.github_ci_branch), "^") &&
        !strcontains(trimspace(var.github_ci_branch), ":") &&
        !strcontains(trimspace(var.github_ci_branch), "?") &&
        !strcontains(trimspace(var.github_ci_branch), "*") &&
        !strcontains(trimspace(var.github_ci_branch), "[") &&
        !strcontains(trimspace(var.github_ci_branch), "\\") &&
        !strcontains(trimspace(var.github_ci_branch), "..")
      )
    )
    error_message = "github_ci_branch에 유효한 Git Branch 이름을 입력해야 합니다. 예: main"
  }
}


variable "github_owner_id" {
  description = "GitHub Repository Owner numeric ID"
  type        = string
}

variable "github_ci_repository_id" {
  description = "GitHub Repository numeric ID"
  type        = string
}
