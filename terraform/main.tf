# =============================================================================
# PixelMosaic - GCP production plane
#
#   Internet ──► External Application LB ──► frontend MIG (nginx, :80)       public edge
#                                                  │ /api/*
#                                                  ▼
#                             Internal TCP LB ──► gateway MIG (:8000)          single API Gateway
#                                                  │
#                    private GCS buckets ◄─────────┼──────────┐
#                           ▲                      │          │
#                  worker MIG (Spot, autoscaled)   assembler MIG (size 1)      no inbound traffic
#
# - Every microservice runs on its own VMs (separate instance templates + MIGs).
# - No VM has an external IP; egress uses Cloud NAT / Private Google Access.
# - Firewall: only Google's LB ranges reach the frontend; only frontend VMs (and health checks)
#   reach the gateway; workers/assembler accept health checks only.
# - Buckets are private (public access prevention enforced); each service account gets only
#   the bucket roles it needs.
# =============================================================================

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.25"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

locals {
  registry_host = "${var.region}-docker.pkg.dev"
  image_prefix  = "${local.registry_host}/${var.project_id}/${google_artifact_registry_repository.pixel_repo.repository_id}"
  name          = var.name_prefix

  bucket_env = {
    USE_GCP                  = "true"
    GCP_PROJECT_ID           = var.project_id
    BUCKET_PHOTO_LIBRARY     = google_storage_bucket.buckets["photo_library"].name
    BUCKET_TARGET_POSTERS    = google_storage_bucket.buckets["target_posters"].name
    BUCKET_TASK_COORDINATION = google_storage_bucket.buckets["task_coordination"].name
    BUCKET_MOSAIC_OUTPUTS    = google_storage_bucket.buckets["mosaic_outputs"].name
  }

  # Google Front End / health-check source ranges
  google_lb_ranges = ["130.211.0.0/22", "35.191.0.0/16"]
}

# -----------------------------------------------------------------------------
# Required APIs
# -----------------------------------------------------------------------------

resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "storage.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudbuild.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# -----------------------------------------------------------------------------
# Storage: the bucket-only data plane (private)
# -----------------------------------------------------------------------------

resource "google_storage_bucket" "buckets" {
  for_each                    = var.bucket_names
  name                        = "${var.project_id}-${each.value}"
  location                    = var.region
  force_destroy               = var.force_destroy_buckets
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Job state, upload tickets, ingest locks and telemetry are short-lived. User accounts
  # (users/, user-emails/) and the session signing key (auth/) live here too and never expire.
  dynamic "lifecycle_rule" {
    for_each = each.key == "task_coordination" ? [1] : []
    content {
      condition {
        age            = var.task_state_retention_days
        matches_prefix = ["jobs/", "upload-tickets/", "ingest-locks/", "telemetry/", "login-failures/"]
      }
      action { type = "Delete" }
    }
  }
  dynamic "lifecycle_rule" {
    for_each = each.key == "photo_library" ? [1] : []
    content {
      condition {
        age            = 2
        matches_prefix = ["incoming/"]
      }
      action { type = "Delete" }
    }
  }
  # Guest vaults (visitors who never signed in) are temporary: deleted a day after upload.
  # Signing in moves a guest's photos into their account before that.
  dynamic "lifecycle_rule" {
    for_each = each.key == "photo_library" ? [1] : []
    content {
      condition {
        age            = 1
        matches_prefix = ["personal/gst_", "incoming/personal/gst_"]
      }
      action { type = "Delete" }
    }
  }

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Artifact Registry for the four service images
# -----------------------------------------------------------------------------

resource "google_artifact_registry_repository" "pixel_repo" {
  location      = var.region
  repository_id = "pixel-services"
  description   = "Images for the frontend, gateway, worker and assembler microservices"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}
