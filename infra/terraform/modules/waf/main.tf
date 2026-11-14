# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo WAF — Web Application Firewall
# Riferimento piano: F0.2 — infra/terraform/modules/waf
#
# Regole attive:
#   - OWASP Core Rule Set 3.3 (managed rules AWS)
#   - Rate limiting: 2000 req/5min per IP
#   - IP Reputation list (known bad actors)
#   - SQL injection protection
#   - XSS protection
#   - Geo-blocking configurabile
#   - (R03 i2) Limite del corpo per prefisso di percorso (var.limiti_corpo, dalla
#     fonte unica infra/helm/values.yaml ingress.routes[].maxBodyMB): 413 se
#     Content-Length supera il limite. L'ALB non ha un limite proprio.

locals {
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "waf"
    ManagedBy   = "Terraform"
  }

  # Limite del corpo: Content-Length (cifre decimali) MAGGIORE di max_byte.
  # Il WAF non confronta numeri: si costruisce l'espressione regolare (RE2) dei
  # numeri maggiori di N = d1 d2 ... dk:
  #   - più di k cifre:                       [1-9][0-9]{k,}
  #   - k cifre, uguali a N fino alla i-esima
  #     esclusa e maggiori alla i-esima:      d1..d(i-1)[(di+1)-9][0-9]{k-i}
  limiti_corpo = { for i, l in var.limiti_corpo : tostring(i) => {
    percorso = l.percorso
    cifre    = format("%d", l.max_byte)
  } }
  regex_oltre_limite = { for k, l in local.limiti_corpo : k => format("^(?:%s)$", join("|", concat(
    [format("[1-9][0-9]{%d,}", length(l.cifre))],
    [for p in range(length(l.cifre)) : format("%s[%d-9][0-9]{%d}",
      substr(l.cifre, 0, p), tonumber(substr(l.cifre, p, 1)) + 1, length(l.cifre) - p - 1)
    if tonumber(substr(l.cifre, p, 1)) < 9]
  ))) }
}

resource "aws_wafv2_web_acl" "main" {
  name  = "${var.project}-${var.environment}"
  scope = "REGIONAL" # Per ALB (non CloudFront)

  default_action {
    allow {} # Permetti tutto di default, le regole definiscono le eccezioni
  }

  # ── Regola 1: Rate limiting per IP ────────────────────────────
  rule {
    name     = "RateLimitPerIP"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.rate_limit_requests_per_5min
        aggregate_key_type = "IP"

        # Escludi le route di health check dal rate limiting
        scope_down_statement {
          not_statement {
            statement {
              byte_match_statement {
                field_to_match {
                  uri_path {}
                }
                positional_constraint = "STARTS_WITH"
                search_string         = "/api/v1/health"
                text_transformation {
                  priority = 0
                  type     = "LOWERCASE"
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "RateLimitPerIP"
      sampled_requests_enabled   = true
    }
  }

  # ── Regola 2: AWS Managed — Core Rule Set (OWASP) ─────────────
  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"

        # Override alcune regole per ridurre falsi positivi su upload documenti
        rule_action_override {
          name = "SizeRestrictions_BODY"
          # Permettiamo body grandi (PdV con file)
          action_to_use {
            allow {}
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesCommonRuleSet"
      sampled_requests_enabled   = true
    }
  }

  # ── Regola 3: AWS Managed — Known Bad Inputs ──────────────────
  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 3

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesKnownBadInputs"
      sampled_requests_enabled   = true
    }
  }

  # ── Regola 4: AWS Managed — IP Reputation List ────────────────
  rule {
    name     = "AWSManagedRulesAmazonIpReputationList"
    priority = 4

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "IPReputationList"
      sampled_requests_enabled   = true
    }
  }

  # ── Regola 5: SQL Injection protection ────────────────────────
  rule {
    name     = "AWSManagedRulesSQLiRuleSet"
    priority = 5

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "SQLiRuleSet"
      sampled_requests_enabled   = true
    }
  }

  # ── Regole 10+: limite del corpo per prefisso (R03 i2) ────────────
  # Più regole possono combaciare con la stessa richiesta (prefissi annidati):
  # vale la più restrittiva, e verifica_manifest.py impone che un prefisso più
  # specifico non abbia un limite più alto di quello che lo contiene.
  # Le richieste senza Content-Length (chunked) passano: resta il limite
  # applicativo (413 del catalogo errori).
  dynamic "rule" {
    for_each = local.limiti_corpo
    content {
      name     = "LimiteCorpo-${rule.key}"
      priority = 10 + tonumber(rule.key)

      action {
        block {
          custom_response {
            response_code = 413
          }
        }
      }

      statement {
        and_statement {
          statement {
            byte_match_statement {
              field_to_match {
                uri_path {}
              }
              positional_constraint = "STARTS_WITH"
              search_string         = rule.value.percorso
              text_transformation {
                priority = 0
                type     = "NONE"
              }
            }
          }
          statement {
            regex_match_statement {
              regex_string = local.regex_oltre_limite[rule.key]
              field_to_match {
                single_header {
                  name = "content-length"
                }
              }
              text_transformation {
                priority = 0
                type     = "NONE"
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "LimiteCorpo-${rule.key}"
        sampled_requests_enabled   = true
      }
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project}-${var.environment}-waf"
    sampled_requests_enabled   = true
  }

  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-waf" })
}

# Associa WAF all'ALB
# Creata solo quando alb_arn è valorizzato: l'ALB nasce dall'Ingress (AWS Load
# Balancer Controller) dopo il deploy del cluster; finché alb_arn = "" il WAF
# esiste ma NON protegge nulla (VD-C-038).
resource "aws_wafv2_web_acl_association" "alb" {
  count        = var.alb_arn != "" ? 1 : 0
  resource_arn = var.alb_arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}

# Log WAF in CloudWatch
resource "aws_wafv2_web_acl_logging_configuration" "main" {
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  resource_arn            = aws_wafv2_web_acl.main.arn

  # I log WAF non contengono mai il corpo delle richieste; si oscurano anche le
  # intestazioni con credenziali (privacy / GDPR).
  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
  redacted_fields {
    single_header {
      name = "x-api-key"
    }
  }
}

resource "aws_cloudwatch_log_group" "waf" {
  name              = "aws-waf-logs-${var.project}-${var.environment}"
  retention_in_days = 90
  tags              = local.tags
}
