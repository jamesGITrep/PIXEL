output "load_balancer_ip" {
  description = "External IP address of the Cloud Load Balancer"
  value       = google_compute_global_forwarding_rule.forwarding_rule.ip_address
}

output "artifact_registry_repo" {
  description = "Artifact Registry Docker repository endpoint"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.pixel_repo.repository_id}"
}

output "bucket_names" {
  description = "GCS Bucket names created for the storage plane"
  value = {
    photo_library     = google_storage_bucket.photo_library.name
    target_posters    = google_storage_bucket.target_posters.name
    task_coordination = google_storage_bucket.task_coordination.name
    mosaic_outputs    = google_storage_bucket.mosaic_outputs.name
  }
}

output "mig_name" {
  description = "Managed Instance Group name for CPU raster workers"
  value       = google_compute_region_instance_group_manager.worker_mig.name
}

output "autoscaler_target_cpu" {
  description = "Autoscaler target CPU load for Spot VMs"
  value       = var.worker_target_cpu_utilization
}
