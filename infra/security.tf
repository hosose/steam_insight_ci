resource "aws_security_group" "rds" {
  name        = "${local.cluster_name}-rds-sg"
  description = "MySQL access only from the EKS cluster security group"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "MySQL from EKS Auto Mode Nodes and Pods"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_eks_cluster.main.vpc_config[0].cluster_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.cluster_name}-rds-sg"
  }
}
