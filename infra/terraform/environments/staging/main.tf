# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Environment: STAGING
# Stesso di prod ma con istanze più piccole per ridurre i costi.
# CIDR: 10.1.0.0/16 (non sovrapposto a prod)

terraform {
  required_version = ">= 1.8.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.50" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  backend "s3" {
    bucket         = "verumdoc-terraform-state"
    key            = "staging/terraform.tfstate"
    region         = "eu-south-1"
    encrypt        = true
    dynamodb_table = "verumdoc-terraform-lock"
  }
}

provider "aws" {
  region = local.aws_region
  default_tags {
    tags = {
      Project     = local.project
      Environment = local.environment
      ManagedBy   = "Terraform"
    }
  }
}

locals {
  project     = "verumdoc"
  environment = "staging"
  aws_region  = "eu-south-1"

  # R03 (i2): UNICA fonte della topologia è infra/helm/values.yaml (VD-P-24):
  # da lì i limiti del corpo per il WAF e le scadenze dei bucket non WORM.
  helm_values = yamldecode(file("${path.module}/../../../helm/values.yaml"))
  limiti_corpo = [for r in local.helm_values.ingress.routes : {
    percorso = r.path
    max_byte = r.maxBodyMB * 1048576
  } if try(r.maxBodyMB, null) != null]
  # Multipart incompleti dello staging annullati un giorno dopo la scadenza
  # massima di una sessione di caricamento; PdD scaduti un giorno dopo la validità
  staging_multipart_incompleti_giorni = ceil(tonumber(local.helm_values.services.gateway.config.CARICAMENTI_SCADENZA_ORE) / 24) + 1
  pdd_scadenza_giorni                 = ceil(tonumber(local.helm_values.services.distribution.config.PDD_ZIP_VALIDITA_ORE) / 24) + 1

  # R04 (VD-C-101): un ruolo IRSA per ogni servizio dell'account principale con
  # ServiceAccount annotato (irsa diverso da false), permessi dal blocco «iam».
  # Il testimone (account: testimone) ha il suo account AWS: nessun ruolo qui.
  # Un lavoratore con blocco «iam» proprio ha ServiceAccount e ruolo propri
  # (<servizio>-<lavoratore>), con i soli permessi che gli servono.
  iam_servizi_values = merge(
    { for nome, s in local.helm_values.services : nome => try(s.iam, {})
    if try(s.enabled, false) && try(s.irsa, true) != false && try(s.account, "principale") == "principale" },
    merge([for nome, s in local.helm_values.services : { for wn, w in try(s.workers, {}) : "${nome}-${wn}" => w.iam
      if try(w.iam, null) != null && try(w.enabled, true) != false }
    if try(s.enabled, false) && try(s.account, "principale") == "principale"]...),
    # R09 (D27): job periodici con ruolo proprio (es. hashchain-verifica-audit, sola lettura)
    merge([for nome, s in local.helm_values.services : { for jn, j in try(s.cronJobs, {}) : "${nome}-${jn}" => j.iam
      if try(j.iam, null) != null && try(j.enabled, true) != false }
    if try(s.enabled, false) && try(s.account, "principale") == "principale"]...),
  )
  # R04 correzioni (rev4 rilievo 7): topic precisi per servizio dalla UNICA fonte
  # dei topic (infra/kafka/topics.yaml): scrive solo dove è «producers», legge
  # solo dove è «consumers». infra/k8s/verifica_manifest.py controlla che i
  # permessi «produzione»/«consumo» dei values siano coerenti con topics.yaml.
  kafka_topics = yamldecode(file("${path.module}/../../../kafka/topics.yaml")).topics
  iam_servizi = { for nome, iam in local.iam_servizi_values : nome => merge(iam, {
    kafka_scrittura = [for t in local.kafka_topics : t.name if contains(try(t.producers, []), nome)]
    kafka_lettura   = [for t in local.kafka_topics : t.name if contains(try(t.consumers, []), nome)]
  }) }
  # Repository ECR: un'immagine per servizio (dai values) più PgBouncer (R04)
  immagini = sort(distinct(concat(
    [for s in local.helm_values.services : trimprefix(s.image, "${local.project}-") if try(s.enabled, false)],
    [trimprefix(local.helm_values.pgbouncer.image, "${local.project}-")],
  )))
}

module "kms" {
  source      = "../../modules/kms"
  project     = local.project
  environment = local.environment
  aws_region  = local.aws_region
}

module "vpc" {
  source      = "../../modules/vpc"
  project     = local.project
  environment = local.environment
  aws_region  = local.aws_region
  vpc_cidr    = "10.1.0.0/16" # Diverso da prod (10.0.0.0/16)

  public_subnets   = ["10.1.0.0/20", "10.1.16.0/20", "10.1.32.0/20"]
  private_subnets  = ["10.1.48.0/20", "10.1.64.0/20", "10.1.80.0/20"]
  isolated_subnets = ["10.1.96.0/20", "10.1.112.0/20", "10.1.128.0/20"]
}

module "eks" {
  source      = "../../modules/eks"
  project     = local.project
  environment = local.environment

  vpc_id              = module.vpc.vpc_id
  private_subnet_ids  = module.vpc.private_subnet_ids
  sg_nodes_id         = module.vpc.sg_eks_nodes_id
  kms_secrets_key_arn = module.kms.secrets_key_arn

  # Staging: istanze più piccole
  node_group_general = {
    instance_types = ["t3.large"]
    min_size       = 2
    max_size       = 5
    desired_size   = 2
    disk_size_gb   = 30
  }
  node_group_memory = {
    instance_types = ["r6i.large"]
    min_size       = 1
    max_size       = 3
    desired_size   = 1
    disk_size_gb   = 30
  }
  node_group_spot = {
    instance_types = ["t3.medium", "t3a.medium"]
    min_size       = 0
    max_size       = 10
    desired_size   = 0
    disk_size_gb   = 30
  }
}

# ── IAM dei servizi (R04, VD-C-101): un ruolo IRSA per ServiceAccount ─
# Permessi minimi su bucket, chiave KMS e Kafka dal blocco «iam» di ogni
# servizio in infra/helm/values.yaml; ruolo dell'operatore KEDA (ritardo dei
# gruppi Kafka). Il ruolo del gateway era nel modulo eks (R03 i3): spostato.
module "iam_servizi" {
  source      = "../../modules/iam_servizi"
  project     = local.project
  environment = local.environment

  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_issuer       = module.eks.oidc_issuer
  servizi           = local.iam_servizi
  bucket_arn = {
    staging = module.s3.staging_bucket_arn
    aip     = module.s3.aip_prod_bucket_arn
    pdd     = module.s3.pdd_bucket_arn
  }
  kms_s3_key_arn    = module.kms.s3_aip_key_arn
  kafka_cluster_arn = module.kafka.cluster_arn
}

moved {
  from = module.eks.aws_iam_role.gateway
  to   = module.iam_servizi.aws_iam_role.servizio["gateway"]
}

module "rds" {
  source      = "../../modules/rds"
  project     = local.project
  environment = local.environment

  vpc_id                = module.vpc.vpc_id
  isolated_subnet_ids   = module.vpc.isolated_subnet_ids
  sg_rds_id             = module.vpc.sg_rds_id
  kms_rds_key_arn       = module.kms.rds_key_arn
  max_connections       = local.helm_values.postgres.maxConnessioni
  db_instance_class     = "db.t4g.medium" # Staging: istanza più piccola
  backup_retention_days = 7
}

module "s3" {
  source                  = "../../modules/s3"
  project                 = local.project
  environment             = local.environment
  aws_region              = local.aws_region
  kms_s3_key_arn          = module.kms.s3_aip_key_arn
  default_retention_years = 1 # Staging: retention breve

  staging_multipart_incompleti_giorni = local.staging_multipart_incompleti_giorni
  pdd_scadenza_giorni                 = local.pdd_scadenza_giorni
}

module "elasticache" {
  source      = "../../modules/elasticache"
  project     = local.project
  environment = local.environment

  vpc_id              = module.vpc.vpc_id
  isolated_subnet_ids = module.vpc.isolated_subnet_ids
  sg_redis_id         = module.vpc.sg_redis_id
  node_type           = "cache.t4g.medium"
  num_shards          = 1
  replicas_per_shard  = 0
}

module "kafka" {
  source      = "../../modules/kafka"
  project     = local.project
  environment = local.environment

  vpc_id               = module.vpc.vpc_id
  isolated_subnet_ids  = module.vpc.isolated_subnet_ids
  sg_kafka_id          = module.vpc.sg_kafka_id
  broker_instance_type = "kafka.t3.small"
  num_brokers          = 3
  broker_storage_gb    = 100
}

module "opensearch" {
  source      = "../../modules/opensearch"
  project     = local.project
  environment = local.environment

  vpc_id                = module.vpc.vpc_id
  isolated_subnet_ids   = module.vpc.isolated_subnet_ids
  kms_s3_key_arn        = module.kms.s3_aip_key_arn
  instance_type         = "t3.medium.search"
  instance_count        = 1
  dedicated_master_type = "t3.medium.search"
  volume_size_gb        = 20
}

# ── EFS (R03: volume condiviso ReadWriteMany del canale SFTP) ─────
module "efs" {
  source      = "../../modules/efs"
  project     = local.project
  environment = local.environment

  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  sg_nodes_id        = module.vpc.sg_eks_nodes_id
  kms_key_arn        = module.kms.s3_aip_key_arn
}

module "ecr" {
  source      = "../../modules/ecr"
  project     = local.project
  environment = local.environment
  services    = local.immagini
}

module "waf" {
  source       = "../../modules/waf"
  project      = local.project
  environment  = local.environment
  aws_region   = local.aws_region
  alb_arn      = "" # TODO F0.3
  limiti_corpo = local.limiti_corpo
}
