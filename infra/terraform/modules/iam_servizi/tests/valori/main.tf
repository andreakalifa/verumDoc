# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo d'appoggio dei test: il blocco «iam» REALE di infra/helm/values.yaml,
# letto come negli ambienti (environments/<amb>/main.tf, local.iam_servizi).
locals {
  servizi      = yamldecode(file("${path.module}/../../../../../helm/values.yaml")).services
  kafka_topics = yamldecode(file("${path.module}/../../../../../kafka/topics.yaml")).topics
  iam = merge(
    { for nome, s in local.servizi : nome => try(s.iam, {})
    if try(s.enabled, false) && try(s.irsa, true) != false && try(s.account, "principale") == "principale" },
    merge([for nome, s in local.servizi : { for wn, w in try(s.workers, {}) : "${nome}-${wn}" => w.iam
      if try(w.iam, null) != null && try(w.enabled, true) != false }
    if try(s.enabled, false) && try(s.account, "principale") == "principale"]...),
  )
}

# Come gli ambienti (R04 correzioni): topic precisi da infra/kafka/topics.yaml
output "servizi" {
  value = { for nome, iam in local.iam : nome => merge(iam, {
    kafka_scrittura = [for t in local.kafka_topics : t.name if contains(try(t.producers, []), nome)]
    kafka_lettura   = [for t in local.kafka_topics : t.name if contains(try(t.consumers, []), nome)]
  }) }
}
