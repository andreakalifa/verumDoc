# Test del modulo iam_servizi (OpenTofu ≥ 1.8: `tofu test` con provider finto).
# Esegue il modulo in «plan» e controlla nomi dei ruoli, fiducia e permessi;
# il primo run usa il blocco «iam» REALE di infra/helm/values.yaml, come gli
# ambienti. Uso: cd infra/terraform/modules/iam_servizi && tofu init && tofu test

mock_provider "aws" {}

variables {
  environment       = "prova"
  oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/oidc.eks.eu-south-1.amazonaws.com/id/ABC"
  oidc_issuer       = "oidc.eks.eu-south-1.amazonaws.com/id/ABC"
  bucket_arn = {
    staging = "arn:aws:s3:::verumdoc-prova-staging"
    aip     = "arn:aws:s3:::verumdoc-prova-aip-prod"
    pdd     = "arn:aws:s3:::verumdoc-prova-pdd"
  }
  kms_s3_key_arn    = "arn:aws:kms:eu-south-1:123456789012:key/abc"
  kafka_cluster_arn = "arn:aws:kafka:eu-south-1:123456789012:cluster/verumdoc-prova-kafka/uuid-1"
  servizi = {
    gateway = {
      s3              = { staging = ["lettura", "scrittura", "eliminazione", "elenco", "multipart"], aip = ["lettura"] }
      kafka           = ["produzione"]
      kafka_scrittura = ["pdv.received"]
    }
    aip-builder = {
      s3              = { staging = ["lettura"], aip = ["lettura", "scrittura", "worm", "multipart"] }
      kafka           = ["produzione", "consumo"]
      kafka_scrittura = ["pdv.validated.dlq", "aip.error"]
      kafka_lettura   = ["pdv.validated"]
      gruppi_kafka    = ["aip-builder-service"]
    }
    dashboard-api = { s3 = { aip = ["lettura"] } }
    onboarding    = { kafka = ["produzione"], kafka_scrittura = ["lifecycle.event"] }
  }
}

run "valori" {
  command = apply
  module {
    source = "./tests/valori"
  }
}

run "ruoli_dai_values_reali" {
  command = plan
  variables {
    servizi = run.valori.servizi
  }
  assert {
    condition     = length(aws_iam_role.servizio) > 0
    error_message = "Nessun ruolo creato dai values reali"
  }
  assert {
    condition     = alltrue([for k, r in aws_iam_role.servizio : r.name == "verumdoc-prova-${k}"])
    error_message = "Nome del ruolo diverso da <progetto>-<ambiente>-<servizio> (annotazione del chart)"
  }
  # Lavoratore con ruolo proprio: la verifica continua legge il WORM e la retention, non scrive
  assert {
    condition = !anytrue([for d in jsondecode(aws_iam_role_policy.servizio["hashchain-verifica"].policy).Statement :
    contains(d.Action, "s3:PutObject") || contains(d.Action, "s3:PutObjectRetention")])
    error_message = "hashchain-verifica non deve scrivere nel WORM"
  }
  assert {
    condition = anytrue([for d in jsondecode(aws_iam_role_policy.servizio["hashchain-verifica"].policy).Statement :
    contains(d.Action, "s3:GetObjectRetention")])
    error_message = "hashchain-verifica deve leggere la retention (GetObjectRetention)"
  }
  # R04 correzioni (rev4 rilievo 7): nessun topic/* in scrittura o lettura; solo la
  # hashchain scrive aip.conserved e catena.checkpoint
  assert {
    condition = !anytrue(flatten([for k, p in aws_iam_role_policy.servizio : [for d in jsondecode(p.policy).Statement :
    (contains(d.Action, "kafka-cluster:WriteData") || contains(d.Action, "kafka-cluster:ReadData")) && anytrue([for r in d.Resource : endswith(r, "/*")])]]))
    error_message = "Permessi Kafka su topic/*: ogni servizio deve avere i soli topic di topics.yaml"
  }
  assert {
    condition = alltrue(flatten([for k, p in aws_iam_role_policy.servizio : [for d in jsondecode(p.policy).Statement :
      k == "hashchain" || !contains(d.Action, "kafka-cluster:WriteData") || !anytrue([for r in d.Resource :
    endswith(r, "/aip.conserved") || endswith(r, "/catena.checkpoint")])]]))
    error_message = "Solo la hashchain può scrivere aip.conserved e catena.checkpoint"
  }
  assert {
    condition = anytrue([for d in jsondecode(aws_iam_role_policy.servizio["hashchain"].policy).Statement :
    d.Sid == "KafkaTopicScrittura" && contains(d.Resource, "arn:aws:kafka:eu-south-1:123456789012:topic/verumdoc-prova-kafka/uuid-1/catena.checkpoint")])
    error_message = "La hashchain deve poter scrivere catena.checkpoint"
  }
  assert {
    condition = [for d in jsondecode(aws_iam_role_policy.servizio["notification"].policy).Statement : d.Resource if d.Sid == "KafkaGruppi"][0] == [
      "arn:aws:kafka:eu-south-1:123456789012:group/verumdoc-prova-kafka/uuid-1/notification-eventi-cliente",
    "arn:aws:kafka:eu-south-1:123456789012:group/verumdoc-prova-kafka/uuid-1/notification-service"]
    error_message = "Gruppi di notification diversi da quelli dichiarati"
  }
}

# R04 quarto giro (F1): la hashchain elenca il WORM solo sotto registro/
run "elenco_hashchain_limitato_a_registro" {
  command = plan
  variables {
    servizi = run.valori.servizi
  }
  assert {
    condition = anytrue([for d in jsondecode(aws_iam_role_policy.servizio["hashchain"].policy).Statement :
      d.Sid == "S3ElencoAip" && d.Action == ["s3:ListBucket"]
      && d.Resource == ["arn:aws:s3:::verumdoc-prova-aip-prod"]
    && try(d.Condition.StringLike["s3:prefix"], null) == ["registro/*"]])
    error_message = "La hashchain deve poter elencare il bucket dei PdA sotto registro/ (s3:ListBucket con s3:prefix)"
  }
  assert {
    condition = !anytrue([for d in jsondecode(aws_iam_role_policy.servizio["hashchain"].policy).Statement :
    contains(d.Action, "s3:ListBucket") && d.Sid != "S3ElencoAip"])
    error_message = "La hashchain non deve avere s3:ListBucket senza la condizione sul prefisso"
  }
  # Nessun altro servizio elenca il bucket dei PdA
  assert {
    condition = !anytrue(flatten([for k, p in aws_iam_role_policy.servizio : [for d in jsondecode(p.policy).Statement :
    k != "hashchain" && contains(d.Action, "s3:ListBucket") && contains(d.Resource, "arn:aws:s3:::verumdoc-prova-aip-prod")]]))
    error_message = "Solo la hashchain elenca il bucket dei PdA"
  }
}

run "elenco_prefissi_senza_elenco" {
  command = plan
  variables {
    servizi = { hashchain = { s3 = { aip = ["lettura"] }, elenco_prefissi = { aip = ["registro/"] } } }
  }
  expect_failures = [var.servizi]
}

run "elenco_prefisso_con_jolly" {
  command = plan
  variables {
    servizi = { hashchain = { s3 = { aip = ["lettura", "elenco"] }, elenco_prefissi = { aip = ["registro/*"] } } }
  }
  expect_failures = [var.servizi]
}

run "nomi_e_fiducia" {
  command = plan
  assert {
    condition     = aws_iam_role.servizio["gateway"].name == "verumdoc-prova-gateway"
    error_message = "Nome del ruolo del gateway errato"
  }
  assert {
    condition = jsondecode(aws_iam_role.servizio["aip-builder"].assume_role_policy).Statement[0].Condition.StringEquals[
      "oidc.eks.eu-south-1.amazonaws.com/id/ABC:sub"
    ] == "system:serviceaccount:verumdoc:aip-builder"
    error_message = "La fiducia non è limitata al ServiceAccount del servizio"
  }
}

run "permessi_minimi" {
  command = plan
  # Il gateway legge i PdA ma non li scrive né ne cambia la retention
  assert {
    condition = !anytrue([for d in jsondecode(aws_iam_role_policy.servizio["gateway"].policy).Statement :
    contains(d.Resource, "arn:aws:s3:::verumdoc-prova-aip-prod/*") && (contains(d.Action, "s3:PutObject") || contains(d.Action, "s3:PutObjectRetention"))])
    error_message = "Il gateway non deve scrivere sul bucket dei PdA"
  }
  # aip-builder scrive con retention e legge il blocco del bucket
  assert {
    condition = anytrue([for d in jsondecode(aws_iam_role_policy.servizio["aip-builder"].policy).Statement :
    contains(d.Action, "s3:PutObjectRetention") && contains(d.Resource, "arn:aws:s3:::verumdoc-prova-aip-prod/*")])
    error_message = "aip-builder senza PutObjectRetention sul bucket dei PdA"
  }
  assert {
    condition = anytrue([for d in jsondecode(aws_iam_role_policy.servizio["aip-builder"].policy).Statement :
    contains(d.Action, "s3:GetBucketObjectLockConfiguration") && contains(d.Resource, "arn:aws:s3:::verumdoc-prova-aip-prod")])
    error_message = "aip-builder senza GetBucketObjectLockConfiguration sul bucket"
  }
  # Nessun servizio può eliminare oggetti dal WORM
  assert {
    condition = !anytrue(flatten([for k, p in aws_iam_role_policy.servizio : [for d in jsondecode(p.policy).Statement :
    contains(d.Action, "s3:DeleteObject") && contains(d.Resource, "arn:aws:s3:::verumdoc-prova-aip-prod/*")]]))
    error_message = "Eliminazione sul bucket dei PdA concessa"
  }
  # Sola lettura → KMS solo Decrypt/DescribeKey
  assert {
    condition = [for d in jsondecode(aws_iam_role_policy.servizio["dashboard-api"].policy).Statement : d.Action if d.Sid == "ChiaveBucket"][0] == [
    "kms:Decrypt", "kms:DescribeKey"]
    error_message = "Un servizio in sola lettura non deve cifrare con la chiave dei bucket"
  }
  # Kafka: produttore senza gruppi, consumatore con i gruppi
  assert {
    condition     = !anytrue([for d in jsondecode(aws_iam_role_policy.servizio["onboarding"].policy).Statement : d.Sid == "KafkaGruppi"])
    error_message = "Un produttore non ha bisogno dei gruppi di consumo"
  }
  assert {
    condition = anytrue([for d in jsondecode(aws_iam_role_policy.servizio["aip-builder"].policy).Statement :
    d.Sid == "KafkaGruppi" && d.Resource == ["arn:aws:kafka:eu-south-1:123456789012:group/verumdoc-prova-kafka/uuid-1/aip-builder-service"]])
    error_message = "Consumatore senza permessi sul SUO gruppo"
  }
  assert {
    condition = [for d in jsondecode(aws_iam_role_policy.servizio["gateway"].policy).Statement : d.Resource if d.Sid == "KafkaTopicScrittura"][0] == [
    "arn:aws:kafka:eu-south-1:123456789012:topic/verumdoc-prova-kafka/uuid-1/pdv.received"]
    error_message = "Il gateway deve scrivere solo i suoi topic"
  }
  assert {
    condition = [for d in jsondecode(aws_iam_role_policy.servizio["aip-builder"].policy).Statement : d.Resource if d.Sid == "KafkaTopicLettura"][0] == [
    "arn:aws:kafka:eu-south-1:123456789012:topic/verumdoc-prova-kafka/uuid-1/pdv.validated"]
    error_message = "aip-builder deve leggere solo i suoi topic"
  }
  assert {
    condition     = length(aws_iam_role.keda_operatore) == 1
    error_message = "Ruolo dell'operatore KEDA mancante"
  }
}

run "bucket_sconosciuto" {
  command = plan
  variables {
    servizi = { x = { s3 = { archivio = ["lettura"] } } }
  }
  expect_failures = [aws_iam_role.servizio]
}

run "permesso_sconosciuto" {
  command = plan
  variables {
    servizi = { x = { s3 = { aip = ["tutto"] } } }
  }
  expect_failures = [var.servizi]
}

run "servizio_senza_permessi" {
  command = plan
  variables {
    servizi = { x = {} }
  }
  expect_failures = [var.servizi]
}

run "produzione_senza_topic" {
  command = plan
  variables {
    servizi = { x = { kafka = ["produzione"] } }
  }
  expect_failures = [var.servizi]
}

run "consumo_senza_gruppi" {
  command = plan
  variables {
    servizi = { x = { kafka = ["consumo"], kafka_lettura = ["t"] } }
  }
  expect_failures = [var.servizi]
}

# R05 (ciclo-vita, contratto Q2): solo il servizio del ciclo di vita cancella
# VERSIONI nel WORM (dopo la scadenza della retention: S3 COMPLIANCE la rifiuta
# prima) e ne elenca le versioni; nessuno ha s3:BypassGovernanceRetention.
# R05 (ciclo-vita, contratto Q2) e revisione R05 (R3): SOLO il lavoratore di
# ciclo-vita cancella versioni nel WORM, e solo sotto le chiavi dei PdA; l'API
# esposta all'ingress non cancella e non tocca il legal hold; registro/ negato.
run "scarto_solo_lavoratore_ciclo_vita" {
  command = plan
  variables {
    servizi = run.valori.servizi
  }
  assert {
    condition = anytrue([for d in jsondecode(aws_iam_role_policy.servizio["ciclo-vita-lavoratore"].policy).Statement :
      d.Effect == "Allow" && contains(d.Action, "s3:DeleteObjectVersion")
    && d.Resource == ["arn:aws:s3:::verumdoc-prova-aip-prod/????????-????-????-????-????????????/*"]])
    error_message = "Il lavoratore di ciclo-vita cancella le versioni SOLO sotto le chiavi dei PdA"
  }
  assert {
    condition = anytrue([for d in jsondecode(aws_iam_role_policy.servizio["ciclo-vita-lavoratore"].policy).Statement :
    contains(d.Action, "s3:ListBucketVersions") && d.Resource == ["arn:aws:s3:::verumdoc-prova-aip-prod"]])
    error_message = "Il lavoratore di ciclo-vita deve elencare le versioni degli oggetti di un PdA"
  }
  assert {
    condition = anytrue([for d in jsondecode(aws_iam_role_policy.servizio["ciclo-vita-lavoratore"].policy).Statement :
    d.Effect == "Allow" && contains(d.Action, "s3:GetObjectRetention") && contains(d.Action, "s3:GetObjectLegalHold")])
    error_message = "Il lavoratore di ciclo-vita deve leggere retention e legal hold prima di ogni cancellazione"
  }
  # Nessun Allow di cancellazione o di elenco delle versioni fuori dal lavoratore
  assert {
    condition = !anytrue(flatten([for k, p in aws_iam_role_policy.servizio : [for d in jsondecode(p.policy).Statement :
      k != "ciclo-vita-lavoratore" && d.Effect == "Allow"
    && (contains(d.Action, "s3:DeleteObjectVersion") || contains(d.Action, "s3:ListBucketVersions"))]]))
    error_message = "Solo il lavoratore di ciclo-vita cancella o elenca le versioni nel WORM"
  }
  # L'API esposta: nessun legal hold, scritture solo sotto pratiche/
  assert {
    condition = !anytrue([for d in jsondecode(aws_iam_role_policy.servizio["ciclo-vita"].policy).Statement :
    d.Effect == "Allow" && contains(d.Action, "s3:PutObjectLegalHold")])
    error_message = "L'API di ciclo-vita non deve impostare né togliere il legal hold"
  }
  assert {
    condition = !anytrue(flatten([for k in ["ciclo-vita", "ciclo-vita-lavoratore"] : [
      for d in jsondecode(aws_iam_role_policy.servizio[k].policy).Statement :
      d.Effect == "Allow" && (contains(d.Action, "s3:PutObject") || contains(d.Action, "s3:PutObjectLegalHold"))
    && d.Resource != ["arn:aws:s3:::verumdoc-prova-aip-prod/pratiche/*"] && d.Sid != "S3OggettiStaging"]]))
    error_message = "ciclo-vita scrive nel WORM solo sotto pratiche/ (mai PdA, registro/, audit) e mai il legal hold"
  }
  # R06 (VD-C-126): l'API esposta di ciclo-vita non legge lo staging (solo il lavoratore)
  assert {
    condition = !anytrue([for d in jsondecode(aws_iam_role_policy.servizio["ciclo-vita"].policy).Statement :
    d.Effect == "Allow" && anytrue([for r in flatten([d.Resource]) : startswith(r, "arn:aws:s3:::verumdoc-prova-staging")])])
    error_message = "L'API di ciclo-vita non deve avere permessi sullo staging"
  }
  # Deny esplicito su registro/ per ciclo-vita (API e lavoratore) e aip-builder
  assert {
    condition = alltrue([for k in ["ciclo-vita", "ciclo-vita-lavoratore", "aip-builder"] :
      anytrue([for d in jsondecode(aws_iam_role_policy.servizio[k].policy).Statement :
        d.Effect == "Deny" && contains(d.Action, "s3:PutObject") && contains(d.Action, "s3:DeleteObjectVersion")
    && contains(d.Resource, "arn:aws:s3:::verumdoc-prova-aip-prod/registro/*")])])
    error_message = "registro/ deve essere negato esplicitamente a ciclo-vita e aip-builder"
  }
  assert {
    condition = !anytrue(flatten([for k, p in aws_iam_role_policy.servizio : [for d in jsondecode(p.policy).Statement :
    contains(d.Action, "s3:BypassGovernanceRetention")]]))
    error_message = "Nessun servizio può aggirare la retention"
  }
}

run "prefissi_oggetti_con_jolly_rifiutati" {
  command = plan
  variables {
    servizi = { x = { s3 = { aip = ["scarto"] }, prefissi_oggetti = { aip = { scarto = ["*/"] } } } }
  }
  expect_failures = [var.servizi]
}

run "prefissi_oggetti_su_permesso_assente_rifiutati" {
  command = plan
  variables {
    servizi = { x = { s3 = { aip = ["lettura"] }, prefissi_oggetti = { aip = { scarto = ["pratiche/"] } } } }
  }
  expect_failures = [var.servizi]
}

run "nega_prefissi_con_jolly_rifiutati" {
  command = plan
  variables {
    servizi = { x = { s3 = { aip = ["lettura"] }, nega_prefissi = { aip = ["registro/*"] } } }
  }
  expect_failures = [var.servizi]
}

# Revisione 2 di R05 (S4): la hashchain (esposta) scrive solo sotto registro/ e
# sui RdV dei PdA; il legal hold SOLO sui RdV (PdA permanenti); nessun Allow di
# scrittura o legal hold sull'intero bucket
run "hashchain_scrive_solo_registro_e_rdv" {
  command = plan
  variables {
    servizi = run.valori.servizi
  }
  assert {
    condition = !anytrue([for d in jsondecode(aws_iam_role_policy.servizio["hashchain"].policy).Statement :
      d.Effect == "Allow" && (contains(d.Action, "s3:PutObject") || contains(d.Action, "s3:PutObjectLegalHold")
    || contains(d.Action, "s3:PutObjectRetention")) && contains(d.Resource, "arn:aws:s3:::verumdoc-prova-aip-prod/*")])
    error_message = "La hashchain non deve scrivere, mettere retention o legal hold su tutto il bucket dei PdA"
  }
  assert {
    condition = alltrue([for d in jsondecode(aws_iam_role_policy.servizio["hashchain"].policy).Statement :
      d.Resource == ["arn:aws:s3:::verumdoc-prova-aip-prod/????????-????-????-????-????????????/????????-????-????-????-????????????/RdV_Definitiv*",
                     "arn:aws:s3:::verumdoc-prova-aip-prod/????????-????-????-????-????????????/????????-????-????-????-????????????/Sigillo_*"]
    if d.Effect == "Allow" && contains(d.Action, "s3:PutObjectLegalHold")])
    error_message = "La hashchain imposta il legal hold SOLO sui RdV e sulle buste sigillate (Sigillo_, R13a) dei PdA"
  }
  assert {
    condition = anytrue([for d in jsondecode(aws_iam_role_policy.servizio["hashchain"].policy).Statement :
      d.Effect == "Allow" && contains(d.Action, "s3:PutObject")
    && contains(d.Resource, "arn:aws:s3:::verumdoc-prova-aip-prod/registro/*")])
    error_message = "La hashchain deve scrivere sotto registro/"
  }
  assert {
    condition = anytrue([for d in jsondecode(aws_iam_role_policy.servizio["hashchain"].policy).Statement :
      d.Effect == "Allow" && contains(d.Action, "s3:GetObject")
    && d.Resource == ["arn:aws:s3:::verumdoc-prova-aip-prod/*"]])
    error_message = "La hashchain deve leggere i PdA (verifica)"
  }
}

run "prefisso_con_nome_di_file_ammesso_ma_senza_jolly" {
  command = plan
  variables {
    servizi = { x = { s3 = { aip = ["scrittura"] }, prefissi_oggetti = { aip = { scrittura = ["a/b*"] } } } }
  }
  expect_failures = [var.servizi]
}
