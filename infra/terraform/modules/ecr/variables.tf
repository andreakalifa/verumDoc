# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
variable "environment" { type = string }
variable "project" {
  type    = string
  default = "verumdoc"
}
variable "services" {
  type    = list(string)
  default = ["gateway", "validation", "signature", "aip-builder", "hashchain", "distribution", "notification", "onboarding", "dashboard-api", "frontend"]
}
# Mantieni le ultime N immagini tagged
variable "image_count_to_keep" {
  type    = number
  default = 10
}
