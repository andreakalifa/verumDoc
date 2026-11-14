# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo VPC — Rete privata VerumDoc
# Riferimento piano: F0.2 — infra/terraform/modules/vpc
#
# Architettura 3-tier per 3 AZ eu-south-1:
#   Pubbliche:   Load Balancer, NAT Gateway, Bastion (se necessario)
#   Private:     Pod Kubernetes (EKS), Celery workers
#   Isolate:     Database RDS Aurora, ElastiCache Redis (nessun accesso internet)

locals {
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "vpc"
    ManagedBy   = "Terraform"
  }
}

# ── VPC ───────────────────────────────────────────────────────────
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(local.tags, { Name = "${var.project}-${var.environment}" })
}

# ── INTERNET GATEWAY (per subnet pubbliche) ───────────────────────
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.tags, { Name = "${var.project}-${var.environment}-igw" })
}

# ── SUBNET PUBBLICHE (1 per AZ) ──────────────────────────────────
resource "aws_subnet" "public" {
  count             = length(var.azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.public_subnets[count.index]
  availability_zone = var.azs[count.index]

  # I pod EKS nei public subnet ottengono IP pubblico (Load Balancer)
  map_public_ip_on_launch = false # Solo ALB ottiene IP pubblico

  tags = merge(local.tags, {
    Name                                                      = "${var.project}-${var.environment}-public-${var.azs[count.index]}"
    "kubernetes.io/role/elb"                                  = "1"
    "kubernetes.io/cluster/${var.project}-${var.environment}" = "shared"
  })
}

# ── SUBNET PRIVATE (EKS, Celery) ──────────────────────────────────
resource "aws_subnet" "private" {
  count             = length(var.azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnets[count.index]
  availability_zone = var.azs[count.index]

  tags = merge(local.tags, {
    Name                                                      = "${var.project}-${var.environment}-private-${var.azs[count.index]}"
    "kubernetes.io/role/internal-elb"                         = "1"
    "kubernetes.io/cluster/${var.project}-${var.environment}" = "shared"
  })
}

# ── SUBNET ISOLATE (RDS, Redis — nessun accesso internet) ─────────
resource "aws_subnet" "isolated" {
  count             = length(var.azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.isolated_subnets[count.index]
  availability_zone = var.azs[count.index]

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-isolated-${var.azs[count.index]}"
  })
}

# ── ELASTIC IP per NAT Gateway (1 per AZ per HA) ─────────────────
resource "aws_eip" "nat" {
  count  = length(var.azs)
  domain = "vpc"
  tags   = merge(local.tags, { Name = "${var.project}-${var.environment}-nat-eip-${count.index + 1}" })

  depends_on = [aws_internet_gateway.main]
}

# ── NAT GATEWAY (1 per AZ — ridondanza) ──────────────────────────
resource "aws_nat_gateway" "main" {
  count         = length(var.azs)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags       = merge(local.tags, { Name = "${var.project}-${var.environment}-nat-${var.azs[count.index]}" })
  depends_on = [aws_internet_gateway.main]
}

# ── ROUTE TABLE pubblica ──────────────────────────────────────────
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-rt-public" })
}

resource "aws_route_table_association" "public" {
  count          = length(var.azs)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ── ROUTE TABLE private (1 per AZ — punta al NAT della stessa AZ) ─
resource "aws_route_table" "private" {
  count  = length(var.azs)
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }
  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-rt-private-${var.azs[count.index]}" })
}

resource "aws_route_table_association" "private" {
  count          = length(var.azs)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ── ROUTE TABLE isolate (nessuna route internet) ──────────────────
resource "aws_route_table" "isolated" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.tags, { Name = "${var.project}-${var.environment}-rt-isolated" })
}

resource "aws_route_table_association" "isolated" {
  count          = length(var.azs)
  subnet_id      = aws_subnet.isolated[count.index].id
  route_table_id = aws_route_table.isolated.id
}

# ── VPC FLOW LOGS (audit accessi rete) ────────────────────────────
resource "aws_cloudwatch_log_group" "vpc_flow_logs" {
  name              = "/aws/vpc/flow-logs/${var.project}-${var.environment}"
  retention_in_days = 90
  tags              = local.tags
}

resource "aws_iam_role" "vpc_flow_logs" {
  name = "${var.project}-${var.environment}-vpc-flow-logs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "vpc_flow_logs" {
  name = "vpc-flow-logs-policy"
  role = aws_iam_role.vpc_flow_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogGroups", "logs:DescribeLogStreams"]
      Resource = "*"
    }]
  })
}

resource "aws_flow_log" "main" {
  iam_role_arn    = aws_iam_role.vpc_flow_logs.arn
  log_destination = aws_cloudwatch_log_group.vpc_flow_logs.arn
  traffic_type    = "ALL"
  vpc_id          = aws_vpc.main.id
  tags            = merge(local.tags, { Name = "${var.project}-${var.environment}-flow-log" })
}

# ── SECURITY GROUPS base ──────────────────────────────────────────
resource "aws_security_group" "alb" {
  name        = "${var.project}-${var.environment}-alb"
  description = "Application Load Balancer — accetta HTTPS da Internet"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS da Internet"
  }
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP redirect a HTTPS"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-sg-alb" })
}

resource "aws_security_group" "eks_nodes" {
  name        = "${var.project}-${var.environment}-eks-nodes"
  description = "EKS worker nodes — comunicazione intra-cluster"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
    description = "Comunicazione intra-node"
  }
  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
    description     = "ALB → nodes HTTPS"
  }
  ingress {
    from_port       = 8000
    to_port         = 9000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
    description     = "ALB → servizi applicativi"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-sg-eks-nodes" })
}

resource "aws_security_group" "rds" {
  name        = "${var.project}-${var.environment}-rds"
  description = "RDS Aurora — accetta solo da EKS nodes"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
    description     = "PostgreSQL da EKS nodes"
  }
  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-sg-rds" })
}

resource "aws_security_group" "redis" {
  name        = "${var.project}-${var.environment}-redis"
  description = "ElastiCache Redis — accetta solo da EKS nodes"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
    description     = "Redis da EKS nodes"
  }
  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-sg-redis" })
}

resource "aws_security_group" "kafka" {
  name        = "${var.project}-${var.environment}-kafka"
  description = "MSK Kafka — accetta solo da EKS nodes"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 9092
    to_port         = 9092
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
    description     = "Kafka plaintext da EKS nodes"
  }
  ingress {
    from_port       = 9094
    to_port         = 9094
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
    description     = "Kafka TLS da EKS nodes"
  }
  tags = merge(local.tags, { Name = "${var.project}-${var.environment}-sg-kafka" })
}
