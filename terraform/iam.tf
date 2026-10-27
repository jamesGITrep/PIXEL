# -----------------------------------------------------------------------------
# One service account per microservice, least privilege
# -----------------------------------------------------------------------------

locals {
  services = ["frontend", "gateway", "worker", "assembler"]

  # bucket key -> role, per service. The frontend has no storage access at all.
  bucket_roles = {
    gateway = {
      photo_library     = "roles/storage.objectAdmin" # vault listing/purge, upload relay
      target_posters    = "roles/storage.objectAdmin" # job targets
      task_coordination = "roles/storage.objectAdmin" # manifests, tasks, upload tickets
      mosaic_outputs    = "roles/storage.objectAdmin" # results, event state, export cache
    }
    worker = {
      photo_library     = "roles/storage.objectAdmin" # ingestion of incoming/ uploads
      target_posters    = "roles/storage.objectViewer"
      task_coordination = "roles/storage.objectAdmin" # locks, heartbeats
      mosaic_outputs    = "roles/storage.objectAdmin" # chunk outputs, renders
    }
    assembler = {
      photo_library     = "roles/storage.objectViewer" # tiles for stitching
      target_posters    = "roles/storage.objectViewer"
      task_coordination = "roles/storage.objectAdmin" # assembly locks, telemetry history
      mosaic_outputs    = "roles/storage.objectAdmin" # final artboards
    }
  }

  bucket_bindings = merge([
    for svc, roles in local.bucket_roles : {
      for bucket, role in roles : "${svc}-${bucket}" => { svc = svc, bucket = bucket, role = role }
    }
  ]...)
}

resource "google_service_account" "svc" {
  for_each     = toset(local.services)
  account_id   = "${local.name}-${each.key}"
  display_name = "PixelMosaic ${each.key} service"
  depends_on   = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "svc" {
  for_each = local.bucket_bindings
  bucket   = google_storage_bucket.buckets[each.value.bucket].name
  role     = each.value.role
  member   = "serviceAccount:${google_service_account.svc[each.value.svc].email}"
}

resource "google_artifact_registry_repository_iam_member" "pull" {
  for_each   = toset(local.services)
  location   = google_artifact_registry_repository.pixel_repo.location
  repository = google_artifact_registry_repository.pixel_repo.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.svc[each.key].email}"
}

resource "google_project_iam_member" "logs" {
  for_each = toset(local.services)
  project  = var.project_id
  role     = "roles/logging.logWriter"
  member   = "serviceAccount:${google_service_account.svc[each.key].email}"
}

resource "google_project_iam_member" "metrics" {
  for_each = toset(local.services)
  project  = var.project_id
  role     = "roles/monitoring.metricWriter"
  member   = "serviceAccount:${google_service_account.svc[each.key].email}"
}

# -----------------------------------------------------------------------------
# Cloud Build: builds the four images from source straight into Artifact Registry
# -----------------------------------------------------------------------------

resource "google_service_account" "build" {
  account_id   = "${local.name}-build"
  display_name = "PixelMosaic image builds"
  depends_on   = [google_project_service.apis]
}

# Private bucket for uploaded build sources (bucket-only, like the rest of the platform)
resource "google_storage_bucket" "build_staging" {
  name                        = "${var.project_id}-pixel-build-staging"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  lifecycle_rule {
    condition { age = 7 }
    action { type = "Delete" }
  }
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "build_source" {
  bucket = google_storage_bucket.build_staging.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.build.email}"
}

resource "google_artifact_registry_repository_iam_member" "build_push" {
  location   = google_artifact_registry_repository.pixel_repo.location
  repository = google_artifact_registry_repository.pixel_repo.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.build.email}"
}

resource "google_project_iam_member" "build_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.build.email}"
}
