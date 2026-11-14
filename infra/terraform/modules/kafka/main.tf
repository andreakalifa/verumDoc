# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo MSK Kafka — Managed Kafka 3.6
# Riferimento piano: F0.2 — infra/terraform/modules/kafka
#
# 3 broker in 3 AZ separate — HA garantita
# TLS client + server obbligatorio
# Topic creati separatamente: lista unica infra/kafka/topics.yaml,
# script infra/scripts/kafka_create_topics.sh

locals {
  cluster_name = "${var.project}-${var.environment}-kafka"
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "kafka"
    ManagedBy   = "Terraform"
  }
}

resource "aws_msk_cluster" "main" {
  cluster_name           = local.cluster_name
  kafka_version          = var.kafka_version
  number_of_broker_nodes = var.num_brokers

  broker_node_group_info {
    instance_type   = var.broker_instance_type
    client_subnets  = var.isolated_subnet_ids
    security_groups = [var.sg_kafka_id]

    storage_info {
      ebs_storage_info {
        volume_size = var.broker_storage_gb
        provisioned_throughput {
          enabled           = true
          volume_throughput = 250
        }
      }
    }
  }

  # TLS client + server obbligatorio
  client_authentication {
    tls {
      certificate_authority_arns = []
    }
    sasl {
      iam   = true # IAM authentication
      scram = false
    }
    unauthenticated = false # Vieta accesso non autenticato
  }

  encryption_info {
    encryption_in_transit {
      client_broker = "TLS" # Solo TLS tra client e broker
      in_cluster    = true  # TLS anche inter-broker
    }
  }

  # Configurazione Kafka
  configuration_info {
    arn      = aws_msk_configuration.main.arn
    revision = aws_msk_configuration.main.latest_revision
  }

  # Log broker in CloudWatch
  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.kafka.name
      }
      s3 {
        enabled = false
      }
    }
  }

  # Monitoring
  open_monitoring {
    prometheus {
      jmx_exporter { enabled_in_broker = true }
      node_exporter { enabled_in_broker = true }
    }
  }

  enhanced_monitoring = "PER_TOPIC_PER_PARTITION"

  tags = merge(local.tags, { Name = local.cluster_name })
}

resource "aws_msk_configuration" "main" {
  name           = "${local.cluster_name}-config"
  kafka_versions = [var.kafka_version]
  description    = "Kafka configuration — ${local.cluster_name}"

  server_properties = <<-PROPS
    # Retention
    log.retention.hours=72
    log.retention.bytes=-1
    log.segment.bytes=1073741824

    # Performance
    num.network.threads=3
    num.io.threads=8
    socket.send.buffer.bytes=102400
    socket.receive.buffer.bytes=102400
    socket.request.max.bytes=104857600

    # Compattazione
    log.cleanup.policy=delete
    log.cleaner.enable=true

    # Replica e leader
    default.replication.factor=3
    min.insync.replicas=2
    offsets.topic.replication.factor=3
    transaction.state.log.replication.factor=3
    transaction.state.log.min.isr=2

    # Auto topic creation disabilitato — i topic vengono creati esplicitamente
    auto.create.topics.enable=false

    # Compressione
    compression.type=lz4

    # Max message size 50MB (per AIP grandi)
    message.max.bytes=52428800
  PROPS
}

resource "aws_cloudwatch_log_group" "kafka" {
  name              = "/aws/msk/${local.cluster_name}"
  retention_in_days = 30
  tags              = local.tags
}

# Nessuno «schema registry»: il cluster MSK Serverless che stava qui era un secondo
# cluster Kafka (costo) che nessun servizio usa. Se servirà la validazione degli
# schemi degli eventi si userà AWS Glue Schema Registry (VD-C-038).
