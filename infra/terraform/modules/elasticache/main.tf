# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo ElastiCache — Redis 7 Cluster Mode
# Riferimento piano: F0.2 — infra/terraform/modules/elasticache
#
# Cluster mode: 3 shard x 1 replica = 6 nodi totali
# TLS abilitato obbligatoriamente
# Usato per: Celery broker, rate limiting Redis, cache sessioni

locals {
  cluster_id = "${var.project}-${var.environment}-redis"
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "elasticache"
    ManagedBy   = "Terraform"
  }
}

resource "aws_elasticache_subnet_group" "main" {
  name        = "${local.cluster_id}-subnet-group"
  description = "Subnet group Redis — subnet isolate"
  subnet_ids  = var.isolated_subnet_ids
  tags        = local.tags
}

resource "aws_elasticache_parameter_group" "main" {
  name        = "${local.cluster_id}-params"
  family      = "redis7"
  description = "Parameter group Redis 7 VerumDoc"

  parameter {
    name  = "cluster-enabled"
    value = "yes"
  }
  parameter {
    name  = "maxmemory-policy"
    value = "allkeys-lru" # Evict LRU quando memoria piena
  }
  parameter {
    name  = "timeout"
    value = "300" # 5 minuti idle timeout
  }

  tags = local.tags
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = local.cluster_id
  description          = "Redis 7 cluster mode — ${var.project} ${var.environment}"

  node_type            = var.node_type
  engine_version       = var.engine_version
  parameter_group_name = aws_elasticache_parameter_group.main.name
  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [var.sg_redis_id]

  # Cluster mode
  num_node_groups         = var.num_shards         # 3 shard
  replicas_per_node_group = var.replicas_per_shard # 1 replica per shard

  # TLS obbligatorio
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true

  # Autenticazione con token
  auth_token                 = random_password.redis_auth.result
  auth_token_update_strategy = "ROTATE"

  # Auto failover
  automatic_failover_enabled = true
  multi_az_enabled           = true

  # Manutenzione
  preferred_cache_cluster_azs = ["eu-south-1a", "eu-south-1b", "eu-south-1c"]
  maintenance_window          = "sun:05:00-sun:06:00"
  snapshot_window             = "04:00-05:00"
  snapshot_retention_limit    = 7

  # Aggiornamenti automatici versione minore
  auto_minor_version_upgrade = true
  apply_immediately          = false

  tags = merge(local.tags, { Name = local.cluster_id })
}

resource "random_password" "redis_auth" {
  length  = 32
  special = false # Redis auth token: solo alfanumerici
}

# Salva il token in Secrets Manager
resource "aws_secretsmanager_secret" "redis_auth" {
  name        = "${var.project}-${var.environment}/redis/auth-token"
  description = "Redis auth token — ${local.cluster_id}"
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "redis_auth" {
  secret_id = aws_secretsmanager_secret.redis_auth.id
  secret_string = jsonencode({
    auth_token             = random_password.redis_auth.result
    configuration_endpoint = aws_elasticache_replication_group.main.configuration_endpoint_address
    port                   = 6379
  })
}
