# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
variable "environment" { type = string }
variable "project" {
  type    = string
  default = "verumdoc"
}
variable "aws_region" { type = string }
variable "alb_arn" { type = string }

# Rate limiting: max richieste per 5 minuti per IP
variable "rate_limit_requests_per_5min" {
  type    = number
  default = 2000
}

# Paesi bloccati (ISO 3166-1 alpha-2)
variable "blocked_countries" {
  type    = list(string)
  default = [] # Vuoto di default — configurare in prod se necessario
}

# R03 (i2): limite del corpo per prefisso di percorso. L'ambiente lo ricava da
# infra/helm/values.yaml (ingress.routes[].maxBodyMB × 1048576): UNICA fonte.
variable "limiti_corpo" {
  type = list(object({
    percorso = string
    max_byte = number
  }))
  default = []
  validation {
    condition     = alltrue([for l in var.limiti_corpo : l.max_byte >= 1 && startswith(l.percorso, "/")])
    error_message = "Ogni limite ha max_byte >= 1 e un percorso che inizia con «/»."
  }
}
