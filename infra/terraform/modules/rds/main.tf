# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo RDS — Aurora PostgreSQL 16 HA
# Riferimento piano: F0.2 — infra/terraform/modules/rds
#
# Cluster: 1 writer + 2 reader in 3 AZ separate
# Cifratura: KMS CMK dedicato
# Backup: 35 giorni, point-in-time recovery
# Storage: autoscaling 100GB → 10TB

locals {
  cluster_id = "${var.project}-${var.environment}"
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "rds"
    ManagedBy   = "Terraform"
  }
}

# ── Subnet Group (subnet isolate — nessun accesso internet) ────────
resource "aws_db_subnet_group" "main" {
  name        = "${local.cluster_id}-aurora"
  description = "Subnet group Aurora PostgreSQL — subnet isolate"
  subnet_ids  = var.isolated_subnet_ids
  tags        = merge(local.tags, { Name = "${local.cluster_id}-aurora-subnet-group" })
}

# ── Cluster Parameter Group ────────────────────────────────────────
resource "aws_rds_cluster_parameter_group" "main" {
  name        = "${local.cluster_id}-aurora-pg16"
  family      = "aurora-postgresql16"
  description = "Parameter group VerumDoc Aurora PostgreSQL 16"

  # Ottimizzazioni per workload VerumDoc
  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements,auto_explain"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "1000" # Log query > 1 secondo
  }
  parameter {
    name  = "log_connections"
    value = "1"
  }
  # R04: dal values di Helm (postgres.maxConnessioni): verifica_manifest.py
  # controlla che PgBouncer e i client diretti ci stiano
  parameter {
    name  = "max_connections"
    value = tostring(var.max_connections)
  }
  parameter {
    name  = "work_mem"
    value = "16384" # 16 MB per query
  }

  tags = local.tags
}

resource "aws_db_parameter_group" "main" {
  name        = "${local.cluster_id}-aurora-instance-pg16"
  family      = "aurora-postgresql16"
  description = "Instance parameter group VerumDoc Aurora PostgreSQL 16"
  tags        = local.tags
}

# ── Secrets Manager — password DB (generata automaticamente) ───────
resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${local.cluster_id}/rds/master-password"
  description             = "Password master RDS Aurora ${local.cluster_id}"
  recovery_window_in_days = 30
  kms_key_id              = var.kms_rds_key_arn
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_rotation" "db_password" {
  secret_id           = aws_secretsmanager_secret.db_password.id
  rotation_lambda_arn = null # AWS managed rotation
  rotation_rules {
    automatically_after_days = 90
  }
}

# ── Aurora Cluster ─────────────────────────────────────────────────
resource "aws_rds_cluster" "main" {
  cluster_identifier          = "${local.cluster_id}-aurora"
  engine                      = "aurora-postgresql"
  engine_version              = var.engine_version
  database_name               = var.db_name
  master_username             = var.db_username
  manage_master_user_password = true # AWS gestisce la password in Secrets Manager

  db_subnet_group_name            = aws_db_subnet_group.main.name
  vpc_security_group_ids          = [var.sg_rds_id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.main.name

  # Storage
  storage_encrypted = true
  kms_key_id        = var.kms_rds_key_arn
  allocated_storage = var.min_storage_gb

  # Backup e recovery
  backup_retention_period      = var.backup_retention_days
  preferred_backup_window      = "03:00-04:00" # UTC+1 → 4:00-5:00 ora italiana
  preferred_maintenance_window = "sun:04:00-sun:05:00"
  copy_tags_to_snapshot        = true
  deletion_protection          = true # Blocca eliminazione accidentale

  # Multi-AZ — failover automatico
  availability_zones = ["eu-south-1a", "eu-south-1b", "eu-south-1c"]

  # Performance Insights
  performance_insights_enabled          = true
  performance_insights_kms_key_id       = var.kms_rds_key_arn
  performance_insights_retention_period = 7

  # Log esportati in CloudWatch
  enabled_cloudwatch_logs_exports = ["postgresql"]

  # Aggiornamenti minori automatici
  apply_immediately = false

  # Prevent destroy in produzione
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [availability_zones]
  }

  tags = merge(local.tags, { Name = "${local.cluster_id}-aurora-cluster" })
}

# ── Istanze cluster: 1 writer + 2 reader ──────────────────────────
resource "aws_rds_cluster_instance" "writer" {
  count = 1

  identifier         = "${local.cluster_id}-aurora-writer-${count.index + 1}"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = var.db_instance_class
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version

  db_parameter_group_name = aws_db_parameter_group.main.name
  publicly_accessible     = false

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = var.kms_rds_key_arn
  performance_insights_retention_period = 7
  monitoring_interval                   = 60 # Enhanced monitoring ogni 60s
  monitoring_role_arn                   = aws_iam_role.rds_monitoring.arn

  auto_minor_version_upgrade = true
  apply_immediately          = false

  tags = merge(local.tags, {
    Name = "${local.cluster_id}-aurora-writer-${count.index + 1}"
    Role = "writer"
  })
}

resource "aws_rds_cluster_instance" "reader" {
  count = 2

  identifier         = "${local.cluster_id}-aurora-reader-${count.index + 1}"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = var.db_instance_class
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version

  db_parameter_group_name = aws_db_parameter_group.main.name
  publicly_accessible     = false

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = var.kms_rds_key_arn
  performance_insights_retention_period = 7
  monitoring_interval                   = 60
  monitoring_role_arn                   = aws_iam_role.rds_monitoring.arn

  auto_minor_version_upgrade = true
  apply_immediately          = false

  tags = merge(local.tags, {
    Name = "${local.cluster_id}-aurora-reader-${count.index + 1}"
    Role = "reader"
  })

  depends_on = [aws_rds_cluster_instance.writer]
}

# ── IAM Role per Enhanced Monitoring ──────────────────────────────
resource "aws_iam_role" "rds_monitoring" {
  name = "${local.cluster_id}-rds-monitoring"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
  role       = aws_iam_role.rds_monitoring.name
}
