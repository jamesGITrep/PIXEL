# -----------------------------------------------------------------------------
# Private network: no VM gets an external IP
# -----------------------------------------------------------------------------

resource "google_compute_network" "vpc" {
  name                    = "${local.name}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "services" {
  name                     = "${local.name}-services"
  network                  = google_compute_network.vpc.id
  region                   = var.region
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true # GCS + Artifact Registry without public IPs
}

# Outbound internet (OS/image updates) for VMs without external IPs
resource "google_compute_router" "router" {
  name    = "${local.name}-router"
  network = google_compute_network.vpc.id
  region  = var.region
}

resource "google_compute_router_nat" "nat" {
  name                               = "${local.name}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# -----------------------------------------------------------------------------
# Firewall: the only allowed inbound paths
# -----------------------------------------------------------------------------

# Users -> (Google external LB) -> frontend. Nothing else is reachable from the internet.
resource "google_compute_firewall" "lb_to_frontend" {
  name          = "${local.name}-allow-lb-to-frontend"
  network       = google_compute_network.vpc.id
  direction     = "INGRESS"
  source_ranges = local.google_lb_ranges
  target_tags   = ["pixel-frontend"]
  allow {
    protocol = "tcp"
    ports    = ["80"]
  }
}

# Frontend -> API Gateway: the single internal API path
resource "google_compute_firewall" "frontend_to_gateway" {
  name        = "${local.name}-allow-frontend-to-gateway"
  network     = google_compute_network.vpc.id
  direction   = "INGRESS"
  source_tags = ["pixel-frontend"]
  target_tags = ["pixel-gateway"]
  allow {
    protocol = "tcp"
    ports    = ["8000"]
  }
}

# Health checks for the gateway's internal LB and MIG auto-healing
resource "google_compute_firewall" "healthchecks" {
  name          = "${local.name}-allow-health-checks"
  network       = google_compute_network.vpc.id
  direction     = "INGRESS"
  source_ranges = local.google_lb_ranges
  target_tags   = ["pixel-gateway", "pixel-worker", "pixel-assembler"]
  allow {
    protocol = "tcp"
    ports    = ["8000", "8080"]
  }
}

# Optional operator SSH through Identity-Aware Proxy (no public SSH)
resource "google_compute_firewall" "iap_ssh" {
  count         = var.enable_iap_ssh ? 1 : 0
  name          = "${local.name}-allow-iap-ssh"
  network       = google_compute_network.vpc.id
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["pixel-frontend", "pixel-gateway", "pixel-worker", "pixel-assembler"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
