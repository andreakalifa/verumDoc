# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
# Modulo EKS — Cluster Kubernetes gestito
# Riferimento piano: F0.2 — infra/terraform/modules/eks
#
# 3 node group:
#   general: m6i.xlarge (3-10) — servizi FastAPI e Celery standard
#   memory:  r6i.xlarge (2-6)  — Elasticsearch, operazioni intensive
#   spot:    m6i.large  (0-20) — Celery workers (tollerano interruzioni)

locals {
  cluster_name = "${var.project}-${var.environment}"
  tags = {
    Project     = var.project
    Environment = var.environment
    Module      = "eks"
    ManagedBy   = "Terraform"
  }
}

# ── IAM Role per EKS Control Plane ────────────────────────────────
resource "aws_iam_role" "eks_cluster" {
  name = "${local.cluster_name}-eks-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

resource "aws_iam_role_policy_attachment" "eks_vpc_controller" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSVPCResourceController"
  role       = aws_iam_role.eks_cluster.name
}

# ── EKS Cluster ───────────────────────────────────────────────────
resource "aws_eks_cluster" "main" {
  name     = local.cluster_name
  role_arn = aws_iam_role.eks_cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = var.private_subnet_ids
    security_group_ids      = [var.sg_nodes_id]
    endpoint_private_access = true
    endpoint_public_access  = false # API server solo da VPC
  }

  # Secrets cifrati con CMK KMS
  encryption_config {
    provider {
      key_arn = var.kms_secrets_key_arn
    }
    resources = ["secrets"]
  }

  # Log del control plane in CloudWatch
  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  tags = merge(local.tags, { Name = local.cluster_name })
  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
    aws_iam_role_policy_attachment.eks_vpc_controller,
  ]
}

# ── IAM Role per i Node Group ──────────────────────────────────────
resource "aws_iam_role" "eks_nodes" {
  name = "${local.cluster_name}-eks-nodes"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "eks_worker_node" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_cni" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_ecr_readonly" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.eks_nodes.name
}

# R04 (VD-C-101): nessun permesso S3/KMS sul ruolo dei NODI. Ogni servizio ha
# il suo ruolo IRSA con i permessi minimi (modulo iam_servizi); un pod senza
# ruolo non raggiunge comunque i metadati dell'istanza (NetworkPolicy:
# network.internetExcept esclude 169.254.0.0/16).

# ── IRSA: ruoli IAM per i ServiceAccount dei servizi (R03 i3) ─────────
# Provider OIDC del cluster: un pod con ServiceAccount annotato
# (eks.amazonaws.com/role-arn) riceve le credenziali del SUO ruolo, non quelle
# del nodo. Nome del ruolo: <progetto>-<ambiente>-<servizio>, come l'annotazione
# generata da infra/helm/templates/serviceaccount.yaml e da genera_manifest.py.
resource "aws_iam_openid_connect_provider" "eks" {
  url             = aws_eks_cluster.main.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = var.oidc_thumbprints
  tags            = local.tags
}

locals {
  oidc_issuer = replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")
}

# I ruoli dei servizi (R03 i3: solo il gateway) sono nel modulo iam_servizi
# (R04, VD-C-101): uno per ServiceAccount, dal blocco «iam» di values.yaml.

# Driver EFS CSI (R03 i2): volumi ReadWriteMany del canale SFTP. Permessi sul
# ruolo dei nodi: è il driver CSI (DaemonSet di sistema), non un servizio.
resource "aws_iam_role_policy_attachment" "eks_efs_csi" {
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEFSCSIDriverPolicy"
  role       = aws_iam_role.eks_nodes.name
}

# ── Node Group: general (FastAPI services) ─────────────────────────
resource "aws_eks_node_group" "general" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "general"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = var.private_subnet_ids
  instance_types  = var.node_group_general.instance_types

  scaling_config {
    min_size     = var.node_group_general.min_size
    max_size     = var.node_group_general.max_size
    desired_size = var.node_group_general.desired_size
  }

  update_config {
    max_unavailable = 1
  }

  disk_size = var.node_group_general.disk_size_gb

  labels = {
    "node-type" = "general"
    "workload"  = "fastapi"
  }

  tags = merge(local.tags, { Name = "${local.cluster_name}-nodes-general" })

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node,
    aws_iam_role_policy_attachment.eks_cni,
    aws_iam_role_policy_attachment.eks_ecr_readonly,
  ]
}

# ── Node Group: memory (Elasticsearch, heavy workers) ─────────────
resource "aws_eks_node_group" "memory" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "memory"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = var.private_subnet_ids
  instance_types  = var.node_group_memory.instance_types

  scaling_config {
    min_size     = var.node_group_memory.min_size
    max_size     = var.node_group_memory.max_size
    desired_size = var.node_group_memory.desired_size
  }

  update_config { max_unavailable = 1 }
  disk_size = var.node_group_memory.disk_size_gb

  labels = {
    "node-type" = "memory"
    "workload"  = "celery-heavy"
  }

  taint {
    key    = "memory-optimized"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  tags = merge(local.tags, { Name = "${local.cluster_name}-nodes-memory" })
  lifecycle { ignore_changes = [scaling_config[0].desired_size] }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node,
    aws_iam_role_policy_attachment.eks_cni,
    aws_iam_role_policy_attachment.eks_ecr_readonly,
  ]
}

# ── Node Group: spot (Celery workers — tollerano interruzioni) ─────
resource "aws_eks_node_group" "spot" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "spot"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = var.private_subnet_ids
  instance_types  = var.node_group_spot.instance_types

  capacity_type = "SPOT"

  scaling_config {
    min_size     = var.node_group_spot.min_size
    max_size     = var.node_group_spot.max_size
    desired_size = var.node_group_spot.desired_size
  }

  update_config { max_unavailable_percentage = 33 }
  disk_size = var.node_group_spot.disk_size_gb

  labels = {
    "node-type"          = "spot"
    "workload"           = "celery-standard"
    "kubernetes.io/arch" = "amd64"
  }

  taint {
    key    = "spot"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  tags = merge(local.tags, { Name = "${local.cluster_name}-nodes-spot" })
  lifecycle { ignore_changes = [scaling_config[0].desired_size] }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node,
    aws_iam_role_policy_attachment.eks_cni,
    aws_iam_role_policy_attachment.eks_ecr_readonly,
  ]
}

# ── EKS Add-ons (CoreDNS, kube-proxy, VPC CNI) ────────────────────
resource "aws_eks_addon" "coredns" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "coredns"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.general]
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "kube-proxy"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
}

resource "aws_eks_addon" "vpc_cni" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "vpc-cni"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "aws-ebs-csi-driver"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.general]
}

resource "aws_eks_addon" "efs_csi" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "aws-efs-csi-driver"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.general, aws_iam_role_policy_attachment.eks_efs_csi]
}
