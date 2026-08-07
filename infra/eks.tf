resource "aws_eks_cluster" "main" {
  name     = local.cluster_name
  role_arn = aws_iam_role.eks_cluster.arn
  version  = var.kubernetes_version

  # Auto Mode는 기존 VPC CNI/CoreDNS/kube-proxy Add-on을 직접 부트스트랩하지 않는다.
  bootstrap_self_managed_addons = false

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  # AWS 관리형 Karpenter 기반 NodePool을 사용한다.
  compute_config {
    enabled       = true
    node_pools    = ["general-purpose", "system"]
    node_role_arn = aws_iam_role.eks_auto_node.arn
  }

  kubernetes_network_config {
    service_ipv4_cidr = "172.20.0.0/16"

    # AWS Load Balancer Controller를 직접 설치하지 않고 Auto Mode가 ALB/NLB를 관리한다.
    elastic_load_balancing {
      enabled = true
    }
  }

  # EBS CSI Driver를 직접 설치하지 않고 Auto Mode의 블록 스토리지 기능을 사용한다.
  storage_config {
    block_storage {
      enabled = true
    }
  }

  vpc_config {
    # EKS Control Plane ENI와 기본 Auto Mode NodeClass는 Private App Subnet을 사용한다.
    subnet_ids = values(aws_subnet.app)[*].id

    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.cluster_endpoint_public_access_cidrs
  }

  enabled_cluster_log_types = [
    "api",
    "audit",
    "authenticator",
    "controllerManager",
    "scheduler"
  ]

  tags = {
    Name = local.cluster_name
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster,
    aws_iam_role_policy_attachment.eks_auto_node,
    aws_route_table_association.app,
    aws_cloudwatch_log_group.eks
  ]
}

# HPA가 CPU/메모리 지표를 사용할 수 있도록 Metrics Server 커뮤니티 Add-on만 추가한다.
resource "aws_eks_addon" "metrics_server" {
  cluster_name = aws_eks_cluster.main.name
  addon_name   = "metrics-server"

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  tags = {
    Name = "${local.cluster_name}-metrics-server"
  }
}

resource "aws_eks_access_entry" "admin" {
  for_each = var.additional_admin_role_arns

  cluster_name  = aws_eks_cluster.main.name
  principal_arn = each.value
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "admin" {
  for_each = var.additional_admin_role_arns

  cluster_name  = aws_eks_cluster.main.name
  principal_arn = each.value
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.admin]
}
