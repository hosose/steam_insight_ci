locals {
  cluster_name = "${var.project_name}-${var.environment}"
  az_keys      = ["a", "c"]

  public_subnets = {
    for index, key in local.az_keys : key => {
      az   = var.availability_zones[index]
      cidr = var.public_subnet_cidrs[index]
    }
  }

  app_subnets = {
    for index, key in local.az_keys : key => {
      az   = var.availability_zones[index]
      cidr = var.app_subnet_cidrs[index]
    }
  }

  db_subnets = {
    for index, key in local.az_keys : key => {
      az   = var.availability_zones[index]
      cidr = var.db_subnet_cidrs[index]
    }
  }

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
    Version     = "v3-eks-auto"
  }
}
