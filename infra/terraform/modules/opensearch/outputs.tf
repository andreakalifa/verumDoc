# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
output "domain_endpoint" { value = aws_opensearch_domain.main.endpoint }
output "domain_arn" { value = aws_opensearch_domain.main.arn }
output "domain_id" { value = aws_opensearch_domain.main.domain_id }
output "dashboard_endpoint" { value = aws_opensearch_domain.main.dashboard_endpoint }
