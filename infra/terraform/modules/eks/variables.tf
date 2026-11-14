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
variable "kms_secrets_key_arn" { type = string }
# Impronta del certificato radice dell'emittente OIDC di EKS (richiesta dal provider
# aws 5.x). Per gli emittenti OIDC di EKS IAM verifica la catena con le proprie CA e
# non usa questo valore; il predefinito è la radice documentata da AWS per EKS.
# Si può ricavare con `openssl s_client -showcerts -connect oidc.eks.<regione>.amazonaws.com:443`.
variable "oidc_thumbprints" {
  type    = list(string)
  default = ["9e99a48a9960b14926bb7f3b02e22da2b0ab7280"]
}
variable "kubernetes_version" {
  type    = string
  default = "1.29"
}

variable "node_group_general" {
  type = object({
    instance_types = list(string)
    min_size       = number
    max_size       = number
    desired_size   = number
    disk_size_gb   = number
  })
  default = {
    instance_types = ["m6i.xlarge"]
    min_size       = 3
    max_size       = 10
    desired_size   = 3
    disk_size_gb   = 50
  }
}

variable "node_group_memory" {
  type = object({
    instance_types = list(string)
    min_size       = number
    max_size       = number
    desired_size   = number
    disk_size_gb   = number
  })
  default = {
    instance_types = ["r6i.xlarge"]
    min_size       = 2
    max_size       = 6
    desired_size   = 2
    disk_size_gb   = 50
  }
}

variable "node_group_spot" {
  type = object({
    instance_types = list(string)
    min_size       = number
    max_size       = number
    desired_size   = number
    disk_size_gb   = number
  })
  default = {
    instance_types = ["m6i.large", "m5.large", "m5a.large"]
    min_size       = 0
    max_size       = 20
    desired_size   = 0
    disk_size_gb   = 50
  }
}
