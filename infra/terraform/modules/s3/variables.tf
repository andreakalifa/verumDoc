# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
variable "environment" { type = string }
variable "project" {
  type    = string
  default = "verumdoc"
}
variable "aws_region" { type = string }
variable "kms_s3_key_arn" { type = string }
variable "replication_region" {
  type    = string
  default = "eu-central-1"
}

# Object Lock: periodo di retention di default per AIP
variable "default_retention_years" {
  type    = number
  default = 10
}

# R03: giorni dopo i quali S3 annulla un multipart mai completato nello staging.
# Deve superare la scadenza massima di una sessione di caricamento
# (CARICAMENTI_SCADENZA_ORE del gateway): l'ambiente lo ricava da values.yaml.
variable "staging_multipart_incompleti_giorni" {
  type    = number
  default = 2
  validation {
    condition     = var.staging_multipart_incompleti_giorni >= 1
    error_message = "Almeno 1 giorno."
  }
}

# R03: scadenza degli oggetti del bucket dei PdD (rete di sicurezza oltre
# PDD_ZIP_VALIDITA_ORE di distribution, il tetto della validità dal R07, ricavata
# da values.yaml).
variable "pdd_scadenza_giorni" {
  type    = number
  default = 4
  validation {
    condition     = var.pdd_scadenza_giorni >= 1
    error_message = "Almeno 1 giorno."
  }
}
