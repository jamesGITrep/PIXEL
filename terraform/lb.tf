# -----------------------------------------------------------------------------
# External Application Load Balancer -> frontend only (the only public endpoint)
# -----------------------------------------------------------------------------

resource "google_compute_global_address" "public" {
  name       = "${local.name}-public-ip"
  depends_on = [google_project_service.apis]
}

resource "google_compute_health_check" "frontend_lb" {
  name               = "${local.name}-frontend-lb"
  check_interval_sec = 5
  timeout_sec        = 3
  http_health_check {
    port         = 80
    request_path = "/healthz"
  }
}

resource "google_compute_backend_service" "frontend" {
  name                  = "${local.name}-frontend-backend"
  protocol              = "HTTP"
  port_name             = "http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = 300 # full-resolution exports take several seconds to render
  health_checks         = [google_compute_health_check.frontend_lb.id]
  # During rolling updates and scale-in, a VM being removed stops getting new requests but
  # finishes the ones it has (uploads, job creation, exports) before it is deleted.
  connection_draining_timeout_sec = 120

  backend {
    group           = google_compute_region_instance_group_manager.svc["frontend"].instance_group
    balancing_mode  = "UTILIZATION"
    capacity_scaler = 1.0
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

# Every path goes to the frontend; it forwards /api/* to the gateway internally.
resource "google_compute_url_map" "public" {
  name            = "${local.name}-url-map"
  default_service = google_compute_backend_service.frontend.id
}

resource "google_compute_target_http_proxy" "public" {
  name    = "${local.name}-http-proxy"
  url_map = google_compute_url_map.public.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${local.name}-http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.public.id
  port_range            = "80"
  target                = google_compute_target_http_proxy.public.id
}

# Optional HTTPS with a Google-managed certificate (set var.domain and point its DNS A record
# at the public IP; the certificate provisions automatically).
resource "google_compute_managed_ssl_certificate" "public" {
  count = var.domain == "" ? 0 : 1
  name  = "${local.name}-cert"
  managed {
    domains = [var.domain]
  }
}

resource "google_compute_target_https_proxy" "public" {
  count            = var.domain == "" ? 0 : 1
  name             = "${local.name}-https-proxy"
  url_map          = google_compute_url_map.public.id
  ssl_certificates = [google_compute_managed_ssl_certificate.public[0].id]
}

resource "google_compute_global_forwarding_rule" "https" {
  count                 = var.domain == "" ? 0 : 1
  name                  = "${local.name}-https"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.public.id
  port_range            = "443"
  target                = google_compute_target_https_proxy.public[0].id
}

# -----------------------------------------------------------------------------
# Internal TCP Load Balancer -> API Gateway (private IP, reachable only from the frontend)
# -----------------------------------------------------------------------------

resource "google_compute_address" "gateway_ilb" {
  name         = "${local.name}-gateway-ilb"
  region       = var.region
  subnetwork   = google_compute_subnetwork.services.id
  address_type = "INTERNAL"
}

resource "google_compute_region_health_check" "gateway_lb" {
  name               = "${local.name}-gateway-lb"
  region             = var.region
  check_interval_sec = 5
  timeout_sec        = 3
  http_health_check {
    port         = 8000
    request_path = "/health"
  }
}

resource "google_compute_region_backend_service" "gateway" {
  name                  = "${local.name}-gateway-backend"
  region                = var.region
  protocol              = "TCP"
  load_balancing_scheme = "INTERNAL"
  health_checks         = [google_compute_region_health_check.gateway_lb.id]
  # Let in-flight API calls finish before a gateway VM is removed (rolling update / scale-in)
  connection_draining_timeout_sec = 120

  backend {
    group          = google_compute_region_instance_group_manager.svc["gateway"].instance_group
    balancing_mode = "CONNECTION"
  }
}

resource "google_compute_forwarding_rule" "gateway" {
  name                  = "${local.name}-gateway-ilb"
  region                = var.region
  load_balancing_scheme = "INTERNAL"
  backend_service       = google_compute_region_backend_service.gateway.id
  ip_address            = google_compute_address.gateway_ilb.address
  ip_protocol           = "TCP"
  ports                 = ["8000"]
  network               = google_compute_network.vpc.id
  subnetwork            = google_compute_subnetwork.services.id
}
