# Hermeneut

Hermeneut is an evidence-first AI research agent for tracing difficult references, quotations, and attribution chains in classical texts. The hackathon demo focuses on classical Arabic logic and commentary literature, but the architecture is designed for libraries, archives, and research centers with large OCRed textual collections.

Hermeneut is not a chatbot that answers from memory. It searches curated evidence, shows exactly where a phrase was found, separates source leads from textual proof, and keeps a human reviewer in the loop for OCR and catalog decisions.

## What It Does

- **Library Mode:** searches only curated, OCR/indexed sources inside a selected library.
- **Open Discovery:** uses controlled discovery hints to find possible containing-layer and citation-chain sources, then promotes claims only when indexed text supports them.
- **Evidence Verdict:** shows the candidate, decision tier, strongest quote, confidence, citation hint, and verification status.
- **Location First Evidence:** displays author, work, source, page/reference label, and neighboring passage context.
- **Relationship Graph:** represents commentary, gloss, witness, and source relationships between works.
- **OCR Editor:** shows a PDF page beside OCR text, supports Gemini-assisted audit suggestions, and keeps human approval before correction.
- **Catalog Proposal Workflow:** uses Gemini to propose metadata and relationship improvements without making them canonical until reviewed.

## Demo Scenario

The recorded demo uses this Shamsiyya phrase:

```text
دفع لما يترا أي من أن الشرطية المذكورة بقوله لما نبه مستدركة
```

In **Library Mode**, Hermeneut searches the curated Shamsiyya library and finds the phrase in Abd al-Hakim al-Siyalkuti's Hashiya. The result includes the exact quote, author, work, source, location label, citation hint, and anchored verification.

In **Open Discovery**, the same phrase is tested outside the curated library. Hermeneut searches likely source layers and citation-chain candidates, processes selected sources, and then checks whether OCR/indexed text supports the phrase. If the discovered sources are only weak leads or OCR is not reliable enough, Hermeneut keeps the result as a weak lead instead of overclaiming.

## Architecture

```text
User Interface
  -> Next.js / React research workspace
  -> FastAPI agent API
  -> Gemini planning, reasoning, catalog review, and OCR audit
  -> Elastic / Elasticsearch for retrieval, context, graph, memory, and run snapshots
  -> Elastic MCP / Agent Builder tools for passage lookup, source lookup, graph lookup, and evidence memory
  -> Google Cloud Storage document vault
  -> Google Cloud Vision OCR for uploaded PDF sources
  -> Cloud Run Jobs for durable OCR/source processing
```

### Google Cloud Products

- Google Gemini
- Google Cloud Run
- Google Cloud Run Jobs
- Google Cloud Storage
- Google Cloud Vision OCR
- Google Cloud Build
- Google Artifact Registry
- Google Secret Manager
- Vertex AI / OpenAI-compatible Gemini endpoint

### Elastic Usage

Elastic is the product's context layer, not just a search box:

- indexed passages for lexical/hybrid retrieval
- source, work, and author metadata
- relationship graph between works and witnesses
- evidence memory for prior retrieval decisions
- durable run snapshots for restore/polling
- MCP/Agent Builder tool surface for agent-accessible search and graph queries

## Public And Jury Access

The hosted app is intentionally configured for hackathon judging:

- The **public link** shows the UI and read-only library browsing.
- The **jury access link** enables search, Open Discovery, OCR Editor, Catalog Curator, and relationship analysis.
- Jury access uses a short access code that creates an HttpOnly browser session. The browser does not receive the admin token.
- This is a demo access model for judging, not a production multi-user account system.

The public repository does not include demo access codes, admin tokens, Elastic keys, Google service account files, backups, raw corpus files, or internal runbooks.

## Local Development

### API

```bash
cd apps/api
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Web

```bash
npm install
npm --workspace apps/web run dev
```

Open `http://localhost:3000`.

Without Elastic credentials, the app can render a limited backup/seed preview for UI development. Full research runs, source processing, and catalog mutations require Live Elastic plus the relevant Google Cloud credentials.

## Environment Variables

Copy `.env.example` to `.env` and fill only the credentials you need. Real values should live in local environment files or Secret Manager, never in the repository.

Important variables:

```text
ELASTICSEARCH_URL=
ELASTICSEARCH_API_KEY=
ELASTIC_MCP_ENDPOINT=
ELASTIC_MCP_API_KEY=
GOOGLE_CLOUD_PROJECT=
GCS_BUCKET=
GOOGLE_SERVICE_ACCOUNT_JSON=
AGENT_BUILDER_AGENT_ID=
GEMINI_RESEARCH_MODEL=
GEMINI_REPORT_MODEL=
GEMINI_EMBEDDING_MODEL=
ADMIN_API_TOKEN=
JURY_ACCESS_ENABLED=
JURY_ACCESS_CODE_HASH=
JURY_PROXY_TOKEN=
RUN_EXECUTION_MODE=
JOB_BACKEND=
NEXT_PUBLIC_API_BASE_URL=
```

`JURY_ACCESS_CODE_HASH` is the SHA-256 hash of the access code. `JURY_PROXY_TOKEN` is server-only and is used by the web proxy to unlock jury actions against the API.

## Deployment Shape

The hackathon deployment uses:

- one Cloud Run service for the FastAPI API
- one Cloud Run service for the Next.js web app
- one Cloud Run Job for durable source/OCR processing
- Google Cloud Storage for raw and normalized source artifacts
- Secret Manager for service credentials and jury/admin secrets

Deployment helpers are in `deploy/gcp`. They expect placeholders or Secret Manager values, not checked-in credentials.

## Limitations

- The demo corpus is curated for the hackathon and is not a complete classical-text library.
- Public multi-tenant isolation and public rate limiting are outside this hackathon scope.
- Open Discovery is intentionally controlled; it is not an unrestricted web crawler.
- Weak OCR, catalog leads, and source metadata leads are not treated as final evidence.
- Human verification remains part of the scholarly workflow.

## Tech Stack

- TypeScript
- React
- Next.js
- Python
- FastAPI
- Pydantic
- Tailwind CSS
- Lucide React
- Google Gemini
- Google Cloud Run
- Google Cloud Storage
- Google Cloud Vision OCR
- Elastic / Elasticsearch
- Elastic MCP / Agent Builder tools

## License

Open source under the Apache License 2.0. See [LICENSE](LICENSE).
