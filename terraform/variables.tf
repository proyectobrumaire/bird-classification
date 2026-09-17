variable "aws_region" {
  description = "Región AWS"
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "Nombre del bucket S3 (debe ser globalmente único)"
  type        = string
  default     = "brumaire-data"
}

variable "rtc_utc_offset_h" {
  description = "Offset horario del RTC respecto a UTC (2 = Francia/CEST, -5 = Colombia)"
  type        = number
  default     = 2
}

