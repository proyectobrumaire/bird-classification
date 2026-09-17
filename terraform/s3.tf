resource "aws_s3_bucket" "brumaire" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_versioning" "brumaire" {
  bucket = aws_s3_bucket.brumaire.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "brumaire" {
  bucket = aws_s3_bucket.brumaire.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "brumaire" {
  bucket                  = aws_s3_bucket.brumaire.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# CSV único de clasificaciones — Lambda lo actualiza, Terraform lo crea vacío una sola vez
resource "aws_s3_object" "species_map" {
  bucket       = aws_s3_bucket.brumaire.id
  key          = "classifications/species_map.csv"
  content      = "filename,species,confidence,detector_score,x1,y1,x2,y2,timestamp\n"
  content_type = "text/csv"

  lifecycle {
    ignore_changes = [content, etag] # Lambda escribe aquí, Terraform no pisa
  }
}

# Notificaciones S3 — un solo recurso por bucket
resource "aws_s3_bucket_notification" "triggers" {
  bucket = aws_s3_bucket.brumaire.id

  # Imagen RAW → clasificador ML
  lambda_function {
    lambda_function_arn = aws_lambda_function.classifier.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "images/raw/"
    filter_suffix       = ".jpg"
  }

  # Log de app → procesador de métricas
  lambda_function {
    lambda_function_arn = aws_lambda_function.log_processor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "logs/app/"
    filter_suffix       = ".json"
  }

  depends_on = [
    aws_lambda_permission.allow_s3_classifier,
    aws_lambda_permission.allow_s3_log_processor,
  ]
}
