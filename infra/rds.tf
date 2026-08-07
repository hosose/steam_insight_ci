resource "aws_db_subnet_group" "main" {
  name       = "${local.cluster_name}-db-subnet-group"
  subnet_ids = values(aws_subnet.db)[*].id

  tags = {
    Name = "${local.cluster_name}-db-subnet-group"
  }
}

resource "aws_db_instance" "mysql" {
  identifier = "${local.cluster_name}-mysql"

  engine         = "mysql"
  engine_version = "8.0"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.db_name
  username = var.db_username

  # AWS Secrets Manager가 마스터 비밀번호를 생성하고 관리한다.
  manage_master_user_password = true

  multi_az               = true
  publicly_accessible    = false
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  backup_retention_period = 7
  backup_window           = "18:00-19:00"
  maintenance_window      = "sun:19:00-sun:20:00"

  auto_minor_version_upgrade = true
  apply_immediately          = true
  copy_tags_to_snapshot      = true
  deletion_protection        = false
  skip_final_snapshot        = true

  tags = {
    Name = "${local.cluster_name}-mysql"
  }
}
