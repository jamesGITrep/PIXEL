output "public_url" {
  description = "The only public entry point (the frontend, via the external load balancer)"
  value       = var.domain == "" ? "http://${google_compute_global_address.public.address}" : "https://${var.domain}"
}

output "public_ip" {
  description = "External load balancer IP (point your domain's A record here when using HTTPS)"
  value       = google_compute_global_address.public.address
}

output "gateway_internal_url" {
  description = "API Gateway address inside the VPC (not reachable from the internet)"
  value       = local.gateway_url
}

output "artifact_registry_repo" {
  description = "Push the four service images here"
  value       = local.image_prefix
}

output "bucket_names" {
  description = "Private GCS buckets of the data plane"
  value       = { for k, b in google_storage_bucket.buckets : k => b.name }
}

output "instance_groups" {
  description = "Managed instance group per microservice (each autoscales 1..7)"
  value       = { for k, g in google_compute_region_instance_group_manager.svc : k => g.name }
}

output "build_service_account" {
  description = "Service account Cloud Build runs as"
  value       = google_service_account.build.email
}

output "build_staging_bucket" {
  description = "Private bucket for uploaded build sources"
  value       = google_storage_bucket.build_staging.name
}
