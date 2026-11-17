locals {
  static_ip_name = var.static_ip_name != null ? var.static_ip_name : "${var.instance_name}-static-ip"
}

resource "aws_lightsail_instance" "dev" {
  name              = var.instance_name
  availability_zone = var.availability_zone
  blueprint_id      = var.blueprint_id
  bundle_id         = var.bundle_id
  ip_address_type   = "dualstack"
  tags              = var.tags
}

resource "aws_lightsail_static_ip" "dev" {
  name = local.static_ip_name
}

resource "aws_lightsail_static_ip_attachment" "dev" {
  static_ip_name = aws_lightsail_static_ip.dev.name
  instance_name  = aws_lightsail_instance.dev.name
}

resource "aws_lightsail_instance_public_ports" "dev" {
  instance_name = aws_lightsail_instance.dev.name

  port_info {
    protocol   = "tcp"
    from_port  = 22
    to_port    = 22
    cidrs      = var.ssh_allowed_ipv4_cidrs
    ipv6_cidrs = var.ssh_allowed_ipv6_cidrs
  }

  port_info {
    protocol   = "tcp"
    from_port  = 80
    to_port    = 80
    cidrs      = ["0.0.0.0/0"]
    ipv6_cidrs = ["::/0"]
  }

  port_info {
    protocol   = "tcp"
    from_port  = 443
    to_port    = 443
    cidrs      = ["0.0.0.0/0"]
    ipv6_cidrs = ["::/0"]
  }
}
