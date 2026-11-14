# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo S3 — Storage AIP con Object Lock WORM
# Riferimento piano: F0.2 — infra/terraform/modules/s3
#
# Bucket:
#   aip-prod:    AIP conservati — Object Lock COMPLIANCE (immutabile)
#   aip-backup:  Replica cross-region per DR
#   logs-audit:  Log di sistema — append-only
#
# Object Lock in modalità COMPLIANCE: NESSUNO (incluso root) può
# cancellare un oggetto prima della retention scaduta.
# Questo è il requisito normativo per la conservazione AGID.

locals {
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "s3"
    ManagedBy   = "Terraform"
    Normativa   = "AGID-2022-CAD-44"
  }
}

# ── IAM Role per replicazione cross-region ─────────────────────────
resource "aws_iam_role" "s3_replication" {
  name = "${var.project}-${var.environment}-s3-replication"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "s3_replication" {
  name = "s3-replication-policy"
  role = aws_iam_role.s3_replication.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-aip-prod"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObjectVersionForReplication", "s3:GetObjectVersionAcl", "s3:GetObjectVersionTagging"]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-aip-prod/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-aip-backup/*"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.kms_s3_key_arn
      }
    ]
  })
}

# ── BUCKET AIP PRODUZIONE (WORM Object Lock Compliance) ─────────────
resource "aws_s3_bucket" "aip_prod" {
  bucket = "${var.project}-${var.environment}-aip-prod"
  # Object Lock va abilitato alla CREAZIONE del bucket (provider AWS >= 4): senza,
  # aws_s3_bucket_object_lock_configuration fallisce o non protegge nulla.
  object_lock_enabled = true

  # Prevent terraform destroy — protezione extra oltre Object Lock
  lifecycle {
    prevent_destroy = true
  }

  tags = merge(local.tags, {
    Name       = "${var.project}-${var.environment}-aip-prod"
    Purpose    = "Conservazione AIP a norma AGID 2022"
    ObjectLock = "COMPLIANCE"
  })
}

# Object Lock COMPLIANCE: immutabile per la durata di retention
# ATTENZIONE: una volta abilitato, NON può essere disabilitato
resource "aws_s3_bucket_object_lock_configuration" "aip_prod" {
  bucket     = aws_s3_bucket.aip_prod.id
  depends_on = [aws_s3_bucket_versioning.aip_prod]

  object_lock_enabled = "Enabled"

  rule {
    default_retention {
      mode  = "COMPLIANCE"
      years = var.default_retention_years
    }
  }
}

resource "aws_s3_bucket_versioning" "aip_prod" {
  bucket = aws_s3_bucket.aip_prod.id
  versioning_configuration {
    status = "Enabled" # Obbligatorio per Object Lock
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "aip_prod" {
  bucket = aws_s3_bucket.aip_prod.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_s3_key_arn
    }
    bucket_key_enabled = true # Riduce costi KMS
  }
}

# Blocca TUTTO l'accesso pubblico
resource "aws_s3_bucket_public_access_block" "aip_prod" {
  bucket                  = aws_s3_bucket.aip_prod.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle rules: tiered storage automatico
resource "aws_s3_bucket_lifecycle_configuration" "aip_prod" {
  bucket = aws_s3_bucket.aip_prod.id

  # Tier standard (documenti generici)
  rule {
    id     = "standard-tiering"
    status = "Enabled"

    filter {
      tag {
        key   = "conservation_class"
        value = "standard"
      }
    }

    transition {
      days          = 365 # HOT → WARM dopo 1 anno
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 1825 # WARM → COLD dopo 5 anni
      storage_class = "GLACIER_IR"
    }
    transition {
      days          = 3650 # COLD → ARCHIVAL dopo 10 anni
      storage_class = "DEEP_ARCHIVE"
    }
  }

  # Tier fiscale (fatture — 10 anni hot per obbligo fiscale)
  rule {
    id     = "fiscale-tiering"
    status = "Enabled"

    filter {
      tag {
        key   = "conservation_class"
        value = "fiscale"
      }
    }

    transition {
      days          = 3650 # HOT 10 anni, poi WARM
      storage_class = "STANDARD_IA"
    }
  }

  # Tier permanente (archivio storico — non scade mai)
  rule {
    id     = "permanente-tiering"
    status = "Enabled"

    filter {
      tag {
        key   = "conservation_class"
        value = "permanente"
      }
    }

    transition {
      days          = 3650
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 7300
      storage_class = "GLACIER_IR"
    }
  }

  # Abort incomplete multipart uploads (pulizia)
  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Replicazione cross-region per DR
resource "aws_s3_bucket_replication_configuration" "aip_prod" {
  depends_on = [aws_s3_bucket_versioning.aip_prod]
  bucket     = aws_s3_bucket.aip_prod.id
  role       = aws_iam_role.s3_replication.arn

  rule {
    id     = "replicate-all-to-backup"
    status = "Enabled"

    filter {} # Replica tutto

    destination {
      bucket        = aws_s3_bucket.aip_backup.arn
      storage_class = "STANDARD_IA"

      encryption_configuration {
        replica_kms_key_id = var.kms_s3_key_arn
      }

      replication_time {
        status = "Enabled"
        time { minutes = 15 }
      }

      metrics {
        status = "Enabled"
        event_threshold { minutes = 15 }
      }
    }

    source_selection_criteria {
      sse_kms_encrypted_objects { status = "Enabled" }
    }

    # Replica anche le impostazioni Object Lock
    delete_marker_replication { status = "Disabled" }
  }
}

# Bucket policy: forza HTTPS e require Object Lock headers
resource "aws_s3_bucket_policy" "aip_prod" {
  bucket = aws_s3_bucket.aip_prod.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyHTTP"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = ["${aws_s3_bucket.aip_prod.arn}", "${aws_s3_bucket.aip_prod.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "DenyNonEncryptedObjects"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.aip_prod.arn}/*"
        Condition = {
          StringNotEquals = { "s3:x-amz-server-side-encryption" = "aws:kms" }
        }
      }
    ]
  })
}

# ── BUCKET AIP BACKUP (replica cross-region) ──────────────────────
resource "aws_s3_bucket" "aip_backup" {
  provider = aws.backup_region
  bucket   = "${var.project}-${var.environment}-aip-backup"
  # Object Lock va abilitato alla CREAZIONE del bucket (provider AWS >= 4): senza,
  # aws_s3_bucket_object_lock_configuration fallisce o non protegge nulla.
  object_lock_enabled = true

  lifecycle { prevent_destroy = true }

  tags = merge(local.tags, {
    Name    = "${var.project}-${var.environment}-aip-backup"
    Purpose = "Replica DR cross-region"
  })
}

resource "aws_s3_bucket_versioning" "aip_backup" {
  provider = aws.backup_region
  bucket   = aws_s3_bucket.aip_backup.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_object_lock_configuration" "aip_backup" {
  provider            = aws.backup_region
  bucket              = aws_s3_bucket.aip_backup.id
  depends_on          = [aws_s3_bucket_versioning.aip_backup]
  object_lock_enabled = "Enabled"
}

resource "aws_s3_bucket_public_access_block" "aip_backup" {
  provider                = aws.backup_region
  bucket                  = aws_s3_bucket.aip_backup.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "aip_backup" {
  provider = aws.backup_region
  bucket   = aws_s3_bucket.aip_backup.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_s3_key_arn
    }
    bucket_key_enabled = true
  }
}

# ── BUCKET LOGS AUDIT (append-only) ──────────────────────────────
resource "aws_s3_bucket" "logs_audit" {
  bucket = "${var.project}-${var.environment}-logs-audit"
  # Object Lock va abilitato alla CREAZIONE del bucket (provider AWS >= 4): senza,
  # aws_s3_bucket_object_lock_configuration fallisce o non protegge nulla.
  object_lock_enabled = true
  tags = merge(local.tags, {
    Name    = "${var.project}-${var.environment}-logs-audit"
    Purpose = "Log audit immutabili"
  })
}

resource "aws_s3_bucket_versioning" "logs_audit" {
  bucket = aws_s3_bucket.logs_audit.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_object_lock_configuration" "logs_audit" {
  bucket              = aws_s3_bucket.logs_audit.id
  depends_on          = [aws_s3_bucket_versioning.logs_audit]
  object_lock_enabled = "Enabled"
  rule {
    default_retention {
      mode  = "GOVERNANCE" # GOVERNANCE (non COMPLIANCE) per permettere override admin
      years = 7
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs_audit" {
  bucket = aws_s3_bucket.logs_audit.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_s3_key_arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "logs_audit" {
  bucket                  = aws_s3_bucket.logs_audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Provider alias per la region di backup
provider "aws" {
  alias  = "backup_region"
  region = var.replication_region
}

# ══ R03 (i2): bucket NON conservativi ═════════════════════════════════
# Nessun Object Lock: contengono copie di transito (staging) o pacchetti di
# distribuzione temporanei (PdD). Stessa cifratura KMS, nessun accesso pubblico,
# solo HTTPS.

# ── BUCKET DI STAGING (S3_BUCKET_PDV del gateway) ──────────────────────
# versamenti/{tenant}/{pdv}/... (multipart REST in streaming) e
# caricamenti/{tenant}/{id}/dati (sessioni di caricamento, C1). Gli oggetti
# referenziati da un PdV NON scadono qui: la loro pulizia dopo la conservazione
# è una decisione R04 (rapporto b1, richiesta 6). Si annullano solo i multipart
# mai completati, dopo la scadenza massima di una sessione.
resource "aws_s3_bucket" "staging" {
  bucket = "${var.project}-${var.environment}-staging"
  tags = merge(local.tags, {
    Name    = "${var.project}-${var.environment}-staging"
    Purpose = "Staging dei versamenti e delle sessioni di caricamento (non WORM)"
  })
}

resource "aws_s3_bucket_public_access_block" "staging" {
  bucket                  = aws_s3_bucket.staging.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "staging" {
  bucket = aws_s3_bucket.staging.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_s3_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "staging" {
  bucket = aws_s3_bucket.staging.id

  # Parti di multipart mai completati (sessioni scadute, processi interrotti):
  # occupano spazio senza comparire come oggetti. Il giorno è successivo alla
  # scadenza massima di una sessione (CARICAMENTI_SCADENZA_ORE), così S3 non
  # annulla mai un caricamento ancora valido.
  rule {
    id     = "annulla-multipart-incompleti"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = var.staging_multipart_incompleti_giorni
    }
  }
}

resource "aws_s3_bucket_policy" "staging" {
  bucket = aws_s3_bucket.staging.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyHTTP"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.staging.arn, "${aws_s3_bucket.staging.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# ── BUCKET TEMPORANEO DEI PdD (S3_BUCKET_PDD di distribution) ──────────
# ZIP dei pacchetti di distribuzione: validi PDD_VALIDITA_ORE dell'ente (console,
# R07 D18: 72 h predefinite, fino a 30 giorni) e mai oltre il tetto
# PDD_ZIP_VALIDITA_ORE (720 h); la pulizia applicativa li elimina; la scadenza
# degli oggetti qui è la rete di sicurezza (un giorno oltre il tetto).
resource "aws_s3_bucket" "pdd" {
  bucket = "${var.project}-${var.environment}-pdd"
  tags = merge(local.tags, {
    Name    = "${var.project}-${var.environment}-pdd"
    Purpose = "Pacchetti di distribuzione temporanei (non WORM)"
  })
}

resource "aws_s3_bucket_public_access_block" "pdd" {
  bucket                  = aws_s3_bucket.pdd.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "pdd" {
  bucket = aws_s3_bucket.pdd.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_s3_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "pdd" {
  bucket = aws_s3_bucket.pdd.id

  rule {
    id     = "scadenza-pdd"
    status = "Enabled"
    filter {}
    expiration {
      days = var.pdd_scadenza_giorni
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_policy" "pdd" {
  bucket = aws_s3_bucket.pdd.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyHTTP"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.pdd.arn, "${aws_s3_bucket.pdd.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}
