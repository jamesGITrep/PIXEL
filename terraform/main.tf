terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.25.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# -----------------------------------------------------------------------------
# 1. GOOGLE CLOUD STORAGE BUCKETS (Bucket-Only Storage Architecture)
# -----------------------------------------------------------------------------

resource "google_storage_bucket" "photo_library" {
  name                        = "${var.project_id}-${var.bucket_names["photo_library"]}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  cors {
    origin          = ["*"]
    method          = ["GET", "POST", "PUT", "HEAD", "OPTIONS"]
    response_header = ["*"]
    max_age_seconds = 3600
  }
}

resource "google_storage_bucket" "target_posters" {
  name                        = "${var.project_id}-${var.bucket_names["target_posters"]}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  cors {
    origin          = ["*"]
    method          = ["GET", "POST", "PUT", "HEAD", "OPTIONS"]
    response_header = ["*"]
    max_age_seconds = 3600
  }
}

resource "google_storage_bucket" "task_coordination" {
  name                        = "${var.project_id}-${var.bucket_names["task_coordination"]}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  cors {
    origin          = ["*"]
    method          = ["GET", "POST", "PUT", "HEAD", "OPTIONS"]
    response_header = ["*"]
    max_age_seconds = 3600
  }
}

resource "google_storage_bucket" "mosaic_outputs" {
  name                        = "${var.project_id}-${var.bucket_names["mosaic_outputs"]}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  cors {
    origin          = ["*"]
    method          = ["GET", "POST", "PUT", "HEAD", "OPTIONS"]
    response_header = ["*"]
    max_age_seconds = 3600
  }
}

# -----------------------------------------------------------------------------
# 2. ARTIFACT REGISTRY FOR CONTAINER IMAGES
# -----------------------------------------------------------------------------

resource "google_artifact_registry_repository" "pixel_repo" {
  location      = var.region
  repository_id = "pixel-services"
  description   = "Docker repository for Gateway, Worker, Assembler, and Frontend container images"
  format        = "DOCKER"
}

# -----------------------------------------------------------------------------
# 3. IAM & SERVICE ACCOUNTS
# -----------------------------------------------------------------------------

resource "google_service_account" "worker_sa" {
  account_id   = "pixel-worker-sa"
  display_name = "Pixel Mosaic Worker Service Account"
}

resource "google_project_iam_member" "worker_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.worker_sa.email}"
}

resource "google_project_iam_member" "worker_monitoring" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.worker_sa.email}"
}

# -----------------------------------------------------------------------------
# 4. COMPUTE ENGINE MANAGED INSTANCE GROUP (MIG) WITH SPOT VMS (CPU-Only)
# -----------------------------------------------------------------------------

resource "google_compute_instance_template" "worker_template" {
  name_prefix  = "pixel-worker-template-"
  machine_type = var.worker_machine_type
  region       = var.region

  scheduling {
    preemptible        = true
    provisioning_model = "SPOT"
    automatic_restart  = false
  }

  disk {
    source_image = "cos-cloud/cos-stable"
    auto_delete  = true
    boot         = true
    disk_type    = "pd-balanced"
    disk_size_gb = 30
  }

  network_interface {
    network = "default"
    access_config {
      // Ephemeral IP for public internet egress to GCS
    }
  }

  service_account {
    email  = google_service_account.worker_sa.email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = <<-EOT
    #!/bin/bash
    docker run -d \
      --restart=always \
      -e USE_GCP=true \
      -e BUCKET_PHOTO_LIBRARY=${google_storage_bucket.photo_library.name} \
      -e BUCKET_TARGET_POSTERS=${google_storage_bucket.target_posters.name} \
      -e BUCKET_TASK_COORDINATION=${google_storage_bucket.task_coordination.name} \
      -e BUCKET_MOSAIC_OUTPUTS=${google_storage_bucket.mosaic_outputs.name} \
      ${var.region}-docker.pkg.dev/${var.project_id}/pixel-services/worker:latest
  EOT

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_region_instance_group_manager" "worker_mig" {
  name               = "pixel-worker-mig"
  base_instance_name = "pixel-worker"
  region             = var.region
  target_size        = var.worker_min_replicas

  version {
    instance_template = google_compute_instance_template.worker_template.id
  }

  named_port {
    name = "health"
    port = 8000
  }

  auto_healing_policies {
    health_check      = google_compute_health_check.gateway_health.id
    initial_delay_sec = 120
  }
}

resource "google_compute_region_autoscaler" "worker_autoscaler" {
  name   = "pixel-worker-autoscaler"
  region = var.region
  target = google_compute_region_instance_group_manager.worker_mig.id

  autoscaling_policy {
    max_replicas    = var.worker_max_replicas
    min_replicas    = var.worker_min_replicas
    cooldown_period = 60

    cpu_utilization {
      target = var.worker_target_cpu_utilization
    }
  }
}

# -----------------------------------------------------------------------------
# 5. EXTERNAL APPLICATION LOAD BALANCER & HTTP HEALTH CHECKS
# -----------------------------------------------------------------------------

resource "google_compute_health_check" "gateway_health" {
  name               = "pixel-gateway-health-check"
  check_interval_sec = 5
  timeout_sec        = 3
  healthy_threshold   = 2
  unhealthy_threshold = 3

  http_health_check {
    request_path = "/health"
    port         = 8000
  }
}

resource "google_compute_backend_service" "gateway_backend" {
  name                  = "pixel-gateway-backend"
  protocol              = "HTTP"
  port_name             = "health"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = 30
  health_checks         = [google_compute_health_check.gateway_health.id]
}

resource "google_compute_backend_service" "frontend_backend" {
  name                  = "pixel-frontend-backend"
  protocol              = "HTTP"
  port_name             = "http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = 30
  health_checks         = [google_compute_health_check.gateway_health.id]
}

resource "google_compute_url_map" "url_map" {
  name            = "pixel-lb-url-map"
  default_service = google_compute_backend_service.frontend_backend.id

  host_rule {
    hosts        = ["*"]
    path_matcher = "allpaths"
  }

  path_matcher {
    name            = "allpaths"
    default_service = google_compute_backend_service.frontend_backend.id

    path_rule {
      paths   = ["/api/*", "/health"]
      service = google_compute_backend_service.gateway_backend.id
    }
  }
}

resource "google_compute_target_http_proxy" "http_proxy" {
  name    = "pixel-http-proxy"
  url_map = google_compute_url_map.url_map.id
}

resource "google_compute_global_forwarding_rule" "forwarding_rule" {
  name                  = "pixel-global-forwarding-rule"
  ip_protocol           = "TCP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  port_range            = "80"
  target                = google_compute_target_http_proxy.http_proxy.id
}
