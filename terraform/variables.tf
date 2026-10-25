variable "project_id" {
  description = "The GCP Project ID to deploy the collaborative art platform into"
  type        = string
  default     = "pixelmosaic-prod"
}

variable "region" {
  description = "GCP Region for regional resources"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP Zone for compute resources"
  type        = string
  default     = "us-central1-a"
}

variable "environment" {
  description = "Environment tag (staging / production)"
  type        = string
  default     = "production"
}

variable "bucket_names" {
  description = "The 4 required bucket keys for the bucket-only data plane"
  type        = map(string)
  default = {
    photo_library     = "pixel-photo-library"
    target_posters    = "pixel-target-posters"
    task_coordination = "pixel-task-coordination"
    mosaic_outputs    = "pixel-mosaic-outputs"
  }
}

variable "worker_min_replicas" {
  description = "Minimum number of Spot VM worker instances in the Managed Instance Group"
  type        = number
  default     = 2
}

variable "worker_max_replicas" {
  description = "Maximum number of Spot VM worker instances for peak workloads"
  type        = number
  default     = 20
}

variable "worker_target_cpu_utilization" {
  description = "Target CPU utilization metric for autoscaling Spot worker instances (0.75 = 75%)"
  type        = number
  default     = 0.75
}

variable "worker_machine_type" {
  description = "Compute Engine machine type for CPU raster workers (No GPUs)"
  type        = string
  default     = "e2-standard-4"
}
