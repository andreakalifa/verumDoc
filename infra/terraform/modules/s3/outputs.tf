# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
output "aip_prod_bucket" { value = aws_s3_bucket.aip_prod.id }
output "aip_prod_bucket_arn" { value = aws_s3_bucket.aip_prod.arn }
output "aip_backup_bucket" { value = aws_s3_bucket.aip_backup.id }
output "logs_audit_bucket" { value = aws_s3_bucket.logs_audit.id }
output "logs_audit_bucket_arn" { value = aws_s3_bucket.logs_audit.arn }
output "staging_bucket" { value = aws_s3_bucket.staging.id }
output "staging_bucket_arn" { value = aws_s3_bucket.staging.arn }
output "pdd_bucket" { value = aws_s3_bucket.pdd.id }
output "pdd_bucket_arn" { value = aws_s3_bucket.pdd.arn }
