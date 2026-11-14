# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo OpenSearch — Cluster per ricerca metadati Allegato 5
# Riferimento piano: F0.2 — infra/terraform/modules/opensearch
#
# 3 nodi dati r6g.large.search + 1 master dedicato
# TLS obbligatorio, cifratura a riposo con KMS
# 1 indice per tenant per isolamento dei metadati

locals {
  domain_name = "${var.project}-${var.environment}"
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "opensearch"
    ManagedBy   = "Terraform"
  }
}

# Security Group per OpenSearch (non riutilizzato da VPC module — dedicated)
resource "aws_security_group" "opensearch" {
  name        = "${local.domain_name}-opensearch"
  description = "OpenSearch — accetta solo da EKS nodes sulla porta 443"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"] # Solo dalla VPC
    description = "HTTPS da VPC"
  }

  tags = merge(local.tags, { Name = "${local.domain_name}-sg-opensearch" })
}

# Il ruolo collegato al servizio esiste una sola volta per account AWS: se è già
# presente (per esempio creato da un altro ambiente) impostare
# create_service_linked_role = false, altrimenti la creazione fallisce.
resource "aws_iam_service_linked_role" "opensearch" {
  count            = var.create_service_linked_role ? 1 : 0
  aws_service_name = "es.amazonaws.com"
}

resource "aws_opensearch_domain" "main" {
  domain_name    = local.domain_name
  engine_version = var.engine_version

  cluster_config {
    instance_type          = var.instance_type
    instance_count         = var.instance_count
    zone_awareness_enabled = true

    zone_awareness_config {
      availability_zone_count = 3
    }

    # Master node dedicato per stabilità cluster
    dedicated_master_enabled = true
    dedicated_master_type    = var.dedicated_master_type
    dedicated_master_count   = 3
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = var.volume_size_gb
    throughput  = 125
    iops        = 3000
  }

  vpc_options {
    subnet_ids         = slice(var.isolated_subnet_ids, 0, 3)
    security_group_ids = [aws_security_group.opensearch.id]
  }

  # Cifratura a riposo con KMS
  encrypt_at_rest {
    enabled    = true
    kms_key_id = var.kms_s3_key_arn
  }

  # TLS obbligatorio
  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  # Autenticazione HTTPS fine-grained
  advanced_security_options {
    enabled                        = true
    internal_user_database_enabled = false
    master_user_options {
      master_user_arn = data.aws_caller_identity.current.arn
    }
  }

  # Log in CloudWatch
  log_publishing_options {
    log_type                 = "INDEX_SLOW_LOGS"
    cloudwatch_log_group_arn = aws_cloudwatch_log_group.opensearch_slow.arn
  }
  log_publishing_options {
    log_type                 = "SEARCH_SLOW_LOGS"
    cloudwatch_log_group_arn = aws_cloudwatch_log_group.opensearch_slow.arn
  }

  # Auto tune per ottimizzazione automatica
  auto_tune_options {
    desired_state       = "ENABLED"
    rollback_on_disable = "NO_ROLLBACK"
  }

  advanced_options = {
    "rest.action.multi.allow_explicit_index" = "true"
    "override_main_response_version"         = "false"
  }

  tags       = merge(local.tags, { Name = local.domain_name })
  depends_on = [aws_iam_service_linked_role.opensearch]
}

resource "aws_cloudwatch_log_group" "opensearch_slow" {
  name              = "/aws/opensearch/${local.domain_name}/slow-logs"
  retention_in_days = 7
  tags              = local.tags
}

data "aws_caller_identity" "current" {}
