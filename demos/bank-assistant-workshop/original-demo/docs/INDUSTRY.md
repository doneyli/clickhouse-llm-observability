# Industry adoption

**What this covers:** public adoption numbers for Langfuse and public customer stories from financial services and other regulated industries. Each entry has its source URL.
**Capability IDs:** GATE-05 (value and adoption evidence).

All figures are **vendor-published and as of 2026-10-07** (re-checked 2026-10-08). Re-verify before quoting them externally. Stories are paraphrased; read the source for exact wording.

## Adoption numbers

| Metric | Value | Source (as of 2026-10-07) |
|---|---|---|
| Companies using Langfuse | 50,000+ | https://langfuse.com/enterprise |
| Fortune 50 / Fortune 500 | 21 of the Fortune 50; 129 of the Fortune 500 | https://langfuse.com/enterprise |
| SDK installs | 65M+ per month | https://langfuse.com/enterprise |
| Docker pulls | 38M+ | https://langfuse.com/enterprise |
| GitHub stars | ~35.5k (35,504 via the GitHub API on 2026-10-08) | https://github.com/langfuse/langfuse |
| Observations processed (Cloud) | 90B+ per month | https://langfuse.com |
| Engineers building on Langfuse | 100,000+ | https://langfuse.com |
| License | MIT (core); EE features under a commercial license | https://langfuse.com/self-hosting/license-key |

## Financial services: public customer stories

| Organization | Segment | What is public | Relevance to the bank | Source (as of 2026-10-07) |
|---|---|---|---|---|
| **Trade Republic** | Fully licensed European bank / broker (10M+ customers) | Ops Tools team runs **self-hosted OSS** Langfuse. It moved from Postgres to the ClickHouse-based version, then expanded from one team to many after passing the bank's compliance requirements. It uses datasets to move production LLM workflows to agentic (ReAct) harnesses safely | Closest analogue: a regulated bank, self-hosted, scaled through internal compliance | https://langfuse.com/users/trade-republic |
| **Ramp** | Corporate cards and finance automation | Fully **self-hosted** on standard AWS building blocks (S3, Redis, Postgres, ECS, Terraform) with **ClickHouse Cloud**. Chosen for being open source, OTel-native and API-first. Uses traces to drive automated fixes of its agents | Same architecture family as deployment model B in [ARCHITECTURE.md](ARCHITECTURE.md) | https://langfuse.com/users/ramp |
| **SumUp** | Payments (4M+ merchants, 35+ markets) | Started with **self-hosting during the PoC**, then rolled AI first-level support out to 35+ markets over ~18 months. Reports deflecting about half of support conversations to AI | Customer-service assistant at scale; POC-to-production path | https://langfuse.com/users/sumup |
| Intuit, Lemonade, Rocket Money | Financial software, insurance, personal finance | Listed as Langfuse users (no detailed story) | Breadth across financial services | https://langfuse.com/users |

## Other regulated industries

| Organization | Segment | What is public | Source (as of 2026-10-07) |
|---|---|---|---|
| **Merck Group** | Pharma / life science | A central platform team serves **80+ AI project teams** globally on self-hosted Langfuse, citing data sovereignty, auditability of every AI interaction, and governance. This is the platform-team operating model proposed in the adoption playbook ([PATH_TO_PRODUCTION.md](PATH_TO_PRODUCTION.md)) | https://langfuse.com/users/merckgroup |

## Compliance posture (vendor-stated)

- **Langfuse Cloud:** SOC 2 Type II, ISO 27001, GDPR, HIPAA alignment (https://langfuse.com/enterprise, https://langfuse.com/security).
- **Self-hosted Enterprise:** SOC 2 Type II / ISO 27001 reports available, plus InfoSec and legal reviews. Bundled with a ClickHouse commercial plan (Cloud, BYOC or Private) (https://langfuse.com/pricing-self-host).
- **In development:** "Hardening for Government" (FIPS-compatible deployment, hardened images, air-gapped deployment documentation) for highly regulated environments (https://langfuse.com/self-hosting/configuration/hardening#hardening-for-government).
