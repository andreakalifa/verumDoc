# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Ruoli per servizio: nome del ServiceAccount → ARN (annotazione IRSA del chart)
output "ruoli_arn" { value = { for k, r in aws_iam_role.servizio : k => r.arn } }
output "ruoli_nome" { value = { for k, r in aws_iam_role.servizio : k => r.name } }
output "keda_operatore_role_arn" { value = try(aws_iam_role.keda_operatore[0].arn, "") }
# Dichiarazioni delle policy (per i test del modulo e per la revisione dei permessi)
output "dichiarazioni" { value = local.dichiarazioni }
