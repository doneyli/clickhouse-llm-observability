# Presenter flow

Use fictional application, team, organization, and email names. Open the portal on port 8091 and Langfuse on port 3001. Keep the inspector open; switch between Script, HTTP request, and Response. The highlight toggle distinguishes Enterprise capabilities from included OSS steps.

1. **Application:** name `Support assistant`, team `Application platform`, retention 30, count 1, explicitly choose Ingestion. Click Create. Show purple project/key provisioning and blue prompt/dataset calls. Open its real project ID in Langfuse.
2. **Team and Permissions:** synthetic `example.invalid` identities, Member/Viewer roles. Read back grants. Change a role. Team is a portal roster, not a native SCIM Group.
3. **Keys and Dashboards:** choose replacement permissions. Show permission-aware checks and old-key rejection. Provision a real dashboard; explain that observations populate charts.
4. **Protected labels:** application `Governance Demo`. Click Set up and verify governance. Show the successful staging update, expected HTTP 403 for Member production promotion, unchanged production, and Admin promotion. Explain that service keys retain CI write access. The calls use native UI RPC sessions pinned to 4.76.0, not the Public API.
5. **Audit evidence:** click Generate and verify. Expand the record marked “This run.” Read actor, timestamp, Member → Viewer before/after, and object ID. Show the restore-to-Member call and the native audit-log link. This evidence is retrieved from Langfuse storage, not reconstructed from the portal log.
6. **Organizations:** name `Operations Demo Organization`. Show Bearer-authenticated instance org creation and key bootstrapping, then Basic-authenticated starter project creation. Open the starter project as the local presenter. A repeated run verifies the same objects.
7. **Offboarding:** remove the selected application's recorded team project grants. Verify absent memberships while the integration still works. Organization membership and application data remain.
8. **Reset:** preview exact project IDs, demo memberships, and isolated organizations. Check ownership warnings. Type `RESET DEMO` only when ready to delete everything listed. Show read-back verification and any asynchronous cleanup status. Global user accounts and the original organization remain.

A failed real request stops the run. Use its response to explain prerequisites rather than narrating simulated success. Inspect the build contract/version before upgrading the server. Future additions are specified in [docs/future-addons.md](docs/future-addons.md).
