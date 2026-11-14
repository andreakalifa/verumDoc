# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo KMS — Chiavi CMK per cifratura dati a riposo
# Riferimento piano: F0.2 — infra/terraform/modules/kms
# Crea: CMK per S3 AIP, CMK per RDS, CMK per secrets EKS
# Rotazione annuale automatica obbligatoria

locals {
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "kms"
    ManagedBy   = "Terraform"
  }
}

# ── CMK per S3 AIP (WORM — documenti conservati) ──────────────────
resource "aws_kms_key" "s3_aip" {
  description             = "${var.project}-${var.environment} — S3 AIP documenti conservati"
  deletion_window_in_days = 30
  enable_key_rotation     = true # Rotazione annuale obbligatoria
  multi_region            = true # Per disaster recovery cross-region

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccess"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowS3Service"
        Effect    = "Allow"
        Principal = { Service = "s3.amazonaws.com" }
        Action    = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource  = "*"
      }
    ]
  })

  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-s3-aip" })
}

resource "aws_kms_alias" "s3_aip" {
  name          = "alias/${var.project}-${var.environment}-s3-aip"
  target_key_id = aws_kms_key.s3_aip.key_id
}

# ── CMK per RDS Aurora (dati a riposo) ────────────────────────────
resource "aws_kms_key" "rds" {
  description             = "${var.project}-${var.environment} — RDS Aurora PostgreSQL"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccess"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowRDSService"
        Effect    = "Allow"
        Principal = { Service = "rds.amazonaws.com" }
        Action    = ["kms:GenerateDataKey", "kms:Decrypt", "kms:CreateGrant", "kms:DescribeKey"]
        Resource  = "*"
      }
    ]
  })

  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-rds" })
}

resource "aws_kms_alias" "rds" {
  name          = "alias/${var.project}-${var.environment}-rds"
  target_key_id = aws_kms_key.rds.key_id
}

# ── CMK per Secrets EKS / Sealed Secrets ──────────────────────────
resource "aws_kms_key" "secrets" {
  description             = "${var.project}-${var.environment} — Secrets EKS e Vault"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-secrets" })
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/${var.project}-${var.environment}-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

data "aws_caller_identity" "current" {}
