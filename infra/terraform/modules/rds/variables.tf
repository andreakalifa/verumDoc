# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
variable "environment" { type = string }
variable "project" {
  type    = string
  default = "verumdoc"
}
variable "vpc_id" { type = string }
variable "isolated_subnet_ids" { type = list(string) }
variable "sg_rds_id" { type = string }
variable "kms_rds_key_arn" { type = string }
variable "db_instance_class" {
  type    = string
  default = "db.r6g.large"
}
variable "db_name" {
  type    = string
  default = "verumdoc"
}
variable "db_username" {
  type    = string
  default = "vd_user"
}
variable "backup_retention_days" {
  type    = number
  default = 35
}
# Connessioni massime dell'istanza (R04: postgres.maxConnessioni di infra/helm/values.yaml)
variable "max_connections" {
  type    = number
  default = 200
}
variable "engine_version" {
  type    = string
  default = "16.1"
}

# Parametri scaling storage
variable "min_storage_gb" {
  type    = number
  default = 100
}
# 10 TB max
variable "max_storage_gb" {
  type    = number
  default = 10240
}
