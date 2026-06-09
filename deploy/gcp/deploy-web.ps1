param(
  [string]$ProjectId = "hermeneut",
  [string]$Region = "europe-west4",
  [string]$Service = "hermeneut-web",
  [string]$ApiService = "hermeneut-api"
)

$ErrorActionPreference = "Stop"
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = "1"
$Image = "$Region-docker.pkg.dev/$ProjectId/hermeneut/web:latest"

gcloud config set project $ProjectId | Out-Null

$ApiUrl = gcloud run services describe $ApiService `
  --project $ProjectId `
  --region $Region `
  --format "value(status.url)"

if (-not $ApiUrl) {
  throw "API service URL not found. Deploy the API first."
}

$WebUrl = gcloud run services describe $Service `
  --project $ProjectId `
  --region $Region `
  --format "value(status.url)" 2>$null

$WebBaseEnv = ""
if ($WebUrl) {
  $WebBaseEnv = ",HERMENEUT_WEB_BASE_URL=$WebUrl"
}

Write-Host "Building web image: $Image"
gcloud builds submit . `
  --project $ProjectId `
  --config deploy/gcp/cloudbuild-web.yaml `
  --substitutions "_IMAGE=$Image"
if ($LASTEXITCODE -ne 0) { throw "Web image build failed." }

Write-Host "Deploying web to Cloud Run..."
gcloud run deploy $Service `
  --project $ProjectId `
  --region $Region `
  --image $Image `
  --platform managed `
  --allow-unauthenticated `
  --memory 512Mi `
  --cpu 1 `
  --min-instances 0 `
  --max-instances 3 `
  --set-env-vars "NEXT_PUBLIC_API_BASE_URL=$ApiUrl,HERMENEUT_API_BASE_URL=$ApiUrl,JURY_ACCESS_ENABLED=true,JURY_SESSION_MAX_AGE_SECONDS=172800$WebBaseEnv" `
  --set-secrets "JURY_PROXY_TOKEN=JURY_PROXY_TOKEN:latest,JURY_ACCESS_CODE_HASH=JURY_ACCESS_CODE_HASH:latest"
if ($LASTEXITCODE -ne 0) { throw "Web Cloud Run deploy failed." }

gcloud run services describe $Service `
  --project $ProjectId `
  --region $Region `
  --format "value(status.url)"
