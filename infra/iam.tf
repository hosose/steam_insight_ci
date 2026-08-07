# EKS Auto Mode Cluster Role
# Auto Mode가 컴퓨팅, 네트워크, ALB, 블록 스토리지를 관리할 수 있도록 한다.
data "aws_iam_policy_document" "eks_cluster_assume" {
  statement {
    effect = "Allow"
    actions = [
      "sts:AssumeRole",
      "sts:TagSession"
    ]

    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eks_cluster" {
  name               = "${local.cluster_name}-cluster-role"
  assume_role_policy = data.aws_iam_policy_document.eks_cluster_assume.json
}

locals {
  eks_auto_cluster_policies = {
    cluster        = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
    compute        = "arn:aws:iam::aws:policy/AmazonEKSComputePolicy"
    block_storage  = "arn:aws:iam::aws:policy/AmazonEKSBlockStoragePolicyV2"
    load_balancing = "arn:aws:iam::aws:policy/AmazonEKSLoadBalancingPolicy"
    networking     = "arn:aws:iam::aws:policy/AmazonEKSNetworkingPolicy"
  }
}

resource "aws_iam_role_policy_attachment" "eks_cluster" {
  for_each = local.eks_auto_cluster_policies

  role       = aws_iam_role.eks_cluster.name
  policy_arn = each.value
}

# EKS Auto Mode Node Role
# 기존 Managed Node Group Role보다 필요한 정책 수가 적다.
data "aws_iam_policy_document" "eks_auto_node_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eks_auto_node" {
  name               = "${local.cluster_name}-auto-node-role"
  assume_role_policy = data.aws_iam_policy_document.eks_auto_node_assume.json
}

locals {
  eks_auto_node_policies = {
    worker = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodeMinimalPolicy"
    ecr    = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPullOnly"
  }
}

resource "aws_iam_role_policy_attachment" "eks_auto_node" {
  for_each = local.eks_auto_node_policies

  role       = aws_iam_role.eks_auto_node.name
  policy_arn = each.value
}
