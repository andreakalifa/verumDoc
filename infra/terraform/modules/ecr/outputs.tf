# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
output "repository_urls" {
  value = { for svc, repo in aws_ecr_repository.services : svc => repo.repository_url }
}
output "registry_id" {
  value = values(aws_ecr_repository.services)[0].registry_id
}
