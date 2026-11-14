# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo ECR — Container Registry privato
# Riferimento piano: F0.2 — infra/terraform/modules/ecr
#
# 1 repository per servizio (9 totali)
# Scan vulnerabilità automatico su ogni push
# Lifecycle policy: mantieni ultime 10 immagini tagged

locals {
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "ecr"
    ManagedBy   = "Terraform"
  }
}

resource "aws_ecr_repository" "services" {
  for_each = toset(var.services)

  name                 = "${var.project}-${each.value}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true # Scan CVE su ogni docker push
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(local.tags, { Name = "${var.project}-${each.value}" })
}

# Lifecycle policy — rimuovi immagini vecchie (mantieni ultime N tagged)
resource "aws_ecr_lifecycle_policy" "services" {
  for_each   = toset(var.services)
  repository = aws_ecr_repository.services[each.key].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Mantieni ultime ${var.image_count_to_keep} immagini con tag"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v", "sha-"]
          countType     = "imageCountMoreThan"
          countNumber   = var.image_count_to_keep
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Rimuovi immagini untagged più vecchie di 1 giorno"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      }
    ]
  })
}
