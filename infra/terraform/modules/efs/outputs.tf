# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
output "file_system_id" { value = aws_efs_file_system.main.id }
output "file_system_arn" { value = aws_efs_file_system.main.arn }
output "sg_efs_id" { value = aws_security_group.efs.id }
