# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Output dell'account del testimone — valori da riportare in values.testimone.yaml
# (DA_IMPOSTARE) e, per l'URL, nei values della hashchain (TESTIMONE_URL)
output "account_id" { value = data.aws_caller_identity.attuale.account_id }
output "vpc_id" { value = module.vpc.vpc_id }
output "eks_cluster_name" { value = module.eks.cluster_name }
output "eks_cluster_endpoint" { value = module.eks.cluster_endpoint }
output "rds_endpoint" { value = module.rds.cluster_endpoint }
output "rds_secret_arn" { value = module.rds.secret_arn }
output "ecr_repository_urls" { value = module.ecr.repository_urls }
output "acm_certificate_arn_testimone" { value = aws_acm_certificate.testimone.arn }
output "acm_validazione_dns" {
  description = "Record DNS da creare per validare il certificato"
  value       = aws_acm_certificate.testimone.domain_validation_options
}
output "waf_web_acl_arn_testimone" { value = module.waf.web_acl_arn }
