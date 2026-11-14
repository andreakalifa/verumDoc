# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
variable "environment" { type = string }
variable "project" {
  type    = string
  default = "verumdoc"
}
variable "vpc_id" { type = string }
variable "isolated_subnet_ids" { type = list(string) }
variable "kms_s3_key_arn" { type = string }
variable "instance_type" {
  type    = string
  default = "r6g.large.search"
}
variable "instance_count" {
  type    = number
  default = 3
}
variable "dedicated_master_type" {
  type    = string
  default = "m6g.large.search"
}
variable "engine_version" {
  type    = string
  default = "OpenSearch_2.11"
}
variable "volume_size_gb" {
  type    = number
  default = 100
}

# false se il ruolo collegato es.amazonaws.com esiste già nell'account
variable "create_service_linked_role" {
  type    = bool
  default = true
}
