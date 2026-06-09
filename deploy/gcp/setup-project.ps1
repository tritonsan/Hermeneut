param(
  [string]$ProjectId = "hermeneut",
  [string]$Region = "europe-west4",
  [string]$Bucket = "hermeneut-sources"
)

$ErrorActionPreference = "Stop"
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = "1"

Write-Host "Using Google Cloud project: $ProjectId"
gcloud config set project $ProjectId | Out-Null

Write-Host "Enabling required APIs..."
$services = @(
  "run.googleapis.com",
  "artifactregistry.googleapis.com",
  "cloudbuild.googleapis.com",
  "secretmanager.googleapis.com",
  "storage.googleapis.com",
  "aiplatform.googleapis.com",
  "discoveryengine.googleapis.com",
  "documentai.googleapis.com",
  "logging.googleapis.com",
  "monitoring.googleapis.com"
)

foreach ($service in $services) {
  gcloud services enable $service --project $ProjectId
}

Write-Host "Creating Artifact Registry repository if needed..."
$repoExists = gcloud artifacts repositories list `
  --location $Region `
  --project $ProjectId `
  --filter "name~'/hermeneut$'" `
  --format "value(name)"
if (-not $repoExists) {
  gcloud artifacts repositories create hermeneut `
    --repository-format docker `
    --location $Region `
    --description "Hermeneut container images" `
    --project $ProjectId
}

Write-Host "Creating GCS bucket if needed..."
$bucketExists = gcloud storage buckets list `
  --project $ProjectId `
  --filter "name=$Bucket" `
  --format "value(name)"
if (-not $bucketExists) {
  gcloud storage buckets create "gs://$Bucket" `
    --project $ProjectId `
    --location $Region `
    --uniform-bucket-level-access
}

Write-Host "Creating placeholder secrets if needed..."
$secrets = @(
  "ELASTICSEARCH_URL",
  "ELASTICSEARCH_API_KEY",
  "ELASTIC_MCP_ENDPOINT",
  "ELASTIC_MCP_API_KEY",
  "AGENT_BUILDER_AGENT_ID",
  "OPENAI_PROXY_API_KEY",
  "MCP_PROXY_TOKEN",
  "ADMIN_API_TOKEN",
  "JURY_PROXY_TOKEN",
  "JURY_ACCESS_CODE_HASH",
  "GOOGLE_SERVICE_ACCOUNT_JSON"
)

foreach ($secret in $secrets) {
  $exists = gcloud secrets list `
    --project $ProjectId `
    --filter "name~'/secrets/$secret$'" `
    --format "value(name)"
  if (-not $exists) {
    "placeholder" | gcloud secrets create $secret --project $ProjectId --data-file=-
  }
}

Write-Host "Granting Cloud Run service account access to secrets and storage..."
$projectNumber = gcloud projects describe $ProjectId --format "value(projectNumber)"
$cloudRunServiceAccount = "$projectNumber-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding $ProjectId `
  --member "serviceAccount:$cloudRunServiceAccount" `
  --role "roles/secretmanager.secretAccessor" `
  --quiet | Out-Null

gcloud projects add-iam-policy-binding $ProjectId `
  --member "serviceAccount:$cloudRunServiceAccount" `
  --role "roles/storage.objectAdmin" `
  --quiet | Out-Null

Write-Host "Setup complete. Replace placeholder secret versions before production use."
