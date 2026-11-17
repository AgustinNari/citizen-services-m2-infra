variable "aws_region" {
  description = "Región AWS donde se administrará el entorno DEV."
  type        = string
  default     = "sa-east-1"
}

variable "availability_zone" {
  description = "Availability Zone de Lightsail; debe pertenecer a aws_region."
  type        = string
  default     = "sa-east-1a"
}

variable "instance_name" {
  description = "Nombre de la instancia Lightsail DEV."
  type        = string
  default     = "da2-m2-dev"
}

variable "blueprint_id" {
  description = "Identificador del blueprint de Lightsail."
  type        = string
  default     = "ubuntu_24_04"
}

variable "bundle_id" {
  description = "Identificador del bundle de Lightsail, pendiente de confirmación con AWS Support."
  type        = string
}

variable "static_ip_name" {
  description = "Nombre de la Static IP. Si es null, se deriva del nombre de la instancia."
  type        = string
  default     = null

  validation {
    condition     = var.static_ip_name == null ? true : length(trimspace(var.static_ip_name)) > 0
    error_message = "static_ip_name debe ser null o un nombre no vacío."
  }
}

variable "ssh_allowed_ipv4_cidrs" {
  description = "CIDR IPv4 autorizados para acceder por SSH. No usar 0.0.0.0/0."
  type        = set(string)

  validation {
    condition = (
      length(var.ssh_allowed_ipv4_cidrs) > 0 &&
      !contains(var.ssh_allowed_ipv4_cidrs, "0.0.0.0/0") &&
      alltrue([for cidr in var.ssh_allowed_ipv4_cidrs : can(cidrhost(cidr, 0))])
    )
    error_message = "Indicá al menos un CIDR válido y restringido para SSH; 0.0.0.0/0 no está permitido."
  }
}

variable "ssh_allowed_ipv6_cidrs" {
  description = "CIDR IPv6 autorizados para SSH. Puede quedar vacío si no se utilizará SSH mediante IPv6."
  type        = set(string)
  default     = []

  validation {
    condition = (
      !contains(var.ssh_allowed_ipv6_cidrs, "::/0") &&
      alltrue([for cidr in var.ssh_allowed_ipv6_cidrs : can(cidrhost(cidr, 0))])
    )
    error_message = "Los CIDR IPv6 de SSH deben ser válidos y no pueden incluir ::/0."
  }
}

variable "tags" {
  description = "Tags aplicados a la instancia Lightsail DEV."
  type        = map(string)
  default = {
    Environment = "dev"
    Module      = "m2"
    Project     = "da2"
  }
}
