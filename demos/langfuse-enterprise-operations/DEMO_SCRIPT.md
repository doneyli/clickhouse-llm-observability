# Langfuse Enterprise Operations — Demo Script

The audience watches a platform team run Langfuse for other teams, live: onboard an
application, grant and change access, rotate a key, protect production prompts, prove a
change in the native audit log, provision an organization, offboard, and clean up. Every
button makes real API calls against a licensed local instance, and the inspector shows the
script, request, response and edition of each one.

- **App:** the local portal at http://127.0.0.1:8091 (`./scripts/run-portal.sh`), with Langfuse at http://localhost:3001 beside it
- **Observability:** Langfuse self-hosted Enterprise (server 4.76.0), organization `Demo Organization`; no traces, every act creates administration objects
- **Models:** none; no LLM provider or paid model calls
- **Data:** fictional application, team, organization and email names, typed by the presenter
- **Run length:** about 24 min, 30 with the optional Acts 6 and 8; short path is Opening, Acts 1, 4 and 5, and Close (about 16 min). Estimates until a rehearsal measures them

> Setup lives in [README.md](README.md).

---

## How to run this script

It's a conversation, not a walkthrough. Every act does four things:

- **Frame** — the problem, in their terms. Say it before you touch the screen.
- **Show** — the exact clicks and commands.
- **Land** — the benefit, not the feature.
- **Ask** — an open question that maps it to their world.

The Ask answers tell you which act gets the time. The short path is the Opening, Acts 1, 4
and 5, and the Close. Acts 6 and 8 are optional: show Organizations only when the instance
API is enabled, and Reset only when the room wants to see cleanup.

Use fictional application, team, organization and email names throughout. Keep the
inspector open and switch between Script, HTTP request and Response. The highlight toggle
separates Enterprise capabilities from included OSS steps. A failed real request stops the
run: use its response to explain the prerequisite rather than narrating a simulated success.

---

## 0 · Pre-flight

```bash
cd demos/langfuse-enterprise-operations
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
./scripts/run-portal.sh                  # leave it running in its own terminal
python3 scripts/verify_demo.py --links   # READY, plus the link for every act
```

`verify_demo.py` exits 1 when a required check fails. Fix what it names before the meeting.
A WARN on the instance key only means Act 6 is not available.

**Tabs to open:** the portal on port 8091 and Langfuse on port 3001, signed in as the local
presenter. `--links` prints the governance prompt and audit-log pages for Acts 4 and 5.

### The day before

```bash
python3 scripts/seed_all.py   # re-runs Protected labels: governance project, Member and Admin personas
```

This is where a signup policy, email verification, SSO enforcement or a missing license
shows up, a day early instead of in Act 4.

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| Projects, scoped keys and starter assets from one form | Act 1 |
| Project roles granted and changed by API, with read-back | Act 2 |
| Key rotation without losing service; old key rejected | Act 3 |
| Dashboards provisioned as code (OSS) | Act 3 |
| Protected labels: Member denied production, Admin promotes, CI keys still write | Act 4 |
| Native audit evidence of a role change | Act 5 |
| Instance-level organization provisioning | Act 6 |
| Offboarding that keeps data and integrations | Act 7 |
| Reviewed, bounded cleanup | Act 8 |

---

## Opening · The platform team's job (2 min)

**Frame.** Once a few teams run on Langfuse, the work shifts from tracing to administration:
projects, people, keys, who may ship a prompt to production, and cleanup. Security's first
question is whether you can prove who changed what.

**Ask.** How does a new team get a Langfuse workspace today, and who does it? What does
your security review need to see before production? Which of these do you expect the
license to cover?

**Land.** Let me run each of those as a real operation against a licensed local instance.
Every button makes real API calls, and the inspector shows which ones, and which edition
they need.

---

## Act 1 · Onboard an application (4 min)

**Frame.** A team asks for a workspace. They need a project, a key with the right
permissions, and a starting prompt and dataset, and nobody wants a ticket queue for it.

**Show.** Select **Application**. Name `Support assistant`, team `Application platform`,
retention 30, count 1, and explicitly choose **Ingestion** as the key permission. Click
**Create application**. In the inspector, show purple project and key provisioning, then
the blue prompt and dataset calls. Open its real project ID in Langfuse.

**Land.** A team goes from request to a scoped, working workspace in one reviewed run, and
every object it created has an ID you can open.

**Ask.** Who approves a new project today, and what metadata or retention rules must it
carry?

> **Fallback:** a rerun with the same name reuses the recorded project and verifies it. A
> different team or retention under the same name is refused: pick a new name.

---

## Act 2 · Give a team access, then change it (3 min)

**Frame.** Access is where audits find problems: people granted too much, or never removed.

**Show.** Select **Team**. Add synthetic `example.invalid` identities with Member and
Viewer roles and click **Onboard team**. Read back the grants. Then select
**Permissions**, pick a member, choose a new role, and click **Update role**. Team is a
portal roster, not a native SCIM Group.

**Land.** Access is an API call with a read-back check, not a ticket, so what you
requested and what Langfuse holds can be compared at any time.

**Ask.** Where do your identities come from: an IdP with SCIM, or manual invites?

> **Fallback:** an email that already belongs to an organization user is refused. Use a
> fresh synthetic identity; existing users are preserved.

---

## Act 3 · Rotate a key and add a dashboard (3 min)

**Frame.** Keys leak and people leave. Rotation must not take the integration down, and
the old key must really stop working.

**Show.** Select **Keys**, choose the replacement permissions, and click **Rotate
integration key**. Show the permission-aware checks on the new key and the old key's
rejection. Then select **Dashboards** and click **Create dashboard**; explain that
observations populate the charts.

**Land.** The replacement is verified before the switch, and the old key is proven dead
with a 401, so rotation is routine instead of risky.

**Ask.** How often do you rotate keys today, and what breaks when you do?

---

## Act 4 · Protect production prompts (5 min)

**Frame.** Anyone who can edit a prompt can, by default, change what production serves.
Most teams want reviewers on production, while CI can still deploy.

**Show.** Select **Protected labels**, application `Governance Demo`, and click **Set up
and verify governance**. Show the successful staging update, the expected HTTP 403 for
the Member's production promotion, production unchanged, and the Admin's promotion.
Explain that service keys keep CI write access. These calls use native UI RPC sessions
pinned to 4.76.0, not the Public API.

**Land.** Production prompt changes need the right human role, proven with real sessions,
while your pipeline keeps working.

**Ask.** Who should be allowed to move production on your team, and does your CI deploy
prompts today?

> **Fallback:** if persona sign-in fails, a signup policy, email verification or SSO
> enforcement is blocking demo preparation. Show the response, then the governed prompt
> from `--links`, which the day-before seed proved.

---

## Act 5 · Prove it with native audit evidence (3 min)

**Frame.** A log the portal wrote is not evidence. Security wants Langfuse's own record.

**Show.** Select **Audit evidence** and click **Generate and verify audit evidence**.
Expand the record marked "This run". Read the actor, timestamp, Member → Viewer
before/after, and object ID. Show the restore-to-Member call and the native audit-log link.
This evidence is retrieved from Langfuse storage, not reconstructed from the portal log.

**Land.** Every role change has a native record with who, when, before and after, which is
what an access review asks for.

**Ask.** Where do your audit records need to end up: a SIEM, a quarterly review, an
auditor's request?

---

## Act 6 · Provision an isolated organization (optional, 3 min)

**Frame.** Some teams, business units or customers need hard separation, not just another
project.

**Show.** Select **Organizations**, name `Operations Demo Organization`, and click **Create
organization and workspace**. Show the Bearer-authenticated instance organization creation
and key bootstrapping, then the Basic-authenticated starter project creation. Open the
starter project as the local presenter. A repeated run verifies the same objects.

**Land.** A new tenant, with its own key and first project, comes from one reviewed run.

**Ask.** Do you separate by project or by organization today, and who decides?

> **Fallback:** "Instance API is not configured" means `private/instance.env` or the
> server key is missing. Skip the act; the README's Enable organization creation covers it.

---

## Act 7 · Offboard the team (2 min)

**Frame.** When a team moves on, its access should go, without breaking the application or
losing its data.

**Show.** Select **Offboarding** and click **Remove team access** for the application.
Verify the memberships are absent while the integration still works. Organization
membership and application data remain.

**Land.** Offboarding removes exactly the grants it created and proves the integration
still works.

**Ask.** What does your leaver process look like for tools like this?

---

## Act 8 · Reviewed reset (optional, 3 min)

**Frame.** Cleanup is where automation does damage. It has to delete only what it made.

**Show.** Select **Reset demo** and click **Preview reset**. Show the exact project IDs,
demo memberships and isolated organizations, and any ownership warnings. Type `RESET DEMO`
only when ready to delete everything listed, then click **Delete demo projects and reset**.
Show the read-back verification and any asynchronous cleanup status. Global user accounts
and the original organization remain.

**Land.** Deletion is bound to a reviewed preview, so anything unexpected stops it.

**Ask.** Who signs off on deleting a project in your environment?

> **Fallback:** **Cleanup pending** means background purging is still running. Preview and
> retry later; records are kept.

---

## Close · What to take away (2 min)

1. Projects, access, keys and dashboards are scripted operations with read-backs (Acts 1–3).
2. Production prompt governance and native audit evidence are real, and proven live
   (Acts 4–5).
3. The edition badges show which steps need Enterprise, so the license conversation rests
   on the calls you just saw.

Hand over the repo: `demos/langfuse-enterprise-operations` runs against their own licensed
local instance with the steps in the README. Future additions are specified in
[docs/future-addons.md](docs/future-addons.md).

---

## Show me the code

**1 · Every call is a real CLI call, and the inspector cites its line — `workflows.py:199`**
`Context.call` maps each operation to its method, path and edition from
`api-operations.json`, emits the request with the calling source line, then runs the
vendored CLI at `workflows.py:241`.

**2 · Keys fail closed — `workflows.py:317`**
`issue_project_key` revokes a new key at once if Langfuse does not confirm the requested
role.

**3 · Rotation records the revocation first, and only 401 proves it — `workflows.py:486`**
A failed revoke is retried on the next rotation. `workflows.py:497` expects 401 only,
because a still-valid restricted key also gets 403.

**4 · A denial passes only on its exact status — `workflows.py:775`**
The Member's production promotion must return 403, and `workflows.py:776` reads production
back to prove it did not change.

**5 · The audit role change is always undone — `workflows.py:811`**
The Member role is restored in `finally`, whether or not the native record matched.

**6 · The native adapter refuses other server versions — `scripts/platform-api.mjs:25`**
UI RPC is not a public contract, so the adapter checks `/api/public/health` first.

**7 · Reset is bound to the reviewed preview — `workflows.py:605`**
The preview's SHA-256 fingerprint covers the manifest and live inventory;
`workflows.py:633` refuses a reset whose inventory changed since.

**8 · Loopback only — `server.py:131`**
Host must be loopback, and `server.py:187` requires a loopback Origin when one is sent, a custom header
and JSON on every run request.

---

## Talking points & objections

- **"Isn't this just the Langfuse UI with extra steps?"** It is what a platform team
  scripts once and runs for every team. Each step maps to a documented API the inspector
  names, so it can move into your own pipeline.
- **"Is all of this paid?"** No. Blue steps (prompts, datasets, dashboards, project key
  permission presets) are OSS; purple ones are Enterprise. The `/api/public` prefix does
  not decide licensing.
- **"If production is protected, how does CI deploy prompts?"** Protected labels govern
  human UI roles. Writable project keys can still move them, which Act 4 shows.
- **"Those label calls aren't the Public API. Is that supported?"** No. They use native UI
  RPC, pinned to 4.76.0 and re-validated before any upgrade. Use the UI, or a supported API
  when one exists.
- **"Can we run this against Langfuse Cloud?"** Not as built: the portal refuses any target
  but a local HTTP instance, and the instance API is a self-hosting feature.
- **"Does reset delete people?"** It removes demo users from the organization. Their global
  accounts remain, because the organization SCIM API cannot delete them.

---

## Known limits

- The protected-label and audit calls rely on a native UI adapter pinned to 4.76.0.
- Runs are not transactional; a crash mid-run can need manual reconciliation.
- Instance signup policies or SSO enforcement can block persona preparation.
- The portal is a loopback sample, not a production administration service.
- Global user accounts survive Reset.

The full list is in the README's [Known limits](README.md#known-limits).

---

## Reset / re-run

In the portal: **Reset demo** → **Preview reset** → type `RESET DEMO` → **Delete demo
projects and reset**. `scripts/reset-demo.sh` is the script behind it and needs the
fingerprint the preview returns. Then:

```bash
python3 scripts/seed_all.py      # recreate the governance project and personas
python3 scripts/verify_demo.py   # READY again
```

To re-run without deleting, run any workflow again: completed workflows verify and reuse
their recorded objects.
