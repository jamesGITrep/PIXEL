# -----------------------------------------------------------------------------
# One instance template + regional Managed Instance Group + autoscaler per microservice.
# Every group: autoscaling 1..7 VMs on CPU, auto-healing on its health check, rolling updates.
# -----------------------------------------------------------------------------

locals {
  gateway_url = "http://${google_compute_address.gateway_ilb.address}:8000"

  vm = {
    frontend = {
      spot        = false
      ports       = ["80:3000"]
      health_port = 80
      health_path = "/healthz"
      named_port  = { name = "http", port = 80 }
      env = {
        FRONTEND_PORT   = "3000"
        API_GATEWAY_URL = local.gateway_url
        MAX_UPLOAD_MB   = tostring(var.max_upload_mb + 2)
      }
    }
    gateway = {
      spot        = false
      ports       = ["8000:8000"]
      health_port = 8000
      health_path = "/health"
      named_port  = { name = "api", port = 8000 }
      env = merge(local.bucket_env, {
        MAX_UPLOAD_MB   = tostring(var.max_upload_mb)
        ADMIN_USERNAMES = join(",", var.admin_usernames)
      })
    }
    worker = {
      spot        = true # Spot VMs: preemption is handled by lock leases + SIGTERM shutdown
      ports       = ["8080:8080"]
      health_port = 8080
      health_path = "/health"
      named_port  = null
      env         = merge(local.bucket_env, { HEALTH_PORT = "8080" })
    }
    assembler = {
      spot        = false
      ports       = ["8080:8080"]
      health_port = 8080
      health_path = "/health"
      named_port  = null
      env         = merge(local.bucket_env, { HEALTH_PORT = "8080" })
    }
  }
}

resource "google_compute_instance_template" "svc" {
  for_each     = local.vm
  name_prefix  = "${local.name}-${each.key}-"
  machine_type = var.machine_types[each.key]
  region       = var.region
  tags         = ["pixel-${each.key}"]
  labels       = { app = "pixelmosaic", service = each.key, environment = var.environment }

  disk {
    source_image = "cos-cloud/cos-stable"
    auto_delete  = true
    boot         = true
    disk_type    = "pd-standard"
    disk_size_gb = var.boot_disk_gb
  }

  network_interface {
    subnetwork = google_compute_subnetwork.services.id
    # no access_config: VMs have private IPs only
  }

  scheduling {
    provisioning_model          = each.value.spot ? "SPOT" : "STANDARD"
    preemptible                 = each.value.spot
    automatic_restart           = !each.value.spot
    on_host_maintenance         = each.value.spot ? "TERMINATE" : "MIGRATE"
    # Spot termination action is left at the default: the MIG itself recreates preempted VMs
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  service_account {
    email  = google_service_account.svc[each.key].email
    scopes = ["cloud-platform"] # actual permissions come from the IAM bindings in iam.tf
  }

  metadata = {
    google-logging-enabled = "true"
    startup-script = templatefile("${path.module}/startup.sh.tftpl", {
      service       = each.key
      image         = "${local.image_prefix}/${each.key}:${var.image_tag}"
      registry_host = local.registry_host
      ports         = each.value.ports
      env           = each.value.env
    })
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Auto-healing: a VM whose service stops answering is recreated
resource "google_compute_health_check" "autoheal" {
  for_each            = local.vm
  name                = "${local.name}-${each.key}-autoheal"
  check_interval_sec  = 10
  timeout_sec         = 5
  healthy_threshold   = 2
  unhealthy_threshold = 6
  http_health_check {
    port         = each.value.health_port
    request_path = each.value.health_path
  }
}

resource "google_compute_region_instance_group_manager" "svc" {
  for_each           = local.vm
  name               = "${local.name}-${each.key}"
  base_instance_name = "${local.name}-${each.key}"
  region             = var.region
  # target_size is managed by the autoscaler below

  version {
    instance_template = google_compute_instance_template.svc[each.key].id
  }

  dynamic "named_port" {
    for_each = each.value.named_port == null ? [] : [each.value.named_port]
    content {
      name = named_port.value.name
      port = named_port.value.port
    }
  }

  auto_healing_policies {
    health_check      = google_compute_health_check.autoheal[each.key].id
    initial_delay_sec = 300 # image pull + container start on first boot
  }

  update_policy {
    type                         = "PROACTIVE"
    minimal_action               = "REPLACE"
    instance_redistribution_type = "PROACTIVE"
    max_surge_fixed              = 3 # regional MIGs need >= number of zones
    max_unavailable_fixed        = 0
  }
}

resource "google_compute_region_autoscaler" "svc" {
  for_each = local.vm
  name     = "${local.name}-${each.key}-autoscaler"
  region   = var.region
  target   = google_compute_region_instance_group_manager.svc[each.key].id

  autoscaling_policy {
    min_replicas    = var.autoscaling[each.key].min
    max_replicas    = var.autoscaling[each.key].max
    cooldown_period = 120
    cpu_utilization {
      target = var.autoscaling[each.key].cpu_target
    }
  }
}
