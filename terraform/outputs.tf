output "instance_name" {
  description = "Nombre de la instancia Lightsail DEV."
  value       = aws_lightsail_instance.dev.name
}

output "static_ip_address" {
  description = "Dirección IPv4 pública estática asociada a la instancia."
  value       = aws_lightsail_static_ip.dev.ip_address
}

output "aws_region" {
  description = "Región AWS configurada para el entorno DEV."
  value       = var.aws_region
}
