# F31 reference qualification — harness corrections

This is fixture development qualification, not a BlueMatrix acceptance execution.

| Attempt | GitHub run | Finding | Correction and read-only proof | Selection |
|---|---|---|---|---|
| Build-all 01, source c2471d0 | 40406002434 | Pinned ansible-core 2.19.3 `get_url` rejects `ca_path`. Build/publication succeeded; prod and UAT refused before download/copy; remaining work cancelled. All three ROOT.war files confirmed absent. | Regression test compares arguments with the exact installed module's options. Same public CA supplied through task-scoped SSL_CERT_FILE; certificate verification remains enabled and CA hash is pinned. Corrected check-mode HEAD and runtime preflight passed before re-dispatch. No product code, account or shared setting changed. | Unselected, preserved |
| Build-all 02, source 4c4b164 | See structured journal | First corrected attempt; new immutable Nexus version p02, preserving p01. | Assertions unchanged. | Selected: PASS, run 40407083127 |

No owner retry exception was consumed for a BlueMatrix case. The corrected
fixture attempt follows the standing harness-only correction rule; no product
predicate was relaxed. Original full job log stays private outside git; GitHub's
run and job metadata identify the preserved attempt.
