<#
.SYNOPSIS
  Deploys PixelMosaic to Google Cloud:
    1. APIs, Artifact Registry and the Cloud Build service account (Terraform)
    2. Builds the four microservice images in Cloud Build, straight into Artifact Registry
    3. Everything else (Terraform): private VPC, one autoscaled MIG per microservice (1..7 VMs),
       health checks + auto-healing, external LB to the frontend only, internal LB to the API
       Gateway, private buckets.

.PREREQUISITES
  - gcloud CLI logged in (`gcloud auth login`) as an Owner of the project, billing enabled.
    THIS CREATES BILLABLE RESOURCES.
  - Docker Desktop running (only to run the Terraform container when Terraform isn't installed).

.EXAMPLES
  .\scripts\deploy_gcp.ps1 -ProjectId my-project                 # build + deploy
  .\scripts\deploy_gcp.ps1 -ProjectId my-project -PlanOnly       # show what would be created
  .\scripts\deploy_gcp.ps1 -ProjectId my-project -Domain mosaic.example.com
  .\scripts\deploy_gcp.ps1 -ProjectId my-project -Destroy        # tear everything down
#>
param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [string]$Region = "asia-south1",
  [string]$Zone = "asia-south1-a",
  [string]$Tag = (Get-Date -Format "yyyyMMdd-HHmmss"),
  [string]$Domain = "",
  [switch]$PlanOnly,
  [switch]$Destroy
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$TfDir = Join-Path $Root "terraform"
$Repo = "$Region-docker.pkg.dev/$ProjectId/pixel-services"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Check($what) { if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit $LASTEXITCODE)" } }

# --- Terraform: local binary if installed, otherwise the official container.
# Authenticates with the current gcloud login (short-lived access token), so no
# application-default credentials or key files are needed.
function Tf {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$TfArgs)
  $token = (gcloud auth print-access-token 2>$null)
  if (-not $token) { Fail "gcloud is not logged in. Run: gcloud auth login" }
  if (Get-Command terraform -ErrorAction SilentlyContinue) {
    $env:GOOGLE_OAUTH_ACCESS_TOKEN = $token
    Push-Location $TfDir; try { & terraform @TfArgs } finally { Pop-Location }
  } else {
    & docker run --rm -i -v "${TfDir}:/tf" -w /tf -e GOOGLE_OAUTH_ACCESS_TOKEN=$token `
      hashicorp/terraform:1.9.8 @TfArgs
  }
}
$TfVars = @("-var", "project_id=$ProjectId", "-var", "region=$Region", "-var", "zone=$Zone",
            "-var", "image_tag=$Tag", "-var", "domain=$Domain")

# --- Preflight -----------------------------------------------------------------------------
Step "Preflight checks"
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) { Fail "gcloud CLI not found" }
gcloud auth print-access-token *> $null
if ($LASTEXITCODE -ne 0) { Fail "gcloud is not logged in. Run: gcloud auth login" }
if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
  docker info *> $null; Check "docker info (Docker Desktop must run the Terraform container)"
}
gcloud config set project $ProjectId *> $null; Check "gcloud config set project"
Write-Host "Project: $ProjectId   Region: $Region   Image tag: $Tag"

Step "terraform init"
Tf init "-input=false" "-upgrade"; Check "terraform init"

if ($Destroy) {
  Step "Destroying all PixelMosaic resources in $ProjectId"
  Write-Host "Data buckets are kept unless empty (set force_destroy_buckets=true to delete data)." -ForegroundColor Yellow
  Tf destroy "-input=false" "-auto-approve" @TfVars; Check "terraform destroy"
  exit 0
}

if ($PlanOnly) {
  Step "terraform plan (nothing will be created)"
  Tf plan "-input=false" @TfVars; Check "terraform plan"
  exit 0
}

# --- 1. Registry + build identity first, so images exist before VMs are created -------------
Step "Creating APIs, Artifact Registry and the Cloud Build service account"
Tf apply "-input=false" "-auto-approve" @TfVars `
  "-target=google_project_service.apis" `
  "-target=google_artifact_registry_repository_iam_member.build_push" `
  "-target=google_storage_bucket_iam_member.build_source" `
  "-target=google_project_iam_member.build_logs"
Check "terraform apply (registry + build identity)"
$BuildSa = (Tf output "-raw" build_service_account)
$Staging = (Tf output "-raw" build_staging_bucket)

# --- 2. Build the four images in Cloud Build ------------------------------------------------
Step "Building images in Cloud Build -> $Repo (tag $Tag)"
Push-Location $Root
try {
  gcloud builds submit . --project $ProjectId --config cloudbuild.yaml `
    --substitutions "_REPO=$Repo,_TAG=$Tag" `
    --service-account "projects/$ProjectId/serviceAccounts/$BuildSa" `
    --gcs-source-staging-dir "gs://$Staging/source"
  Check "Cloud Build"
} finally { Pop-Location }

# --- 3. Everything else (VPC, MIGs, autoscalers, health checks, load balancers, IAM) -------
Step "Applying infrastructure (rolling update to tag $Tag)"
Tf apply "-input=false" "-auto-approve" @TfVars; Check "terraform apply"

$PublicUrl = (Tf output "-raw" public_url)

# --- 4. Wait for the public endpoint --------------------------------------------------------
Step "Waiting for $PublicUrl (new load balancers and VMs take 5-10 minutes)"
$deadline = (Get-Date).AddMinutes(20)
while ((Get-Date) -lt $deadline) {
  try {
    $r = Invoke-WebRequest -Uri "$PublicUrl/api/v1/library/community/stats" -UseBasicParsing -TimeoutSec 10
    if ($r.StatusCode -eq 200) { Write-Host "`nPixelMosaic is live: $PublicUrl" -ForegroundColor Green; exit 0 }
  } catch { }
  Write-Host -NoNewline "."
  Start-Sleep -Seconds 20
}
Write-Host "`nDeployed, but $PublicUrl is not answering yet. Check: gcloud compute instance-groups managed list" -ForegroundColor Yellow
