# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
variable "environment" { type = string }
variable "project" {
  type    = string
  default = "verumdoc"
}
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "sg_nodes_id" { type = string }
variable "kms_key_arn" { type = string }
