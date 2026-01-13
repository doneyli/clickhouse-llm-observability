# Codebase Cleanup Plan & Assessment

**Created:** 2025-01-12
**Status:** Completed

---

## Executive Summary

This document provides a comprehensive audit of the ClickHouse LLM Observability codebase with **specific, actionable cleanup items**. After cleanup, the Quickstart and Tutorial guides will be updated to reflect the clean codebase.

**Total Cleanup Impact:**
- Remove ~1,500 lines of dead/duplicate code
- Remove ~560 KB of files that shouldn't be committed
- Standardize dependency versions across 6 services
- Add health checks to 5 core services
- Pin 5 floating Docker image versions

---

## Phase 1: File Cleanup (Immediate)

### 1.1 Remove Committed Cache/Build Artifacts

**Issue:** `__pycache__` directories were committed despite being in `.gitignore`

**Files to Delete:**
```
text-to-sql/__pycache__/  (7 files)
vector-rag/__pycache__/   (5 files)
```

**Command:**
```bash
git rm -r --cached text-to-sql/__pycache__
git rm -r --cached vector-rag/__pycache__
```

---

### 1.2 Remove Committed Log Files

**Issue:** Log files totaling ~460 KB committed to repo

**Files to Delete:**
```
logs/console.log                    (373 KB - largest offender)
logs/debug-2026-01-*.log           (empty but shouldn't exist)
logs/error-2026-01-*.log           (empty but shouldn't exist)
logs/.*-audit.json                 (2 audit files)
mcp-logs/mcp.log                   (68 KB)
```

**Command:**
```bash
git rm -r --cached logs/
git rm -r --cached mcp-logs/
# Keep directories with .gitkeep if needed for Docker mounts
```

---

### 1.3 Remove Committed .env File (Security Risk)

**Issue:** `.env` file with credentials committed to repo

**File to Delete:**
```
.env                               (2.4 KB - contains API keys!)
```

**Command:**
```bash
git rm --cached .env
# Ensure .env is in .gitignore (it is)
```

---

### 1.4 Remove Duplicate Documentation

**Issue:** Duplicate Langfuse documentation files

| File | Size | Keep? |
|------|------|-------|
| `LANGFUSE_INTEGRATION_ISSUE.md` (root) | 24 KB | DELETE |
| `docs/LANGFUSE_INTEGRATION.md` | 13.9 KB | KEEP |

**Command:**
```bash
git rm LANGFUSE_INTEGRATION_ISSUE.md
```

---

### 1.5 Remove Old Quickstart (After New Docs Finalized)

**Issue:** Overlapping quickstart files

| File | Size | Status |
|------|------|--------|
| `docs/QUICKSTART.md` | 10.5 KB | DELETE (old version) |
| `docs/QUICKSTART_GUIDE.md` | 12.7 KB | KEEP (new version) |

**Command:**
```bash
git rm docs/QUICKSTART.md
```

---

## Phase 2: Script Cleanup

### 2.1 Remove Deprecated Dashboard Scripts

**Issue:** 3 dashboard scripts, only 1 works for LLM data

| Script | Lines | Status | Reason |
|--------|-------|--------|--------|
| `scripts/create-hyperdx-dashboard.sh` | 427 | DELETE | External API v2 doesn't support traces |
| `scripts/create-hyperdx-dashboard.py` | 524 | DELETE | Same limitation as shell version |
| `scripts/create-hyperdx-dashboard-mongo.sh` | 399 | KEEP | Only working method |

**Impact:** Removes 951 lines of non-functional code

**Command:**
```bash
git rm scripts/create-hyperdx-dashboard.sh
git rm scripts/create-hyperdx-dashboard.py
```

---

### 2.2 Remove Orphaned Setup Script

**Issue:** `setup.sh` is not referenced anywhere in documentation

| Script | Lines | Status | Reason |
|--------|-------|--------|--------|
| `scripts/setup.sh` | 51 | DELETE | Orphaned, not documented |

**Command:**
```bash
git rm scripts/setup.sh
```

---

### 2.3 Final Scripts Directory Structure

After cleanup:
```
scripts/
├── validate.py                        # General deployment validation (KEEP)
├── validate-langfuse.sh               # Langfuse-specific validation (KEEP)
├── generate_load.py                   # Load testing utility (KEEP)
└── create-hyperdx-dashboard-mongo.sh  # Dashboard creation (KEEP - only working method)
```

---

## Phase 3: Dependency Cleanup

### 3.1 text-to-sql/requirements.txt

**Remove unused dependencies:**
```diff
- langchain-openai>=0.2.0           # NOT USED - only langchain-anthropic is used
- uvicorn>=0.30.0                   # NOT USED - no FastAPI server
- fastapi>=0.111.0                  # NOT USED - no FastAPI server
- traceloop-sdk>=0.30.0             # NOT USED - listed but never imported
```

**Updated file:**
```
# Core LLM frameworks
langchain>=0.3.0,<1.0.0
langchain-anthropic>=0.3.0,<1.0.0
langchain-community>=0.3.0,<1.0.0

# OpenTelemetry
opentelemetry-api>=1.25.0,<2.0.0
opentelemetry-sdk>=1.25.0,<2.0.0
opentelemetry-exporter-otlp-proto-http>=1.25.0,<2.0.0

# OpenLLMetry - LLM instrumentation
opentelemetry-instrumentation-langchain>=0.30.0,<1.0.0

# TruLens evaluation
trulens-core>=1.0.0,<2.0.0
trulens-providers-langchain>=1.0.0,<2.0.0
trulens-dashboard>=1.0.0,<2.0.0

# Langfuse (optional dual instrumentation)
langfuse>=2.0.0,<3.0.0

# Utilities
httpx>=0.27.0,<1.0.0
python-dotenv>=1.0.0,<2.0.0
```

---

### 3.2 vector-rag/requirements.txt

**Remove unused dependencies:**
```diff
- traceloop-sdk>=0.30.0             # NOT USED - listed but never imported
```

**Standardize versions:**
```diff
- opentelemetry-sdk>=1.20.0
+ opentelemetry-sdk>=1.25.0,<2.0.0

- opentelemetry-api>=1.20.0
+ opentelemetry-api>=1.25.0,<2.0.0

- opentelemetry-exporter-otlp-proto-http>=1.20.0
+ opentelemetry-exporter-otlp-proto-http>=1.25.0,<2.0.0
```

---

### 3.3 trace-evaluator/requirements.txt

**Remove unused dependencies:**
```diff
- pandas>=2.0.0                     # NOT USED - never imported
```

**Fix incorrect package:**
```diff
- trulens>=1.0.0                    # Wrong package name
+ trulens-core>=1.0.0,<2.0.0        # Correct package
```

**Fix version mismatch:**
```diff
- langchain-anthropic>=0.2.0
+ langchain-anthropic>=0.3.0,<1.0.0

- opentelemetry-api>=1.20.0
+ opentelemetry-api>=1.25.0,<2.0.0
```

---

### 3.4 librechat-exporter/requirements.txt

**Standardize versions:**
```diff
- opentelemetry-api>=1.20.0
+ opentelemetry-api>=1.25.0,<2.0.0

- opentelemetry-sdk>=1.20.0
+ opentelemetry-sdk>=1.25.0,<2.0.0

- opentelemetry-exporter-otlp-proto-http>=1.20.0
+ opentelemetry-exporter-otlp-proto-http>=1.25.0,<2.0.0
```

---

### 3.5 test-scenarios/requirements.txt

**Standardize versions:**
```diff
- opentelemetry-api>=1.20.0
+ opentelemetry-api>=1.25.0,<2.0.0

- opentelemetry-sdk>=1.20.0
+ opentelemetry-sdk>=1.25.0,<2.0.0

- opentelemetry-exporter-otlp-proto-http>=1.20.0
+ opentelemetry-exporter-otlp-proto-http>=1.25.0,<2.0.0
```

---

### 3.6 langfuse-evaluator/requirements.txt

**Add version bounds:**
```diff
- langfuse>=2.0.0
+ langfuse>=2.0.0,<3.0.0

- httpx>=0.27.0
+ httpx>=0.27.0,<1.0.0

- python-dotenv>=1.0.0
+ python-dotenv>=1.0.0,<2.0.0
```

---

## Phase 4: Docker Configuration Cleanup

### 4.1 Pin Floating Image Versions

**Issue:** 5 services use `latest` tags - production risk

**Changes in docker-compose.yaml:**
```diff
  mongodb:
-   image: mongo:latest
+   image: mongo:7.0

  otelcol:
-   image: otel/opentelemetry-collector-contrib:latest
+   image: otel/opentelemetry-collector-contrib:0.115.0

  langfuse-minio:
-   image: minio/minio:latest
+   image: minio/minio:RELEASE.2024-12-18T13-15-44Z

  langfuse-minio-init:
-   image: minio/mc:latest
+   image: minio/mc:RELEASE.2024-12-18T10-29-56Z

  langfuse-web:
-   image: langfuse/langfuse:latest
+   image: langfuse/langfuse:2
```

---

### 4.2 Add Health Checks to Core Services

**Issue:** 7 core services lack health checks

**Add to docker-compose.yaml:**

```yaml
  api:
    # ... existing config ...
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3080/api/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  mongodb:
    # ... existing config ...
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
      interval: 10s
      timeout: 5s
      retries: 5

  meilisearch:
    # ... existing config ...
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7700/health"]
      interval: 10s
      timeout: 5s
      retries: 3

  otelcol:
    # ... existing config ...
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:13133/"]
      interval: 30s
      timeout: 10s
      retries: 3

  mcp-clickhouse:
    # ... existing config ...
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

---

### 4.3 Consolidate Duplicate LibreChat Exporter Services

**Issue:** Two services with identical Dockerfile, different commands

**Current (2 services):**
```yaml
librechat-exporter-watcher:   # Always running
  command: ["python", "main.py", "--watch", "--interval", "10"]

librechat-exporter:           # Manual run (profiles: tools)
  # No command - uses Dockerfile default
```

**Recommended (keep both but clarify):** The current setup is intentional - one for continuous export, one for manual runs. Add comments to clarify.

---

## Phase 5: .env.example Reorganization

**Issue:** Required and optional variables are mixed together

**Reorganize .env.example:**
```bash
# ==============================================================================
# REQUIRED - Must be set before running
# ==============================================================================

# Anthropic API Key (https://console.anthropic.com/)
ANTHROPIC_API_KEY=

# ClickStack API Key (http://localhost:8080 -> Team Settings)
CLICKSTACK_API_KEY=

# LibreChat Secrets (generate with: openssl rand -hex 32)
CREDS_KEY=
CREDS_IV=
JWT_SECRET=
JWT_REFRESH_SECRET=

# ==============================================================================
# OPTIONAL - Defaults work for most cases
# ==============================================================================

# ClickHouse MCP Server (default: public demo database)
CLICKHOUSE_HOST=sql-clickhouse.clickhouse.com
CLICKHOUSE_USER=demo
CLICKHOUSE_PASSWORD=

# LLM Models
ANTHROPIC_MODEL=claude-sonnet-4-20250514
TRULENS_MODEL=claude-3-5-haiku-20241022
TEMPERATURE=0.7

# Service Ports
TEXT_TO_SQL_PORT=8002
VECTOR_RAG_PORT=8003

# ==============================================================================
# LANGFUSE (Optional - enable with: docker compose --profile langfuse up)
# ==============================================================================

LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=http://localhost:3001
LANGFUSE_PORT=3001

# ==============================================================================
# INTERNAL - Usually don't change
# ==============================================================================

MONGO_URI=mongodb://mongodb:27017/LibreChat
MEILI_HOST=http://meilisearch:7700
MEILI_MASTER_KEY=DrhYf7zENyR6AlUCKmnz0eYASOQdl6zxH7s7MKFSfFCt
ALLOW_REGISTRATION=true
CONSOLE_JSON=true
DEBUG_CONSOLE=true
```

---

## Execution Checklist

### Phase 1: File Cleanup
- [x] Remove `__pycache__` directories from git tracking
- [x] Remove log files from git tracking
- [x] Remove `.env` from git tracking (security)
- [x] Remove `LANGFUSE_INTEGRATION_ISSUE.md` (duplicate)
- [x] Remove `docs/QUICKSTART.md` (old version)

### Phase 2: Script Cleanup
- [x] Remove `scripts/create-hyperdx-dashboard.sh`
- [x] Remove `scripts/create-hyperdx-dashboard.py`
- [x] Remove `scripts/setup.sh`

### Phase 3: Dependency Cleanup
- [x] Update `text-to-sql/requirements.txt`
- [x] Update `vector-rag/requirements.txt`
- [x] Update `trace-evaluator/requirements.txt`
- [x] Update `librechat-exporter/requirements.txt`
- [x] Update `test-scenarios/requirements.txt`
- [x] Update `langfuse-evaluator/requirements.txt`

### Phase 4: Docker Cleanup
- [x] Pin floating image versions in `docker-compose.yaml`
- [x] Add health checks to core services
- [x] Add clarifying comments for exporter services

### Phase 5: Configuration
- [x] Reorganize `.env.example` with clear sections

### Phase 6: Documentation Update
- [x] Update `QUICKSTART_GUIDE.md` to reflect clean codebase
- [x] Update `TUTORIAL.md` to reflect clean codebase
- [x] Update `README.md` documentation links

---

## Summary of Changes

| Category | Items Removed | Lines/Size Removed |
|----------|---------------|-------------------|
| Cache files | 12 `.pyc` files | ~100 KB |
| Log files | 10 log files | ~460 KB |
| Environment | 1 `.env` file | 2.4 KB (security) |
| Documentation | 2 files | ~35 KB |
| Scripts | 3 scripts | 1,002 lines |
| Dependencies | 6 packages | - |
| **Total** | **28+ items** | **~600 KB + 1,000 lines** |

| Category | Items Added/Fixed |
|----------|------------------|
| Health checks | 5 services |
| Pinned versions | 5 Docker images |
| Version bounds | 20+ dependencies |
| Documentation | Reorganized .env.example |

---

## Questions Before Execution

1. **Confirm file deletions:** Should I proceed with removing all items in Phase 1-2?

2. **Dependency testing:** After updating requirements.txt files, should I rebuild and test containers?

3. **Docker image versions:** The pinned versions listed are recent stable releases. Should I use specific versions or just move away from `latest`?

4. **Commit strategy:** Should cleanup be one commit or broken into phases?
