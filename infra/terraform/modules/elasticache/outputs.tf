# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
output "configuration_endpoint" { value = aws_elasticache_replication_group.main.configuration_endpoint_address }
output "port" { value = 6379 }
output "auth_secret_arn" { value = aws_secretsmanager_secret.redis_auth.arn }
output "replication_group_id" { value = aws_elasticache_replication_group.main.id }
