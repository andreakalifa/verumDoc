# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
output "s3_aip_key_arn" { value = aws_kms_key.s3_aip.arn }
output "s3_aip_key_id" { value = aws_kms_key.s3_aip.key_id }
output "rds_key_arn" { value = aws_kms_key.rds.arn }
output "rds_key_id" { value = aws_kms_key.rds.key_id }
output "secrets_key_arn" { value = aws_kms_key.secrets.arn }
output "secrets_key_id" { value = aws_kms_key.secrets.key_id }
