data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_exec" {
  name               = "brumaire-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# Logs en CloudWatch
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Acceso al bucket: leer imágenes RAW, escribir processed + CSV + modelo
data "aws_iam_policy_document" "lambda_s3" {
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.brumaire.arn,
      "${aws_s3_bucket.brumaire.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "lambda_s3" {
  name   = "brumaire-lambda-s3"
  role   = aws_iam_role.lambda_exec.id
  policy = data.aws_iam_policy_document.lambda_s3.json
}

data "aws_iam_policy_document" "lambda_cloudwatch_metrics" {
  statement {
    actions = [
      "cloudwatch:PutMetricData",
      "cloudwatch:GetMetricStatistics",
    ]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "lambda_dynamodb" {
  statement {
    actions = [
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:GetItem",
    ]
    resources = [aws_dynamodb_table.telemetry.arn]
  }
}

resource "aws_iam_role_policy" "lambda_dynamodb" {
  name   = "brumaire-lambda-dynamodb"
  role   = aws_iam_role.lambda_exec.id
  policy = data.aws_iam_policy_document.lambda_dynamodb.json
}

resource "aws_iam_role_policy" "lambda_cloudwatch" {
  name   = "brumaire-lambda-cloudwatch"
  role   = aws_iam_role.lambda_exec.id
  policy = data.aws_iam_policy_document.lambda_cloudwatch_metrics.json
}
