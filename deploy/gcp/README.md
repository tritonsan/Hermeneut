# Google Cloud Deployment

This folder contains optional Google Cloud deployment helpers for Hermeneut.

The scripts deploy:

- a FastAPI Cloud Run service
- a Next.js Cloud Run service
- a Cloud Run Job for OCR/source processing
- an Artifact Registry Docker repository
- a GCS source vault bucket
- Secret Manager placeholders for runtime credentials

Run from the repository root in PowerShell:

```powershell
.\deploy\gcp\setup-project.ps1 -ProjectId "<your-project-id>" -Region "<region>" -Bucket "<gcs-bucket>"
.\deploy\gcp\deploy-api.ps1 -ProjectId "<your-project-id>" -Region "<region>" -Bucket "<gcs-bucket>"
.\deploy\gcp\deploy-web.ps1 -ProjectId "<your-project-id>" -Region "<region>"
```

Before deploying a production-like demo, replace placeholder Secret Manager values for Elastic, Gemini/Google Cloud, MCP, admin access, and jury access. Do not commit real secret values.

For hackathon judging, the web app can expose public read-only browsing while `/jury?code=...` enables the controlled demo workflows.
