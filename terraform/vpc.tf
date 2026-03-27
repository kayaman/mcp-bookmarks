data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-vpc"
    Component = "network"
  })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-igw"
    Component = "network"
  })
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 4, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-public-${count.index + 1}"
    Tier      = "public"
    Component = "network"
  })
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 4, count.index + 8)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-private-${count.index + 1}"
    Tier      = "private"
    Component = "network"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-rt-public"
    Component = "network"
  })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-rt-private"
    Component = "network"
  })
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Gateway endpoints (no hourly charge) — reduz saída para S3/DynamoDB a partir da VPC
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat([aws_route_table.public.id], [aws_route_table.private.id])

  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-vpce-s3"
    Component = "network-endpoint"
  })
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat([aws_route_table.public.id], [aws_route_table.private.id])

  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-vpce-dynamodb"
    Component = "network-endpoint"
  })
}
