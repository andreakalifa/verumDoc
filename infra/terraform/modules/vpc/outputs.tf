# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
output "vpc_id" { value = aws_vpc.main.id }
output "vpc_cidr" { value = aws_vpc.main.cidr_block }
output "public_subnet_ids" { value = aws_subnet.public[*].id }
output "private_subnet_ids" { value = aws_subnet.private[*].id }
output "isolated_subnet_ids" { value = aws_subnet.isolated[*].id }
output "sg_alb_id" { value = aws_security_group.alb.id }
output "sg_eks_nodes_id" { value = aws_security_group.eks_nodes.id }
output "sg_rds_id" { value = aws_security_group.rds.id }
output "sg_redis_id" { value = aws_security_group.redis.id }
output "sg_kafka_id" { value = aws_security_group.kafka.id }
output "nat_gateway_ids" { value = aws_nat_gateway.main[*].id }
