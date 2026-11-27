# Reliability before and after the WA review fixes

Agent Toolkit skill `aws-well-architected-review`, criticality Standard. Both runs read the same 63 Reliability BPs: IDs and titles match between the two ledgers.

| | Before | After |
|---|---|---|
| Report (kept out of the repo: it holds account details) | full mode, all pillars | pillar-scoped: Reliability |
| Run | 20261127T095810Z-k7w2 | 20261127T171301Z-r4q8 |
| Code | 12b693b | 4da5eb3 (b557860, d17e007, 66ed02a, 9a49e1a, 5980a8a, 4da5eb3 deployed) |
| BP pages read | 216 / 216 (63 Reliability) | 63 / 63 |
| **Reliability score (1-5)** | **2** | **3** |
| Implemented | 6 | 14 |
| Partially Implemented | 30 | 32 |
| Not Implemented | 9 | 7 |
| Not Applicable | 10 | 8 |
| Cannot Determine | 8 | 2 |
| BPs at High risk | 5 | 0 |
| High findings that cite REL BPs | F01, F02, F03, F04 | none (7 Medium, 14 Low) |

Change across the 63 BPs: 21 better, 4 worse, 6 newly assessed (Cannot Determine before), 32 unchanged.

## What changed for the four findings

| Finding | Fix commits | BPs before | BPs after |
|---|---|---|---|
| F01 alarms | b557860 | REL06-BP03 NI/High, REL11-BP06 NI/High, REL11-BP01 PI/High | REL06-BP03 Implemented, REL11-BP06 Implemented, REL11-BP01 PI/Medium |
| F04 quotas | 66ed02a, 9a49e1a, 5980a8a | REL01-BP04 NI/High, REL01-BP05 NI/Medium, REL01-BP03 PI/Medium, REL07-BP03 PI/Medium | REL01-BP04 PI/Medium, REL01-BP05 PI/Low, REL01-BP03 Implemented, REL07-BP03 Implemented |
| F05 admission control | d17e007, 66ed02a | REL04-BP02 PI/Medium, REL05-BP04 PI/Medium, REL05-BP01 PI/Medium | REL04-BP02 Implemented, REL05-BP04 PI/Medium, REL05-BP01 PI/Low |
| F07 load test | none in code (lock tests only) | REL07-BP04 PI/Medium, REL12-BP03 PI/Medium | REL07-BP04 PI/Medium, REL12-BP03 PI/Medium |

What the after report still flags on these BPs:
- REL01-BP04 (its F4, Medium): the Bedrock alarms watch InvocationThrottles only, so Nova usage alarms only after throttling. The Lambda quota is still 10 with no increase requested.
- REL05-BP04 (its F7, Low): lease queue wait and depth are not metrics.
- REL07-BP04 and REL12-BP03 (its F6, Low): concurrent-run load is not tested and no SLO defines pass or fail.
- REL11-BP01 (its F3, Medium): Step Functions logging is OFF, with no canary, no API 5XX alarm and no revise or reminders alarms.

## BPs that got worse

No commit since 12b693b changed backups, PITR or DeletionPolicy. The second run judged the same design differently: the first run accepted "regenerable demo data" as the reason for no backups, and the second weighed the live table (PITR DISABLED, no backups, no AWS Backup plan) as the only store of runs and revisions.

| BP | Before | After | After run's reason (verbatim) |
|---|---|---|---|
| REL09-BP01 | PI / Low | NI / Medium | "the only store of runs and revisions has no backup and no documented reproduce decision; a table replacement or delete loses every saved run." |
| REL09-BP03 | NI / Low | NI / Medium | "the table supports PITR but it is off, the named anti-pattern, so no automatic recovery point exists." |
| REL09-BP04 | Not Applicable | NI / Low | "no recovery test exists; it depends on first creating backups under REL09-BP01." |
| REL09-BP05 | Not Applicable | NI / Low | "no backup copy is protected from deletion; for disposable demo data the exposure is small." |

Newly assessed (Cannot Determine before): REL11-BP04 now Implemented; REL06-BP06, REL08-BP03 and REL12-BP02 now PI / Low; REL11-BP07 now NI / Low (no availability target); REL12-BP04 now NI / Low (AWS FIS: 0 experiment templates, 0 experiments).

## Every Reliability BP

Bold IDs are BPs cited by findings F01, F04, F05 or F07 in the first review. Status / risk; "-" means no risk rating.

| BP | Title | Before | After | Change |
|---|---|---|---|---|
| **REL01-BP01** (F04) | Aware of service quotas and constraints | Partially Implemented / Medium | Partially Implemented / Medium | same |
| REL01-BP02 | Manage service quotas across accounts and regions | Not Applicable / - | Not Applicable / - | same |
| **REL01-BP03** (F04) | Accommodate fixed service quotas and constraints through architecture | Partially Implemented / Medium | Implemented / - | better |
| **REL01-BP04** (F04) | Monitor and manage quotas | Not Implemented / High | Partially Implemented / Medium | better |
| **REL01-BP05** (F04) | Automate quota management | Not Implemented / Medium | Partially Implemented / Low | better |
| REL01-BP06 | Verify that a sufficient gap exists between the current quotas and the maximum usage to accommodate failover | Not Applicable / - | Not Applicable / - | same |
| REL02-BP01 | Use highly available network connectivity for your workload public endpoints | Partially Implemented / Medium | Partially Implemented / Medium | same |
| REL02-BP02 | Provision redundant connectivity between private networks in the cloud and on-premises environments | Not Applicable / - | Not Applicable / - | same |
| REL03-BP01 | Choose how to segment your workload | Implemented / - | Implemented / - | same |
| REL03-BP02 | Build services focused on specific business domains and functionality | Implemented / - | Implemented / - | same |
| REL03-BP03 | Provide service contracts per API | Partially Implemented / Low | Partially Implemented / Low | same |
| REL04-BP01 | Identify the kind of distributed systems you depend on | Partially Implemented / Medium | Implemented / - | better |
| **REL04-BP02** (F05) | Implement loosely coupled dependencies | Partially Implemented / Medium | Implemented / - | better |
| REL04-BP03 | Do constant work | Not Applicable / - | Not Applicable / - | same |
| REL04-BP04 | Make mutating operations idempotent | Partially Implemented / Low | Partially Implemented / Low | same |
| **REL05-BP01** (F05) | Implement graceful degradation to transform applicable hard dependencies into soft dependencies | Partially Implemented / Medium | Partially Implemented / Low | better |
| REL05-BP02 | Throttle requests | Partially Implemented / Medium | Partially Implemented / Medium | same |
| **REL05-BP03** (F01) | Control and limit retry calls | Partially Implemented / Medium | Partially Implemented / Low | better |
| **REL05-BP04** (F05) | Fail fast and limit queues | Partially Implemented / Medium | Partially Implemented / Medium | same |
| REL05-BP05 | Set client timeouts | Partially Implemented / Medium | Partially Implemented / Low | better |
| REL05-BP06 | Make systems stateless where possible | Implemented / - | Implemented / - | same |
| REL05-BP07 | Implement emergency levers | Partially Implemented / Medium | Partially Implemented / Low | better |
| REL06-BP01 | Monitor all components for the workload (Generation) | Partially Implemented / High | Partially Implemented / Medium | better |
| **REL06-BP02** (F01) | Define and calculate metrics (Aggregation) | Partially Implemented / Medium | Partially Implemented / Medium | same |
| **REL06-BP03** (F01) | Send notifications with real-time processing and alarming | Not Implemented / High | Implemented / - | better |
| **REL06-BP04** (F01) | Automate responses to alarms | Not Implemented / Medium | Partially Implemented / Low | better |
| REL06-BP05 | Analyze logs | Partially Implemented / Medium | Partially Implemented / Low | better |
| REL06-BP06 | Regularly review monitoring scope and metrics | Cannot Determine / - | Partially Implemented / Low | newly assessed |
| REL06-BP07 | Monitor end-to-end tracing of requests through your system | Not Implemented / Medium | Partially Implemented / Low | better |
| REL07-BP01 | Use automation when obtaining or scaling resources | Partially Implemented / Low | Partially Implemented / Low | same |
| **REL07-BP02** (F01) | Obtain resources upon detection of impairment to a workload | Partially Implemented / Medium | Implemented / - | better |
| **REL07-BP03** (F04) | Obtain resources upon detection that more resources are needed for a workload | Partially Implemented / Medium | Implemented / - | better |
| **REL07-BP04** (F07) | Load test your workload | Partially Implemented / Medium | Partially Implemented / Medium | same |
| REL08-BP01 | Use runbooks for standard activities such as deployment | Partially Implemented / Medium | Partially Implemented / Medium | same |
| REL08-BP02 | Integrate functional testing as part of your deployment | Partially Implemented / Medium | Partially Implemented / Medium | same |
| REL08-BP03 | Integrate resilience testing as part of your deployment | Cannot Determine / - | Partially Implemented / Low | newly assessed |
| REL08-BP04 | Deploy using immutable infrastructure | Partially Implemented / Medium | Partially Implemented / Medium | same |
| REL08-BP05 | Deploy changes with automation | Partially Implemented / Medium | Partially Implemented / Medium | same |
| REL09-BP01 | Identify and back up all data that needs to be backed up, or reproduce the data from sources | Partially Implemented / Low | Not Implemented / Medium | **worse** |
| REL09-BP02 | Secure and encrypt backups | Not Applicable / - | Not Applicable / - | same |
| REL09-BP03 | Perform data backup automatically | Not Implemented / Low | Not Implemented / Medium | **worse** |
| REL09-BP04 | Perform periodic recovery of the data to verify backup integrity and processes | Not Applicable / - | Not Implemented / Low | **worse** |
| REL09-BP05 | Implement cyber resilience for backup data | Not Applicable / - | Not Implemented / Low | **worse** |
| REL10-BP01 | Deploy the workload to multiple locations | Implemented / - | Implemented / - | same |
| REL10-BP02 | Automate recovery for components constrained to a single location | Not Applicable / - | Not Applicable / - | same |
| REL10-BP03 | Use bulkhead architectures to limit scope of impact | Not Applicable / - | Not Applicable / - | same |
| **REL11-BP01** (F01) | Monitor all components of the workload to detect failures | Partially Implemented / High | Partially Implemented / Medium | better |
| REL11-BP02 | Fail over to healthy resources | Partially Implemented / Medium | Partially Implemented / Low | better |
| REL11-BP03 | Automate healing on all layers | Implemented / - | Implemented / - | same |
| REL11-BP04 | Rely on the data plane and not the control plane during recovery | Cannot Determine / - | Implemented / - | newly assessed |
| REL11-BP05 | Use static stability to prevent bimodal behavior | Implemented / - | Implemented / - | same |
| **REL11-BP06** (F01) | Send notifications when events impact availability | Not Implemented / High | Implemented / - | better |
| REL11-BP07 | Architect your product to meet availability targets and uptime service level agreements (SLAs) | Cannot Determine / - | Not Implemented / Low | newly assessed |
| REL12-BP01 | Use playbooks to investigate failures | Not Implemented / Medium | Partially Implemented / Low | better |
| REL12-BP02 | Perform post-incident analysis | Cannot Determine / - | Partially Implemented / Low | newly assessed |
| **REL12-BP03** (F07) | Test scalability and performance requirements | Partially Implemented / Medium | Partially Implemented / Medium | same |
| REL12-BP04 | Test resilience using chaos engineering | Cannot Determine / - | Not Implemented / Low | newly assessed |
| REL12-BP05 | Conduct game days regularly | Cannot Determine / - | Cannot Determine / - | same |
| REL13-BP01 | Define recovery objectives for downtime and data loss | Not Implemented / Medium | Not Implemented / Medium | same |
| REL13-BP02 | Use defined recovery strategies to meet the recovery objectives | Partially Implemented / Medium | Partially Implemented / Medium | same |
| REL13-BP03 | Test disaster recovery implementation to validate the implementation | Cannot Determine / - | Cannot Determine / - | same |
| REL13-BP04 | Manage configuration drift at the DR site or Region | Not Applicable / - | Not Applicable / - | same |
| REL13-BP05 | Automate recovery | Partially Implemented / Medium | Partially Implemented / Low | better |
