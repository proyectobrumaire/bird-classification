locals {
  station = "brumaire-1"
}

# --- Dashboard 1: Operacional (sensores ambientales + eventos) ---
resource "aws_cloudwatch_dashboard" "operational" {
  dashboard_name = "Brumaire-Operacional"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Temperatura (T1_K)"
          view    = "timeSeries"
          stacked = false
          metrics = [["Brumaire", "T1_K", "Station", local.station]]
          period  = 60
          stat    = "Average"
          region  = var.aws_region
          yAxis   = { left = { label = "°C" } }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Humedad (H1_K / H2_K)"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["Brumaire", "H1_K", "Station", local.station],
            ["Brumaire", "H2_K", "Station", local.station],
          ]
          period = 60
          stat   = "Average"
          region = var.aws_region
          yAxis  = { left = { label = "%" } }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "Punto de rocío promedio (P1_K)"
          view    = "timeSeries"
          stacked = false
          metrics = [["Brumaire", "P1_K", "Station", local.station]]
          period  = 60
          stat    = "Average"
          region  = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "PWM Peltier (P2_K)"
          view    = "timeSeries"
          stacked = false
          metrics = [["Brumaire", "P2_K", "Station", local.station]]
          period  = 60
          stat    = "Average"
          region  = var.aws_region
          yAxis   = { left = { label = "%" } }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          title   = "Peso agua en tanque (W1_K)"
          view    = "timeSeries"
          stacked = false
          metrics = [["Brumaire", "W1_K", "Station", local.station]]
          period  = 60
          stat    = "Average"
          region  = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          title   = "Eventos (BOOT / PELTIER_ON / PELTIER_OFF)"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["Brumaire", "Event", "Station", local.station, "Type", "BOOT",        { stat = "Sum" }],
            ["Brumaire", "Event", "Station", local.station, "Type", "PELTIER_ON",  { stat = "Sum" }],
            ["Brumaire", "Event", "Station", local.station, "Type", "PELTIER_OFF", { stat = "Sum" }],
          ]
          period = 300
          region = var.aws_region
        }
      },
    ]
  })
}

# --- Dashboard 2: Detecciones generales ---
resource "aws_cloudwatch_dashboard" "birds" {
  dashboard_name = "Brumaire-Aves"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 24
        height = 6
        properties = {
          title   = "Detecciones totales por hora"
          view    = "timeSeries"
          stacked = true
          metrics = [
            ["Brumaire", "BirdDetection", "Station", local.station, { stat = "SampleCount", period = 3600, label = "Total" }],
          ]
          period = 3600
          region = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "Confianza promedio"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["Brumaire", "BirdDetection", "Station", local.station, { stat = "Average", period = 3600 }],
          ]
          period = 3600
          stat   = "Average"
          region = var.aws_region
          yAxis  = { left = { min = 0, max = 1 } }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "Detecciones por hora (barras)"
          view    = "bar"
          stacked = false
          metrics = [
            ["Brumaire", "BirdDetection", "Station", local.station, { stat = "SampleCount", period = 3600 }],
          ]
          period = 3600
          region = var.aws_region
        }
      },
    ]
  })
}

# --- Dashboard 3: Especies vs tiempo y ambiente ---
resource "aws_cloudwatch_dashboard" "species" {
  dashboard_name = "Brumaire-Especies"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 24
        height = 7
        properties = {
          title   = "Detecciones por especie (serie de tiempo)"
          view    = "timeSeries"
          stacked = false
          metrics = [
            [{ expression = "SEARCH('{Brumaire,Species,Station} MetricName=\"BirdDetection\" Station=\"${local.station}\"', 'SampleCount', 3600)", label = "", id = "species_all" }],
          ]
          period = 3600
          region = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 7
        width  = 24
        height = 7
        properties = {
          title   = "Especies vs Temperatura ambiente (T1_K)"
          view    = "timeSeries"
          stacked = false
          metrics = [
            [{ expression = "SEARCH('{Brumaire,Species,Station} MetricName=\"BirdDetection\" Station=\"${local.station}\"', 'SampleCount', 3600)", label = "", id = "sp_temp", yAxis = "right" }],
            ["Brumaire", "T1_K", "Station", local.station, { stat = "Average", period = 3600, yAxis = "left", label = "Temperatura (°C)", id = "t1" }],
          ]
          period = 3600
          region = var.aws_region
          yAxis = {
            left  = { label = "°C" }
            right = { label = "Detecciones", min = 0 }
          }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 14
        width  = 24
        height = 7
        properties = {
          title   = "Especies vs Humedad relativa ambiente (H1_K)"
          view    = "timeSeries"
          stacked = false
          metrics = [
            [{ expression = "SEARCH('{Brumaire,Species,Station} MetricName=\"BirdDetection\" Station=\"${local.station}\"', 'SampleCount', 3600)", label = "", id = "sp_hum", yAxis = "right" }],
            ["Brumaire", "H1_K", "Station", local.station, { stat = "Average", period = 3600, yAxis = "left", label = "Humedad (%)", id = "h1" }],
          ]
          period = 3600
          region = var.aws_region
          yAxis = {
            left  = { label = "Humedad (%)" }
            right = { label = "Detecciones", min = 0 }
          }
        }
      },
    ]
  })
}
