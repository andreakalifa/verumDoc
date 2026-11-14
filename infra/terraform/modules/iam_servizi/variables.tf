# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
variable "project" {
  type    = string
  default = "verumdoc"
}
variable "environment" { type = string }

# Provider OIDC del cluster (modulo eks): i pod con ServiceAccount annotato
# ricevono le credenziali del ruolo con sts:AssumeRoleWithWebIdentity.
variable "oidc_provider_arn" { type = string }
# Emittente OIDC senza «https://» (es. oidc.eks.eu-south-1.amazonaws.com/id/ABC)
variable "oidc_issuer" { type = string }

# Namespace dei servizi (condizione «sub» della fiducia)
variable "k8s_namespace" {
  type    = string
  default = "verumdoc"
}

# Servizi con ruolo IAM: nome del ServiceAccount → blocco «iam» di
# infra/helm/values.yaml (services.<s>.iam), letto dall'ambiente con
# yamldecode. Forma:
#   { s3 = { <bucket> = [<permesso>, ...] }, kafka = [<permesso>, ...] }
#   bucket:   staging | aip | pdd                (chiavi di var.bucket_arn)
#   permessi S3:    lettura | scrittura | eliminazione | elenco | multipart | worm |
#                   lettura_retention | retention | scarto
#   (scarto, R05: cancellazione di VERSIONI dopo la scadenza della retention,
#    elenco delle versioni, lettura di retention e legal hold: solo il
#    lavoratore di ciclo-vita, limitato alle chiavi dei PdA con prefissi_oggetti;
#    retention, revisione R05 R3: scrittura con retention SENZA legal hold)
#   permessi Kafka: produzione | consumo
# Chiave: nome del ServiceAccount (servizio, o <servizio>-<lavoratore> per un
# lavoratore con blocco «iam» proprio).
# I valori ammessi li controllano anche infra/k8s/verifica_manifest.py e le
# validazioni qui sotto (un errore di battitura non diventa un ruolo vuoto).
# R04 correzioni (rev4 rilievo 7): topic e gruppi PRECISI per servizio.
#   kafka_scrittura / kafka_lettura: nomi dei topic di cui il servizio è
#     produttore / consumatore in infra/kafka/topics.yaml (li calcola l'ambiente);
#   gruppi_kafka: gruppi di consumo del servizio (blocco «iam» dei values).
variable "servizi" {
  type = map(object({
    s3              = optional(map(list(string)), {})
    kafka           = optional(list(string), [])
    kafka_scrittura = optional(list(string), [])
    kafka_lettura   = optional(list(string), [])
    gruppi_kafka    = optional(list(string), [])
    # R04 quarto giro (F1): s3:ListBucket limitato a questi prefissi del bucket
    # (condizione s3:prefix). Richiede «elenco» sullo stesso bucket.
    elenco_prefissi = optional(map(list(string)), {})
    # Revisione R05 (R3): permessi sugli OGGETTI limitati a prefissi di chiave
    # (<bucket> → <permesso> → prefissi; «?» = un carattere qualsiasi, es. le
    # chiavi dei PdA «????????-????-????-????-????????????/»). Il permesso deve
    # essere fra quelli del bucket in «s3».
    prefissi_oggetti = optional(map(map(list(string))), {})
    # Revisione R05 (R3): prefissi su cui scrittura, cancellazione, retention e
    # legal hold sono NEGATI esplicitamente (Deny), es. «registro/».
    nega_prefissi = optional(map(list(string)), {})
  }))

  validation {
    condition = alltrue(flatten([
      for s in values(var.servizi) : [
        for b, p in s.s3 : [for x in p : contains(["lettura", "scrittura", "eliminazione", "elenco", "multipart", "worm", "lettura_retention", "retention", "scarto"], x)]
      ]
    ]))
    error_message = "Permesso S3 sconosciuto: ammessi lettura, scrittura, eliminazione, elenco, multipart, worm, lettura_retention, retention, scarto."
  }
  validation {
    condition     = alltrue(flatten([for s in values(var.servizi) : [for x in s.kafka : contains(["produzione", "consumo"], x)]]))
    error_message = "Permesso Kafka sconosciuto: ammessi produzione, consumo."
  }
  validation {
    condition = alltrue([for s in values(var.servizi) :
      (contains(s.kafka, "produzione") == (length(s.kafka_scrittura) > 0))
    && (contains(s.kafka, "consumo") == (length(s.kafka_lettura) > 0 && length(s.gruppi_kafka) > 0))])
    error_message = "Kafka: «produzione» richiede almeno un topic in kafka_scrittura (producers di topics.yaml) e viceversa; «consumo» richiede topic in kafka_lettura (consumers di topics.yaml) e gruppi_kafka, e viceversa."
  }
  validation {
    condition = alltrue(flatten([for s in values(var.servizi) : [
      for t in concat(s.kafka_scrittura, s.kafka_lettura, s.gruppi_kafka) : can(regex("^[A-Za-z0-9._-]+$", t))
    ]]))
    error_message = "Nome di topic o di gruppo Kafka non valido (niente caratteri jolly)."
  }
  validation {
    condition = alltrue(flatten([for s in values(var.servizi) : [
      for b, prefissi in s.elenco_prefissi : contains(lookup(s.s3, b, []), "elenco") && length(prefissi) > 0
    ]]))
    error_message = "elenco_prefissi: ogni bucket deve avere il permesso «elenco» e almeno un prefisso."
  }
  validation {
    condition = alltrue(flatten([for s in values(var.servizi) : [
      for b, prefissi in s.elenco_prefissi : [for x in prefissi : can(regex("^[a-z0-9_-]+(/[a-z0-9_-]+)*/$", x))]
    ]]))
    error_message = "elenco_prefissi: prefissi come «registro/» (minuscole, cifre, _ e -, barra finale, nessun carattere jolly)."
  }
  validation {
    condition = alltrue(flatten([for s in values(var.servizi) : [
      for b, permessi in s.prefissi_oggetti : [for x, prefissi in permessi :
        contains(lookup(s.s3, b, []), x) && length(prefissi) > 0 && alltrue([
          for q in prefissi : can(regex("^([a-z0-9_?-]+/)+([A-Za-z0-9_-]+)?$", q))
    ])]]]))
    error_message = "prefissi_oggetti: il permesso deve essere nel blocco s3 dello stesso bucket, con almeno un prefisso come «pratiche/» (minuscole, cifre, _, -, ? per un carattere, barra finale, nessun *; dopo l'ultima barra al più l'inizio di un nome di file, es. «<ente>/<pda>/RdV_Definitiv»)."
  }
  validation {
    condition = alltrue(flatten([for s in values(var.servizi) : [
      for b, prefissi in s.nega_prefissi : length(prefissi) > 0 && alltrue([
        for q in prefissi : can(regex("^[a-z0-9_-]+(/[a-z0-9_-]+)*/$", q))
    ])]]))
    error_message = "nega_prefissi: prefissi come «registro/» (minuscole, cifre, _ e -, barra finale, nessun carattere jolly)."
  }
  validation {
    condition     = alltrue([for s in values(var.servizi) : length(s.s3) + length(s.kafka) > 0])
    error_message = "Un servizio con ruolo IAM deve avere almeno un permesso (altrimenti irsa: false nei values)."
  }
}

# ARN dei bucket per nome logico (moduli s3): staging, aip (PdA, WORM), pdd
variable "bucket_arn" {
  type = map(string)
}

# Chiave KMS dei bucket (vuota = nessun permesso KMS)
variable "kms_s3_key_arn" {
  type    = string
  default = ""
}

# ARN del cluster MSK (vuoto = nessun permesso Kafka, es. in un modulo di prova)
variable "kafka_cluster_arn" {
  type    = string
  default = ""
}

# Operatore KEDA (scalatura sul ritardo dei gruppi Kafka): ServiceAccount
# dell'operatore che legge offset e gruppi via IAM. null = nessun ruolo.
variable "keda_operatore" {
  type = object({
    namespace       = string
    service_account = string
  })
  default = {
    namespace       = "keda"
    service_account = "keda-operator"
  }
  nullable = true
}
