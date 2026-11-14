# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Environment: TESTIMONE (R12a, VD-C-110) — ACCOUNT AWS SEPARATO
#
# Il testimone controfirma i checkpoint del registro delle impronte (contratto K3,
# piano §5.3): vale solo se chi amministra il registro NON può manometterlo. Per
# questo vive in un account AWS diverso, con amministratori, stato Terraform,
# chiavi KMS, cluster, database, ALB, certificato e WAF propri. Nessun ruolo IRSA
# (il testimone non usa servizi AWS) e nessuna relazione di fiducia con l'account
# principale: la hashchain lo raggiunge come servizio esterno su HTTPS
# (TESTIMONE_URL = https://testimone.verumdoc.it).
#
# Uso (credenziali dell'account del testimone, MAI quelle dell'account principale):
#   tofu init -backend-config="bucket=<bucket di stato dell'account del testimone>"
#   tofu plan -var account_id_testimone=<id> -var account_id_principale=<id>
# Il provider rifiuta qualunque account diverso da account_id_testimone e la
# precondizione rifiuta l'esecuzione se i due account coincidono.
# Reti: 10.20.0.0/16, come infra/helm/values.testimone.yaml (network.*).

terraform {
  required_version = ">= 1.8.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.50" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  backend "s3" {
    # bucket dell'account del testimone (passato con -backend-config: mai quello
    # dello stato dell'account principale, che i suoi amministratori possono scrivere)
    key            = "testimone/terraform.tfstate"
    region         = "eu-south-1"
    encrypt        = true
    dynamodb_table = "verumdoc-testimone-terraform-lock"
  }
}

variable "account_id_testimone" {
  description = "ID dell'account AWS del testimone (l'unico in cui il provider può operare)"
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id_testimone))
    error_message = "account_id_testimone: 12 cifre."
  }
}

variable "account_id_principale" {
  description = "ID dell'account del registro: solo per rifiutare un'esecuzione nell'account sbagliato"
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id_principale))
    error_message = "account_id_principale: 12 cifre."
  }
}

variable "dominio_testimone" {
  description = "Host pubblico del testimone (ingress.hosts.testimone dei values)"
  type        = string
  default     = "testimone.verumdoc.it"
}

provider "aws" {
  region              = local.aws_region
  allowed_account_ids = [var.account_id_testimone]
  default_tags {
    tags = {
      Project     = local.project
      Environment = local.environment
      ManagedBy   = "Terraform"
      Ruolo       = "testimone-del-registro"
    }
  }
}

locals {
  project     = "verumdoc"
  environment = "testimone"
  aws_region  = "eu-south-1"

  # Stessa fonte dei manifest del testimone (VD-P-24): values.yaml + values.testimone.yaml
  helm_values    = yamldecode(file("${path.module}/../../../helm/values.yaml"))
  helm_testimone = yamldecode(file("${path.module}/../../../helm/values.testimone.yaml"))
  rete           = local.helm_testimone.network
  limiti_corpo = [for r in local.helm_testimone.ingress.routes : {
    percorso = r.path
    max_byte = r.maxBodyMB * 1048576
  } if try(r.maxBodyMB, null) != null]
  # Il testimone non ha ruoli IAM per costruzione (irsa: false nei values)
  testimone_senza_iam = try(local.helm_values.services.testimone.irsa, true) == false
}

data "aws_caller_identity" "attuale" {}

# Barriera esplicita: mai nell'account del registro, mai con un testimone che
# avrebbe ruoli IAM (servirebbero permessi decisi da chi amministra il registro)
resource "terraform_data" "account_separato" {
  input = data.aws_caller_identity.attuale.account_id
  lifecycle {
    precondition {
      condition     = var.account_id_testimone != var.account_id_principale
      error_message = "Il testimone deve stare in un account AWS DIVERSO da quello del registro."
    }
    precondition {
      condition     = data.aws_caller_identity.attuale.account_id == var.account_id_testimone
      error_message = "Credenziali di un account diverso da account_id_testimone."
    }
    precondition {
      condition     = local.testimone_senza_iam
      error_message = "services.testimone.irsa deve restare false nei values (nessun ruolo IAM)."
    }
  }
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
  vpc_cidr    = local.rete.vpcCidr

  # Sottoreti = reti del Chart del testimone (ALB/NLB pubbliche, dati isolate)
  public_subnets   = local.rete.albSourceCidrs
  private_subnets  = ["10.20.48.0/20", "10.20.64.0/20", "10.20.80.0/20"]
  isolated_subnets = local.rete.dataCidrs
}

module "eks" {
  source      = "../../modules/eks"
  project     = local.project
  environment = local.environment

  vpc_id              = module.vpc.vpc_id
  private_subnet_ids  = module.vpc.private_subnet_ids
  sg_nodes_id         = module.vpc.sg_eks_nodes_id
  kms_secrets_key_arn = module.kms.secrets_key_arn

  # Un solo servizio (2–4 repliche): nodi piccoli, niente nodi a memoria né spot
  node_group_general = {
    instance_types = ["t3.medium"]
    min_size       = 2
    max_size       = 3
    desired_size   = 2
    disk_size_gb   = 30
  }
  node_group_memory = {
    instance_types = ["t3.medium"]
    min_size       = 0
    max_size       = 1
    desired_size   = 0
    disk_size_gb   = 30
  }
  node_group_spot = {
    instance_types = ["t3.small"]
    min_size       = 0
    max_size       = 1
    desired_size   = 0
    disk_size_gb   = 20
  }
}

# Database proprio del testimone (schema «testimone», Job testimone-migrazioni)
module "rds" {
  source      = "../../modules/rds"
  project     = local.project
  environment = local.environment

  vpc_id                = module.vpc.vpc_id
  isolated_subnet_ids   = module.vpc.isolated_subnet_ids
  sg_rds_id             = module.vpc.sg_rds_id
  kms_rds_key_arn       = module.kms.rds_key_arn
  db_name               = "verumdoc_testimone"
  db_username           = "vd_testimone_admin"
  db_instance_class     = "db.t4g.medium"
  max_connections       = 100
  backup_retention_days = 35 # le controfirme sono prove: backup lunghi
}

module "ecr" {
  source      = "../../modules/ecr"
  project     = local.project
  environment = local.environment
  services    = ["testimone"]
}

# Certificato dell'host del testimone (validazione DNS fuori da qui: il record
# lo crea chi amministra la zona, che non deve essere chi amministra il registro)
resource "aws_acm_certificate" "testimone" {
  domain_name       = var.dominio_testimone
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

module "waf" {
  source       = "../../modules/waf"
  project      = local.project
  environment  = local.environment
  aws_region   = local.aws_region
  alb_arn      = "" # l'ALB lo crea l'AWS Load Balancer Controller dall'Ingress: associazione dall'annotazione
  limiti_corpo = local.limiti_corpo
  # Il testimone riceve pochi checkpoint al minuto da UN solo chiamante
  rate_limit_requests_per_5min = 600
}
