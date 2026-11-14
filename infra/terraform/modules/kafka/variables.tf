# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
variable "environment" { type = string }
variable "project" {
  type    = string
  default = "verumdoc"
}
variable "vpc_id" { type = string }
variable "isolated_subnet_ids" { type = list(string) }
variable "sg_kafka_id" { type = string }
variable "broker_instance_type" {
  type    = string
  default = "kafka.m5.large"
}
variable "kafka_version" {
  type    = string
  default = "3.6.0"
}
variable "num_brokers" {
  type    = number
  default = 3
}
variable "broker_storage_gb" {
  type    = number
  default = 1000
}
