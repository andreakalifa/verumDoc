# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
output "cluster_name" { value = aws_eks_cluster.main.name }
output "cluster_endpoint" { value = aws_eks_cluster.main.endpoint }
output "cluster_ca_certificate" { value = aws_eks_cluster.main.certificate_authority[0].data }
output "cluster_version" { value = aws_eks_cluster.main.version }
output "node_role_arn" { value = aws_iam_role.eks_nodes.arn }
output "node_group_general_name" { value = aws_eks_node_group.general.node_group_name }
output "node_group_memory_name" { value = aws_eks_node_group.memory.node_group_name }
output "node_group_spot_name" { value = aws_eks_node_group.spot.node_group_name }
output "oidc_provider_arn" { value = aws_iam_openid_connect_provider.eks.arn }
output "oidc_issuer" { value = local.oidc_issuer }
