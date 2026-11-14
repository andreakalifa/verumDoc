# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
output "web_acl_arn" { value = aws_wafv2_web_acl.main.arn }
output "web_acl_id" { value = aws_wafv2_web_acl.main.id }
