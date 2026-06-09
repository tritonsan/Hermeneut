param(
  [string]$ProjectId = "hermeneut"
)

$ErrorActionPreference = "Stop"
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = "1"

Write-Host "Project:"
gcloud projects describe $ProjectId --format "table(projectId,projectNumber,lifecycleState,name)"

Write-Host "`nBilling:"
gcloud billing projects describe $ProjectId --format "table(billingEnabled,billingAccountName)"

Write-Host "`nEnabled core services:"
$allEnabledServices = gcloud services list `
  --project $ProjectId `
  --enabled `
  --format "value(config.name)"

$coreServices = @(
  "run.googleapis.com",
  "artifactregistry.googleapis.com",
  "cloudbuild.googleapis.com",
  "secretmanager.googleapis.com",
  "storage.googleapis.com",
  "aiplatform.googleapis.com",
  "discoveryengine.googleapis.com",
  "documentai.googleapis.com"
)

$enabledCoreServices = $allEnabledServices | Where-Object { $coreServices -contains $_ }
if ($enabledCoreServices) {
  $enabledCoreServices | ForEach-Object { Write-Host $_ }
} else {
  Write-Host "No Hermeneut core services enabled yet."
}

Write-Host "`nCloud Run services:"
if ($enabledCoreServices -contains "run.googleapis.com") {
  gcloud run services list --project $ProjectId --format "table(metadata.name,metadata.labels.location,status.url)" 2>$null
} else {
  Write-Host "run.googleapis.com is not enabled yet. Run setup-project.ps1 before deployment."
}
