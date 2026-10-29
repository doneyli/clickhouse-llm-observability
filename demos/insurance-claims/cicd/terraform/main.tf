# Langfuse in an AWS account, provisioned by the infrastructure pipeline, plus the demo's Langfuse
# configuration applied as code from the same pipeline. A PATTERN to adapt, not a turnkey stack:
# variable names follow the official module's README (github.com/langfuse/langfuse-terraform-aws) —
# pin a module version and check its inputs before you apply.
#
# Two layers, two owners:
#   1. Infrastructure (platform team): the official Langfuse AWS module — EKS, Aurora Postgres,
#      ElastiCache, S3, ClickHouse — in the bank's/insurer's own VPC. Enterprise features are
#      unlocked by the license key in the same containers.
#   2. Langfuse configuration (use-case team, reviewed by the platform team): prompts, evaluators,
#      rules, score configs, datasets — all public-API calls, run by the seed scripts. There is no
#      Terraform provider for these; terraform_data + local-exec re-runs a seeder when its inputs change.

terraform {
  required_version = ">= 1.6"
}

variable "domain" { type = string }                       # e.g. langfuse.claims.internal.example
variable "langfuse_license_key" {
  type      = string
  sensitive = true
}

module "langfuse" {
  source = "github.com/langfuse/langfuse-terraform-aws?ref=1.1.1"   # pin; review inputs per release
  domain = var.domain
  name   = "langfuse"

  # Private networking, HA data stores
  vpc_cidr               = "10.40.0.0/16"
  use_single_nat_gateway = false
  postgres_instance_count = 2
  cache_instance_count    = 2

  # Pin the application version you scanned through your image registry
  # app_version = "4.x.y"

  additional_env = [
    { name = "LANGFUSE_EE_LICENSE_KEY", valueFrom = { secretKeyRef = { name = "langfuse-ee", key = "license" } } },
  ]
}

# ── Layer 2: Langfuse configuration as code (re-applied when the definitions change) ──
locals {
  demo = "${path.module}/../.."
}

resource "terraform_data" "prompts" {
  triggers_replace = [filesha256("${local.demo}/claims/prompts.py")]
  provisioner "local-exec" {
    working_dir = local.demo
    command     = ".venv/bin/python scripts/seed_prompts.py"
  }
}

resource "terraform_data" "evaluators" {
  triggers_replace = [filesha256("${local.demo}/scripts/seed_evals.py")]
  provisioner "local-exec" {
    working_dir = local.demo
    command     = ".venv/bin/python scripts/seed_evals.py"
  }
}

resource "terraform_data" "datasets" {
  triggers_replace = [filesha256("${local.demo}/data/claims/golden.json"),
                      filesha256("${local.demo}/data/claims/adversarial.json")]
  provisioner "local-exec" {
    working_dir = local.demo
    command     = ".venv/bin/python scripts/seed_datasets.py"
  }
}
