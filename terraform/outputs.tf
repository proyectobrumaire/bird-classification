output "presigner_secret" {
  description = "Secret para autenticar Flutter contra el presigner — pégalo en la app"
  value       = random_password.presigner_secret.result
  sensitive   = true
}

output "bucket_name" {
  description = "Nombre del bucket S3"
  value       = aws_s3_bucket.brumaire.id
}

output "presigner_url" {
  description = "URL que usa Flutter para pedir pre-signed URLs"
  value       = aws_apigatewayv2_stage.presigner.invoke_url
}

output "ecr_repository_url" {
  description = "URL del repositorio ECR"
  value       = aws_ecr_repository.classifier.repository_url
}

output "classifier_function_name" {
  description = "Nombre de la Lambda clasificadora"
  value       = aws_lambda_function.classifier.function_name
}

output "gallery_url" {
  description = "Endpoint galería: POST /gallery con x-api-key y body {from, to, species?}"
  value       = "${aws_apigatewayv2_stage.presigner.invoke_url}gallery"
}
