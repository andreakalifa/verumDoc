#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Script: crea il backend S3 per lo stato Terraform
# Da eseguire UNA SOLA VOLTA prima del primo terraform init
#
# Uso:
#   export AWS_REGION="eu-south-1"
#   export AWS_ACCOUNT_ID="123456789012"
#   bash bootstrap_terraform_backend.sh

set -euo pipefail

REGION="${AWS_REGION:-eu-south-1}"
BUCKET="verumdoc-terraform-state"
TABLE="verumdoc-terraform-lock"

echo "Bootstrap Terraform backend..."
echo "  Region: $REGION"
echo "  Bucket: $BUCKET"
echo "  Lock table: $TABLE"

# Crea bucket S3 per lo stato
echo ""
echo "Creando bucket S3..."
aws s3api create-bucket \
  --bucket "$BUCKET" \
  --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION"

# Abilita versioning (per recovery stato corrotto)
aws s3api put-bucket-versioning \
  --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled

# Abilita cifratura server-side
aws s3api put-bucket-encryption \
  --bucket "$BUCKET" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# Blocca accesso pubblico
aws s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

echo "  Bucket S3: OK"

# Crea tabella DynamoDB per il lock
echo "Creando tabella DynamoDB per lock stato..."
aws dynamodb create-table \
  --table-name "$TABLE" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$REGION"

echo "  Tabella DynamoDB: OK"
echo ""
echo "Bootstrap completato. Ora puoi eseguire:"
echo "  cd infra/terraform/environments/prod"
echo "  terraform init"
