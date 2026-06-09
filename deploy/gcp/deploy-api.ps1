param(
  [string]$ProjectId = "hermeneut",
  [string]$Region = "europe-west4",
  [string]$Service = "hermeneut-api",
  [string]$Bucket = "hermeneut-sources",
  [string]$JobName = "hermeneut-ocr-worker"
)

$ErrorActionPreference = "Stop"
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = "1"
$Image = "$Region-docker.pkg.dev/$ProjectId/hermeneut/api:latest"
$LimitEnvVars = "SOURCE_DOWNLOAD_MAX_BYTES=20000000|CATALOG_RESPONSE_MAX_BYTES=2000000|LIBRARY_MULTIPART_UPLOAD_MAX_BYTES=50000000|LIBRARY_DIRECT_UPLOAD_MAX_BYTES=500000000|SHAMSIYYA_IMPORT_MAX_BYTES=150000000|OCR_PAGE_BATCH_SIZE=40|OCR_FULL_DOCUMENT_MAX_PAGES=600|SOURCE_DOWNLOAD_ALLOWED_HOSTS=openiti.org,archive.org,ia800,ia801,ia802,ia803,ia804,ia902,ia903|CATALOG_ALLOWED_HOSTS=loc.gov,worldcat.org,search.worldcat.org,hathitrust.org,catalog.hathitrust.org,archive.org,openiti.org"
$ApiEnvVars = "^|^ENVIRONMENT=production|PUBLIC_DEMO_MODE=false|JURY_ACCESS_ENABLED=true|JURY_SESSION_MAX_AGE_SECONDS=172800|GOOGLE_CLOUD_PROJECT=$ProjectId|GCS_BUCKET=$Bucket|GEMINI_MODEL=gemini-3-pro|GEMINI_RESEARCH_MODEL=google/gemini-3.1-flash-lite|GEMINI_REPORT_MODEL=google/gemini-3.1-pro-preview|GEMINI_CATALOG_MODEL=google/gemini-3.5-flash|GEMINI_CATALOG_JUDGE_MODEL=google/gemini-3.1-pro-preview|GEMINI_EMBEDDING_MODEL=gemini-embedding-001|VERTEX_OPENAI_LOCATION=global|VERTEX_OPENAI_MODEL=google/gemini-2.5-pro|JOB_BACKEND=cloud_run_jobs|RUN_EXECUTION_MODE=async|CORS_ALLOWED_ORIGINS=https://hermeneut-web-906463499709.europe-west4.run.app,http://localhost:3000|CLOUD_RUN_JOB_NAME=$JobName|CLOUD_RUN_JOB_LOCATION=$Region|CLOUD_RUN_JOB_TASK_TIMEOUT_SECONDS=14400|$LimitEnvVars"
$WorkerEnvVars = "^|^ENVIRONMENT=production|GOOGLE_CLOUD_PROJECT=$ProjectId|GCS_BUCKET=$Bucket|GEMINI_MODEL=gemini-3-pro|GEMINI_RESEARCH_MODEL=google/gemini-3.1-flash-lite|GEMINI_REPORT_MODEL=google/gemini-3.1-pro-preview|GEMINI_CATALOG_MODEL=google/gemini-3.5-flash|GEMINI_CATALOG_JUDGE_MODEL=google/gemini-3.1-pro-preview|GEMINI_EMBEDDING_MODEL=gemini-embedding-001|VERTEX_OPENAI_LOCATION=global|VERTEX_OPENAI_MODEL=google/gemini-2.5-pro|CLOUD_RUN_JOB_NAME=$JobName|CLOUD_RUN_JOB_LOCATION=$Region|CLOUD_RUN_JOB_TASK_TIMEOUT_SECONDS=14400|$LimitEnvVars"

gcloud config set project $ProjectId | Out-Null

Write-Host "Building API image: $Image"
gcloud builds submit . `
  --project $ProjectId `
  --config deploy/gcp/cloudbuild-api.yaml `
  --substitutions "_IMAGE=$Image"
if ($LASTEXITCODE -ne 0) { throw "API image build failed." }

Write-Host "Deploying API to Cloud Run..."
gcloud run deploy $Service `
  --project $ProjectId `
  --region $Region `
  --image $Image `
  --platform managed `
  --allow-unauthenticated `
  --memory 4Gi `
  --cpu 2 `
  --timeout 3600 `
  --min-instances 0 `
  --max-instances 3 `
  --set-env-vars $ApiEnvVars `
  --set-secrets "ELASTICSEARCH_URL=ELASTICSEARCH_URL:latest,ELASTICSEARCH_API_KEY=ELASTICSEARCH_API_KEY:latest,ELASTIC_MCP_ENDPOINT=ELASTIC_MCP_ENDPOINT:latest,ELASTIC_MCP_API_KEY=ELASTIC_MCP_API_KEY:latest,AGENT_BUILDER_AGENT_ID=AGENT_BUILDER_AGENT_ID:latest,OPENAI_PROXY_API_KEY=OPENAI_PROXY_API_KEY:latest,MCP_PROXY_TOKEN=MCP_PROXY_TOKEN:latest,ADMIN_API_TOKEN=ADMIN_API_TOKEN:latest,JURY_PROXY_TOKEN=JURY_PROXY_TOKEN:latest,JURY_ACCESS_CODE_HASH=JURY_ACCESS_CODE_HASH:latest,GOOGLE_SERVICE_ACCOUNT_JSON=GOOGLE_SERVICE_ACCOUNT_JSON:latest"
if ($LASTEXITCODE -ne 0) { throw "API Cloud Run deploy failed." }

Write-Host "Deploying OCR worker Cloud Run Job: $JobName"
gcloud run jobs deploy $JobName `
  --project $ProjectId `
  --region $Region `
  --image $Image `
  --memory 4Gi `
  --cpu 2 `
  --task-timeout 14400s `
  --max-retries 1 `
  --command python `
  --args="-m,app.jobs.process_source" `
  --set-env-vars $WorkerEnvVars `
  --set-secrets "ELASTICSEARCH_URL=ELASTICSEARCH_URL:latest,ELASTICSEARCH_API_KEY=ELASTICSEARCH_API_KEY:latest,ELASTIC_MCP_ENDPOINT=ELASTIC_MCP_ENDPOINT:latest,ELASTIC_MCP_API_KEY=ELASTIC_MCP_API_KEY:latest,AGENT_BUILDER_AGENT_ID=AGENT_BUILDER_AGENT_ID:latest,OPENAI_PROXY_API_KEY=OPENAI_PROXY_API_KEY:latest,MCP_PROXY_TOKEN=MCP_PROXY_TOKEN:latest,ADMIN_API_TOKEN=ADMIN_API_TOKEN:latest,GOOGLE_SERVICE_ACCOUNT_JSON=GOOGLE_SERVICE_ACCOUNT_JSON:latest"
if ($LASTEXITCODE -ne 0) { throw "OCR worker Cloud Run Job deploy failed." }

$ProjectNumber = gcloud projects describe $ProjectId --format "value(projectNumber)"
$RuntimeServiceAccount = "$ProjectNumber-compute@developer.gserviceaccount.com"
Write-Host "Ensuring API runtime service account can execute Cloud Run Jobs: $RuntimeServiceAccount"
gcloud projects add-iam-policy-binding $ProjectId `
  --member "serviceAccount:$RuntimeServiceAccount" `
  --role "roles/run.developer" | Out-Null
gcloud iam service-accounts add-iam-policy-binding $RuntimeServiceAccount `
  --project $ProjectId `
  --member "serviceAccount:$RuntimeServiceAccount" `
  --role "roles/iam.serviceAccountUser" | Out-Null

gcloud run services describe $Service `
  --project $ProjectId `
  --region $Region `
  --format "value(status.url)"
