# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo EFS — volume condiviso ReadWriteMany (R03 i2)
#
# Serve al canale SFTP (sftp-ingest): server e lavoratori, su più nodi, lavorano
# sulle stesse cartelle dei clienti e si affidano a rinomine atomiche sullo
# stesso file system. Il driver EFS CSI (addon del modulo eks) crea un access
# point per ogni PVC della StorageClass «verumdoc-efs» (infra/helm/values.yaml),
# con fileSystemId = output file_system_id di questo modulo.
#
# Contenuto: file in transito (in/, lavorazione/, out/, fatti/, scarti/), già
# copiati nello staging S3 dopo il versamento: niente backup AWS Backup (un
# cliente può sempre ricaricare una cartella non ancora versata).

locals {
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "efs"
    ManagedBy   = "Terraform"
  }
}

resource "aws_security_group" "efs" {
  name        = "${var.project}-${var.environment}-efs"
  description = "NFS (2049) verso EFS solo dai nodi EKS"
  vpc_id      = var.vpc_id

  ingress {
    description     = "NFS dai nodi EKS"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [var.sg_nodes_id]
  }

  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-efs" })
}

resource "aws_efs_file_system" "main" {
  creation_token   = "${var.project}-${var.environment}-condiviso"
  encrypted        = true
  kms_key_id       = var.kms_key_arn
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic" # picchi di caricamento senza crediti di burst

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = merge(local.tags, {
    Name    = "${var.project}-${var.environment}-condiviso"
    Purpose = "Aree SFTP dei clienti (transito, non conservazione)"
  })
}

resource "aws_efs_backup_policy" "main" {
  file_system_id = aws_efs_file_system.main.id
  backup_policy {
    status = "DISABLED"
  }
}

# Un mount target per subnet privata (una per zona: i pod di ogni zona montano in locale)
resource "aws_efs_mount_target" "main" {
  count           = length(var.private_subnet_ids)
  file_system_id  = aws_efs_file_system.main.id
  subnet_id       = var.private_subnet_ids[count.index]
  security_groups = [aws_security_group.efs.id]
}
