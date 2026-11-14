# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
variable "environment" { type = string }
variable "project" {
  type    = string
  default = "verumdoc"
}
variable "vpc_id" { type = string }
variable "isolated_subnet_ids" { type = list(string) }
variable "sg_redis_id" { type = string }
variable "node_type" {
  type    = string
  default = "cache.r7g.large"
}
variable "num_shards" {
  type    = number
  default = 3
}
variable "replicas_per_shard" {
  type    = number
  default = 1
}
variable "engine_version" {
  type    = string
  default = "7.1"
}
