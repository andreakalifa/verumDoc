# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
variable "environment" { type = string }
variable "project" {
  type    = string
  default = "verumdoc"
}
variable "aws_region" {
  type    = string
  default = "eu-south-1"
}
variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

# Availability zones eu-south-1
variable "azs" {
  type    = list(string)
  default = ["eu-south-1a", "eu-south-1b", "eu-south-1c"]
}

# Subnet CIDR — /20 per subnet (4094 host ciascuna)
variable "public_subnets" {
  type    = list(string)
  default = ["10.0.0.0/20", "10.0.16.0/20", "10.0.32.0/20"]
}
variable "private_subnets" {
  type    = list(string)
  default = ["10.0.48.0/20", "10.0.64.0/20", "10.0.80.0/20"]
}
variable "isolated_subnets" {
  type    = list(string)
  default = ["10.0.96.0/20", "10.0.112.0/20", "10.0.128.0/20"]
}
