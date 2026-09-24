locals {
  # Cambia cuando cambia cualquier archivo fuente → Terraform rebuilds la imagen
  classifier_src_hash = sha256(join("", [
    filesha256("${path.module}/../bird_detector.py"),
    filesha256("${path.module}/../dl_main.py"),
    filesha256("${path.module}/../predict_image_from_tensors.py"),
    filesha256("${path.module}/../utils_bounding_boxes_separation.py"),
    filesha256("${path.module}/../utils_crop_segmentation.py"),
    filesha256("${path.module}/../lambda_handler.py"),
    filesha256("${path.module}/../Dockerfile"),
  ]))

  image_tag = substr(local.classifier_src_hash, 0, 8)
}

# --- ECR ---
resource "aws_ecr_repository" "classifier" {
  name                 = "brumaire-classifier"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "classifier" {
  repository = aws_ecr_repository.classifier.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Conservar solo las últimas 2 imágenes"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 2
      }
      action = { type = "expire" }
    }]
  })
}

# --- Lambda procesadora de logs → CloudWatch Metrics ---
data "archive_file" "log_processor" {
  type        = "zip"
  source_file = "${path.module}/../log_processor.py"
  output_path = "${path.module}/.build/log_processor.zip"
}

resource "aws_lambda_function" "log_processor" {
  function_name    = "brumaire-log-processor"
  role             = aws_iam_role.lambda_exec.arn
  package_type     = "Zip"
  runtime          = "python3.11"
  handler          = "log_processor.handler"
  filename         = data.archive_file.log_processor.output_path
  source_code_hash = data.archive_file.log_processor.output_base64sha256

  timeout     = 60
  memory_size = 256

  environment {
    variables = {
      STATION_NAME     = "brumaire-1"
      DYNAMO_TABLE     = aws_dynamodb_table.telemetry.name
      RTC_UTC_OFFSET_H = var.rtc_utc_offset_h
    }
  }
}

resource "aws_lambda_permission" "allow_s3_log_processor" {
  statement_id  = "AllowS3InvokeLogs"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.log_processor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.brumaire.arn
}

resource "aws_cloudwatch_log_group" "log_processor" {
  name              = "/aws/lambda/${aws_lambda_function.log_processor.function_name}"
  retention_in_days = 7
}

# --- Build y push de la imagen cuando cambia el código ---
resource "null_resource" "build_classifier" {
  triggers = {
    src_hash = local.classifier_src_hash
  }

  provisioner "local-exec" {
    working_dir = "${path.module}/.."
    command     = <<-EOT
      aws ecr get-login-password --region ${var.aws_region} | \
        docker login --username AWS --password-stdin ${aws_ecr_repository.classifier.repository_url}
      docker build -t ${aws_ecr_repository.classifier.repository_url}:${local.image_tag} .
      docker push ${aws_ecr_repository.classifier.repository_url}:${local.image_tag}
    EOT
  }

  depends_on = [aws_ecr_repository.classifier]
}

# --- Lambda clasificadora ML ---
resource "aws_lambda_function" "classifier" {
  function_name = "brumaire-bird-classifier"
  role          = aws_iam_role.lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.classifier.repository_url}:${local.image_tag}"

  timeout     = 300
  memory_size = 3008

  environment {
    variables = {
      BUCKET_NAME      = aws_s3_bucket.brumaire.id
      MODEL_KEY        = "models/bird_species_resnet18.pth"
      DYNAMO_TABLE     = aws_dynamodb_table.telemetry.name
      STATION_NAME     = "brumaire-1"
      RTC_UTC_OFFSET_H = var.rtc_utc_offset_h
    }
  }

  depends_on = [null_resource.build_classifier]
}

resource "aws_lambda_permission" "allow_s3_classifier" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.classifier.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.brumaire.arn
}

# --- Lambda presigner (ZIP — solo boto3, no PyTorch) ---
data "archive_file" "presigner" {
  type        = "zip"
  source_file = "${path.module}/../presigner.py"
  output_path = "${path.module}/.build/presigner.zip"
}

# Secret generado una sola vez — Terraform lo guarda en el state
resource "random_password" "presigner_secret" {
  length  = 32
  special = false
}

resource "aws_lambda_function" "presigner" {
  function_name    = "brumaire-presigner"
  role             = aws_iam_role.lambda_exec.arn
  package_type     = "Zip"
  runtime          = "python3.11"
  handler          = "presigner.handler"
  filename         = data.archive_file.presigner.output_path
  source_code_hash = data.archive_file.presigner.output_base64sha256

  timeout     = 10
  memory_size = 128

  environment {
    variables = {
      BUCKET_NAME = aws_s3_bucket.brumaire.id
      API_SECRET  = random_password.presigner_secret.result
    }
  }
}

# Permiso explícito para invocación pública (auth la maneja el código, no AWS)
resource "aws_lambda_permission" "presigner_public" {
  statement_id           = "AllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.presigner.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# --- CloudWatch Log Groups con retención de 7 días ---
resource "aws_cloudwatch_log_group" "classifier" {
  name              = "/aws/lambda/${aws_lambda_function.classifier.function_name}"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "presigner" {
  name              = "/aws/lambda/${aws_lambda_function.presigner.function_name}"
  retention_in_days = 7
}

# --- Lambda gallery ---
data "archive_file" "gallery" {
  type        = "zip"
  source_file = "${path.module}/../gallery.py"
  output_path = "${path.module}/.build/gallery.zip"
}

resource "aws_lambda_function" "gallery" {
  function_name    = "brumaire-gallery"
  role             = aws_iam_role.lambda_exec.arn
  package_type     = "Zip"
  runtime          = "python3.11"
  handler          = "gallery.handler"
  filename         = data.archive_file.gallery.output_path
  source_code_hash = data.archive_file.gallery.output_base64sha256

  timeout     = 30
  memory_size = 256

  environment {
    variables = {
      BUCKET_NAME      = aws_s3_bucket.brumaire.id
      API_SECRET       = random_password.presigner_secret.result
      STATION_NAME     = "brumaire-1"
      DYNAMO_TABLE     = aws_dynamodb_table.telemetry.name
      RTC_UTC_OFFSET_H = var.rtc_utc_offset_h
    }
  }
}

resource "aws_cloudwatch_log_group" "gallery" {
  name              = "/aws/lambda/${aws_lambda_function.gallery.function_name}"
  retention_in_days = 7
}

# --- API Gateway HTTP API (Lambda Function URL bloqueada a nivel de cuenta) ---
resource "aws_apigatewayv2_api" "presigner" {
  name          = "brumaire-presigner"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["POST"]
    allow_headers = ["content-type", "x-api-key"]
  }
}

resource "aws_apigatewayv2_integration" "presigner" {
  api_id                 = aws_apigatewayv2_api.presigner.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.presigner.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "presigner" {
  api_id    = aws_apigatewayv2_api.presigner.id
  route_key = "POST /"
  target    = "integrations/${aws_apigatewayv2_integration.presigner.id}"
}

resource "aws_apigatewayv2_stage" "presigner" {
  api_id      = aws_apigatewayv2_api.presigner.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "presigner_apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.presigner.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.presigner.execution_arn}/*/*"
}

resource "aws_apigatewayv2_integration" "gallery" {
  api_id                 = aws_apigatewayv2_api.presigner.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.gallery.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "gallery" {
  api_id    = aws_apigatewayv2_api.presigner.id
  route_key = "POST /gallery"
  target    = "integrations/${aws_apigatewayv2_integration.gallery.id}"
}

resource "aws_lambda_permission" "gallery_apigw" {
  statement_id  = "AllowAPIGatewayInvokeGallery"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gallery.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.presigner.execution_arn}/*/*"
}
