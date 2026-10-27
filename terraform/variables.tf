variable "project_id" {
  description = "GCP project to deploy into"
  type        = string
}

variable "region" {
  description = "Region for VMs, buckets, registry and load balancers"
  type        = string
  default     = "asia-south1"
}

variable "zone" {
  description = "Zone for zonal resources (the assembler)"
  type        = string
  default     = "asia-south1-a"
}

variable "environment" {
  description = "Environment label (staging / production)"
  type        = string
  default     = "production"
}

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
  default     = "pixel"
}

variable "image_tag" {
  description = "Image tag deployed to every service; changing it triggers a rolling update"
  type        = string
  default     = "latest"
}

variable "domain" {
  description = "Optional domain for HTTPS with a Google-managed certificate (empty = HTTP only)"
  type        = string
  default     = ""
}

variable "subnet_cidr" {
  description = "CIDR of the private services subnet"
  type        = string
  default     = "10.20.0.0/24"
}

variable "enable_iap_ssh" {
  description = "Allow operator SSH via Identity-Aware Proxy (no public SSH either way)"
  type        = bool
  default     = true
}

variable "bucket_names" {
  description = "Bucket name suffixes (prefixed with the project id)"
  type        = map(string)
  default = {
    photo_library     = "pixel-photo-library"
    target_posters    = "pixel-target-posters"
    task_coordination = "pixel-task-coordination"
    mosaic_outputs    = "pixel-mosaic-outputs"
  }
}

variable "force_destroy_buckets" {
  description = "Allow `terraform destroy` to delete non-empty buckets (data loss!)"
  type        = bool
  default     = false
}

variable "task_state_retention_days" {
  description = "Days to keep task-coordination objects (manifests, tasks, locks, telemetry)"
  type        = number
  default     = 30
}

variable "max_upload_mb" {
  description = "Maximum size of one uploaded photo"
  type        = number
  default     = 30
}

# --- sizing: smallest machine that runs each service; every group autoscales 1..7 -----------

variable "machine_types" {
  description = "Machine type per microservice"
  type        = map(string)
  default = {
    frontend  = "e2-micro"  # nginx: static files + reverse proxy
    gateway   = "e2-medium" # composes tinted 12000px exports in memory (~1 GB peak)
    worker    = "e2-small"  # CPU-bound matching, small memory footprint (Spot)
    assembler = "e2-medium" # stitches up to 12000x12000 canvases (~1.5 GB peak)
  }
}

variable "autoscaling" {
  description = "Autoscaling bounds and CPU target per microservice"
  type = map(object({
    min        = number
    max        = number
    cpu_target = number
  }))
  default = {
    frontend  = { min = 1, max = 7, cpu_target = 0.6 }
    gateway   = { min = 1, max = 7, cpu_target = 0.6 }
    worker    = { min = 1, max = 7, cpu_target = 0.75 }
    assembler = { min = 1, max = 7, cpu_target = 0.7 }
  }
  validation {
    condition     = alltrue([for k, v in var.autoscaling : v.min >= 1 && v.max <= 7 && v.min <= v.max])
    error_message = "Every group must scale between 1 and 7 VMs."
  }
}

variable "boot_disk_gb" {
  description = "Boot disk per VM (Container-Optimized OS minimum is 10 GB)"
  type        = number
  default     = 10
}

variable "admin_usernames" {
  description = "Accounts allowed to moderate the community pool (usernames, lowercase)"
  type        = list(string)
  default     = []
}
