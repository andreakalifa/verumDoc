# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Output dell'ambiente staging — usati dalla CI/CD e dal deployment K8s
output "vpc_id" { value = module.vpc.vpc_id }
output "private_subnet_ids" { value = module.vpc.private_subnet_ids }
output "eks_cluster_name" { value = module.eks.cluster_name }
output "eks_cluster_endpoint" { value = module.eks.cluster_endpoint }
# R04 (VD-C-101): ruoli IRSA per servizio (annotazioni dei ServiceAccount) e di KEDA
output "iam_ruoli_arn" { value = module.iam_servizi.ruoli_arn }
output "keda_operatore_role_arn" { value = module.iam_servizi.keda_operatore_role_arn }
output "rds_endpoint" { value = module.rds.cluster_endpoint }
output "rds_reader_endpoint" { value = module.rds.cluster_reader_endpoint }
output "rds_secret_arn" { value = module.rds.secret_arn }
output "redis_endpoint" { value = module.elasticache.configuration_endpoint }
output "redis_secret_arn" { value = module.elasticache.auth_secret_arn }
output "kafka_bootstrap" { value = module.kafka.bootstrap_brokers_sasl_iam }
output "opensearch_endpoint" { value = module.opensearch.domain_endpoint }
output "ecr_repository_urls" { value = module.ecr.repository_urls }
output "s3_aip_bucket" { value = module.s3.aip_prod_bucket }
output "kms_s3_key_arn" { value = module.kms.s3_aip_key_arn }
output "kms_rds_key_arn" { value = module.kms.rds_key_arn }
output "waf_web_acl_arn" { value = module.waf.web_acl_arn }
# R03 (i2): bucket non WORM e file system condiviso (segnaposto DA_TERRAFORM in values.yaml)
output "s3_staging_bucket" { value = module.s3.staging_bucket }
output "s3_pdd_bucket" { value = module.s3.pdd_bucket }
output "efs_file_system_id" { value = module.efs.file_system_id }
