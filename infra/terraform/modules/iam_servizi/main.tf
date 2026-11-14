# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo iam_servizi — un ruolo IAM per ServiceAccount (IRSA), permessi minimi
# Riferimento: R04 infra, VD-C-101 (prima di R04 solo il gateway aveva un ruolo:
# le annotazioni degli altri servizi puntavano a ruoli inesistenti).
#
# UNICA FONTE: il blocco «iam» di ogni servizio in infra/helm/values.yaml. Gli
# ambienti (environments/<amb>/main.tf) lo leggono con yamldecode e lo passano
# qui; il chart annota il ServiceAccount con lo STESSO nome di ruolo
# (<progetto>-<ambiente>-<servizio>) e infra/k8s/verifica_manifest.py controlla
# che ogni ServiceAccount annotato abbia il suo blocco «iam» (quindi il ruolo).
#
# Nome del ruolo: <progetto>-<ambiente>-<servizio>; fiducia limitata al
# ServiceAccount <namespace>:<servizio> del cluster (condizioni sub e aud).
# I lavoratori e i CronJob di un servizio usano il ServiceAccount del servizio;
# un lavoratore con un blocco «iam» proprio ha un ServiceAccount e un ruolo
# propri (<servizio>-<lavoratore>), con permessi minori di quelli del servizio
# (es. hashchain-verifica: sola lettura del WORM).

locals {
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "iam_servizi"
    ManagedBy   = "Terraform"
  }

  # Azioni sugli OGGETTI (risorsa <bucket>/*) per permesso logico
  azioni_oggetto = {
    lettura      = ["s3:GetObject", "s3:GetObjectVersion"] # GET e HEAD (checksum, dimensione)
    scrittura    = ["s3:PutObject"]                        # anche CopyObject/UploadPartCopy (destinazione)
    eliminazione = ["s3:DeleteObject"]
    elenco       = []
    multipart    = ["s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
    # Retention e legal hold per oggetto (scrittura WORM e sua verifica)
    worm = ["s3:PutObjectRetention", "s3:GetObjectRetention", "s3:PutObjectLegalHold", "s3:GetObjectLegalHold"]
    # Sola lettura di retention e legal hold (verifica continua, R04)
    lettura_retention = ["s3:GetObjectRetention", "s3:GetObjectLegalHold"]
    # Revisione R05 (R3): scrittura con retention SENZA poter impostare o togliere
    # il legal hold (s3:PutObjectLegalHold resta solo in «worm»)
    retention = ["s3:PutObjectRetention", "s3:GetObjectRetention", "s3:GetObjectLegalHold"]
    # Scarto autorizzato (R05, ciclo-vita): cancellazione di una VERSIONE dopo la
    # scadenza della retention (S3 in COMPLIANCE la rifiuta prima; mai
    # s3:BypassGovernanceRetention) e lettura di retention e legal hold per il
    # controllo che precede ogni cancellazione
    scarto = ["s3:DeleteObjectVersion", "s3:GetObjectVersion", "s3:GetObjectRetention", "s3:GetObjectLegalHold"]
  }
  # Azioni sul BUCKET (risorsa <bucket>) per permesso logico
  azioni_bucket = {
    lettura           = []
    scrittura         = []
    eliminazione      = []
    elenco            = ["s3:ListBucket"]
    multipart         = ["s3:ListBucketMultipartUploads"]
    worm              = ["s3:GetBucketObjectLockConfiguration"] # controllo del blocco all'avvio
    lettura_retention = []
    retention         = ["s3:GetBucketObjectLockConfiguration"] # controllo del blocco all'avvio
    scarto            = ["s3:ListBucketVersions"]               # tutte le versioni degli oggetti di un PdA
  }
  # Permessi che scrivono: servono le chiavi di cifratura dati (GenerateDataKey)
  scrive = ["scrittura", "multipart", "worm", "retention"]
  # Revisione R05 (R3): azioni NEGATE (Deny esplicito, prevale su ogni Allow) sui
  # prefissi di «nega_prefissi»: nessuna scrittura, cancellazione, retention o
  # legal hold (es. registro/ per chi non è la hashchain)
  azioni_negate = ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObject",
  "s3:PutObjectLegalHold", "s3:PutObjectRetention"]

  # Prefissi degli ARN di topic e gruppi del cluster MSK:
  # arn:aws:kafka:<regione>:<account>:topic/<cluster>/<uuid>/<nome>
  kafka_topic_base = var.kafka_cluster_arn == "" ? "" : replace(var.kafka_cluster_arn, ":cluster/", ":topic/")
  kafka_group_base = var.kafka_cluster_arn == "" ? "" : replace(var.kafka_cluster_arn, ":cluster/", ":group/")
  # Tutti i topic e i gruppi: solo per la DESCRIZIONE dell'operatore KEDA
  kafka_topic = var.kafka_cluster_arn == "" ? "" : "${local.kafka_topic_base}/*"
  kafka_group = var.kafka_cluster_arn == "" ? "" : "${local.kafka_group_base}/*"

  # Azioni sul bucket per servizio e bucket, senza s3:ListBucket dove l'elenco è
  # limitato a prefissi (dichiarazione a parte con la condizione s3:prefix)
  azioni_bucket_libere = { for nome, s in var.servizi : nome => { for b, p in s.s3 : b => [
    for a in flatten([for x in p : local.azioni_bucket[x]]) : a
    if !(a == "s3:ListBucket" && length(lookup(s.elenco_prefissi, b, [])) > 0)
  ] } }

  # Un bucket sconosciuto non rompe la valutazione: lo ferma la precondizione
  # del ruolo con un messaggio chiaro (mai un ARN inventato in una policy)
  # Revisione R05 (R3): permessi sugli oggetti SENZA prefissi (risorsa <bucket>/*)
  # e permessi limitati ai prefissi di «prefissi_oggetti» (risorsa <bucket>/<prefisso>*)
  permessi_liberi = { for nome, s in var.servizi : nome => { for b, p in s.s3 : b => [
    for x in p : x if length(lookup(lookup(s.prefissi_oggetti, b, {}), x, [])) == 0
  ] } }

  dichiarazioni = { for nome, s in var.servizi : nome => concat(
    [for b, p in local.permessi_liberi[nome] : {
      Sid      = "S3Oggetti${title(b)}"
      Effect   = "Allow"
      Action   = sort(distinct(flatten([for x in p : local.azioni_oggetto[x]])))
      Resource = ["${lookup(var.bucket_arn, b, "arn:aws:s3:::bucket-sconosciuto")}/*"]
    } if length(flatten([for x in p : local.azioni_oggetto[x]])) > 0],
    flatten([for b, permessi in s.prefissi_oggetti : [for x, prefissi in permessi : {
      Sid      = "S3Oggetti${title(b)}${replace(title(replace(x, "_", " ")), " ", "")}"
      Effect   = "Allow"
      Action   = sort(distinct(local.azioni_oggetto[x]))
      Resource = [for q in sort(prefissi) : "${lookup(var.bucket_arn, b, "arn:aws:s3:::bucket-sconosciuto")}/${q}*"]
    } if length(prefissi) > 0 && length(local.azioni_oggetto[x]) > 0]]),
    [for b, prefissi in s.nega_prefissi : {
      Sid      = "S3Nega${title(b)}"
      Effect   = "Deny"
      Action   = sort(local.azioni_negate)
      Resource = [for q in sort(prefissi) : "${lookup(var.bucket_arn, b, "arn:aws:s3:::bucket-sconosciuto")}/${q}*"]
    } if length(prefissi) > 0],
    # Azioni sul bucket; s3:ListBucket va qui solo SENZA prefissi dichiarati
    [for b, p in s.s3 : {
      Sid      = "S3Bucket${title(b)}"
      Effect   = "Allow"
      Action   = sort(distinct(local.azioni_bucket_libere[nome][b]))
      Resource = [lookup(var.bucket_arn, b, "arn:aws:s3:::bucket-sconosciuto")]
    } if length(local.azioni_bucket_libere[nome][b]) > 0],
    # R04 quarto giro (F1): elenco limitato ai prefissi dichiarati (s3:prefix)
    [for b, prefissi in s.elenco_prefissi : {
      Sid       = "S3Elenco${title(b)}"
      Effect    = "Allow"
      Action    = ["s3:ListBucket"]
      Resource  = [lookup(var.bucket_arn, b, "arn:aws:s3:::bucket-sconosciuto")]
      Condition = { StringLike = { "s3:prefix" = [for x in sort(prefissi) : "${x}*"] } }
    } if contains(lookup(s.s3, b, []), "elenco") && length(prefissi) > 0],
    var.kms_s3_key_arn == "" || length(s.s3) == 0 ? [] : [{
      Sid    = "ChiaveBucket"
      Effect = "Allow"
      Action = anytrue([for p in values(s.s3) : length(setintersection(p, local.scrive)) > 0]) ? [
        "kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey",
      ] : ["kms:Decrypt", "kms:DescribeKey"]
      Resource = [var.kms_s3_key_arn]
    }],
    # R04 correzioni (rev4 rilievo 7): ogni servizio SCRIVE solo i topic di cui è
    # produttore e LEGGE solo quelli di cui è consumatore (infra/kafka/topics.yaml,
    # passati dall'ambiente in kafka_scrittura / kafka_lettura), e usa solo i suoi
    # gruppi di consumo (gruppi_kafka nel blocco «iam» dei values). Prima: topic/*.
    var.kafka_cluster_arn == "" || length(s.kafka) == 0 ? [] : concat(
      [{
        Sid      = "KafkaCluster"
        Effect   = "Allow"
        Action   = ["kafka-cluster:Connect", "kafka-cluster:DescribeCluster"]
        Resource = [var.kafka_cluster_arn]
      }],
      contains(s.kafka, "produzione") ? [{
        Sid      = "KafkaTopicScrittura"
        Effect   = "Allow"
        Action   = ["kafka-cluster:DescribeTopic", "kafka-cluster:WriteData"]
        Resource = [for t in sort(distinct(s.kafka_scrittura)) : "${local.kafka_topic_base}/${t}"]
      }] : [],
      contains(s.kafka, "consumo") ? [{
        Sid      = "KafkaTopicLettura"
        Effect   = "Allow"
        Action   = ["kafka-cluster:DescribeTopic", "kafka-cluster:ReadData"]
        Resource = [for t in sort(distinct(s.kafka_lettura)) : "${local.kafka_topic_base}/${t}"]
      }] : [],
      contains(s.kafka, "consumo") ? [{
        Sid      = "KafkaGruppi"
        Effect   = "Allow"
        Action   = ["kafka-cluster:AlterGroup", "kafka-cluster:DescribeGroup"]
        Resource = [for g in sort(distinct(s.gruppi_kafka)) : "${local.kafka_group_base}/${g}"]
      }] : [],
    ),
  ) }
}

resource "aws_iam_role" "servizio" {
  for_each = var.servizi
  name     = "${var.project}-${var.environment}-${each.key}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = var.oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${var.oidc_issuer}:sub" = "system:serviceaccount:${var.k8s_namespace}:${each.key}"
          "${var.oidc_issuer}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
  tags = merge(local.tags, { Servizio = each.key })

  lifecycle {
    precondition {
      condition     = alltrue([for b in keys(each.value.s3) : contains(keys(var.bucket_arn), b)])
      error_message = "Il servizio ${each.key} cita un bucket sconosciuto: ammessi ${join(", ", keys(var.bucket_arn))}."
    }
  }
}

resource "aws_iam_role_policy" "servizio" {
  for_each = var.servizi
  name     = "permessi-minimi"
  role     = aws_iam_role.servizio[each.key].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = local.dichiarazioni[each.key]
  })
}

# ── Operatore KEDA: legge offset e ritardo dei gruppi Kafka (ScaledObject) ──
# Solo descrizione di topic e gruppi: nessuna lettura dei messaggi.
resource "aws_iam_role" "keda_operatore" {
  count = var.keda_operatore == null || var.kafka_cluster_arn == "" ? 0 : 1
  name  = "${var.project}-${var.environment}-keda-operatore"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = var.oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${var.oidc_issuer}:sub" = "system:serviceaccount:${var.keda_operatore.namespace}:${var.keda_operatore.service_account}"
          "${var.oidc_issuer}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "keda_operatore" {
  count = length(aws_iam_role.keda_operatore)
  name  = "ritardo-gruppi-kafka"
  role  = aws_iam_role.keda_operatore[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "KafkaCluster", Effect = "Allow", Action = ["kafka-cluster:Connect", "kafka-cluster:DescribeCluster"], Resource = [var.kafka_cluster_arn] },
      { Sid = "KafkaTopic", Effect = "Allow", Action = ["kafka-cluster:DescribeTopic"], Resource = [local.kafka_topic] },
      { Sid = "KafkaGruppi", Effect = "Allow", Action = ["kafka-cluster:DescribeGroup"], Resource = [local.kafka_group] },
    ]
  })
}
