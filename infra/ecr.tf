resource "aws_ecr_repository" "web" {
  name                 = "${local.cluster_name}/web"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "was" {
  name                 = "${local.cluster_name}/was"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

locals {
  ecr_repositories = {
    web = aws_ecr_repository.web.name
    was = aws_ecr_repository.was.name
  }
}

resource "aws_ecr_lifecycle_policy" "main" {
  for_each = local.ecr_repositories

  repository = each.value

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "최근 이미지 10개만 유지"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = {
        type = "expire"
      }
    }]
  })
}
