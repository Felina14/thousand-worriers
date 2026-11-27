# Well-Architected review evidence

We reviewed 1,000 Worriers with the Agent Toolkit skill `aws-well-architected-review`, fixed four of its findings, deployed the fixes, load-tested them and ran the skill again on the Reliability pillar. The full reports stay out of this repo because they hold account details. This folder keeps the numbers.

## The two skill runs

| | First review | Re-review |
|---|---|---|
| Run ID | 20261127T095810Z-k7w2 | 20261127T171301Z-r4q8 |
| Mode | full, all 6 pillars | pillar-scoped: Reliability |
| Criticality | Standard | Standard |
| Code | commit 12b693b | commit 4da5eb3 |
| BP pages read | 216 / 216 | 63 / 63 |
| Reliability score (1-5) | 2 | 3 |
| Reliability BPs at High risk | 5 | 0 |
| High findings that cite Reliability BPs | F01, F02, F03, F04 | none (7 Medium, 14 Low) |

[rel-before-after.md](rel-before-after.md) lists the status and risk of all 63 Reliability BPs in both runs.

## Findings we acted on

| Finding | Recommendation in the first review | What we changed | Where | BPs, before → after |
|---|---|---|---|---|
| F01 (High) | Alarm on failed runs, Bedrock throttling and lost worriers | The state machine publishes LostWorriers and TallyFallback for each finished run; the stack adds an SNS topic and alarms on those, on failed runs, on Tally errors and throttles and on Bedrock throttles per Nova model | `statemachine/worry-run.asl.yaml:990` (PublishRunHealth), `template.yaml:641-847`, `README.md:111-122` | REL06-BP03 NI/High → Implemented; REL11-BP06 NI/High → Implemented; REL11-BP01 PI/High → PI/Medium |
| F05 (Medium) | Global admission control and back pressure on the Nova Micro fan-out | A DynamoDB lease (`lock#swarm`) lets one swarm run at a time; other runs wait, take over a dead or expired lease, or end BUSY after 20 minutes | `statemachine/worry-run.asl.yaml:332-497` (AcquireLock to RunBusy), `:811` (ReleaseLock), `:1022` (ReleaseOnFail) | REL04-BP02 PI/Medium → Implemented; REL05-BP04 unchanged at PI/Medium; REL05-BP01 PI/Medium → PI/Low |
| F04 (High) | Monitor the Lambda and Bedrock quotas and raise Lambda concurrency | A second lease (`lock#tally`) runs one tally at a time; Tally paces Nova Pro calls under 13 RPM and backs off with jitter inside its time budget; a test keeps the worst-case run under the execution timeout; an alarm fires at 8 of the 10 allowed Lambda concurrent executions | `statemachine/worry-run.asl.yaml:849-919`, `functions/tally/app.py:39-42`, `:87-130`, `tests/test_worry_run_budget.py`, `template.yaml:758` | REL01-BP04 NI/High → PI/Medium; REL01-BP03 PI/Medium → Implemented; REL01-BP05 NI/Medium → PI/Low; REL07-BP03 PI/Medium → Implemented |
| F07 (Medium) | A repeatable parallel load test at crowd size 1,000 | We ran the same test after each fix: 8 runs of 1,000 worriers, 4 in parallel, in 2 waves. The test script stays local, so the re-review found no load test in the repo | [f05-comparison.json](f05-comparison.json) | REL07-BP04 and REL12-BP03 unchanged at PI/Medium |

The re-review still rates REL01-BP04 Partially Implemented: the Bedrock alarms watch throttles, not usage against each quota, and the Lambda concurrency quota is still 10. It rates REL05-BP04 unchanged because lease queue wait and depth are not metrics.

## Load test, 8 runs per column

Each column is the same test on the code after that fix. Source: [f05-comparison.json](f05-comparison.json).

| | Before F01 | After F01 | After F05 (a) | After F05 (b) | After F04 (a) | After F04 (b) |
|---|---|---|---|---|---|---|
| Runs that ended SUCCEEDED | 8 | 8 | 8 | 5 | 8 | 8 |
| Worriers lost after launch | 66 | 90 | 10 | 0 | 0 | 0 |
| Runs with lost worriers | 8 | 8 | 2 | 0 | 0 | 0 |
| Runs that fell back to the v1 tally | 4 | 2 | 1 | 0 | 0 | 0 |
| Nova Micro throttles | 1,278 | 1,232 | 0 | 0 | 0 | 0 |
| Nova Pro throttles | 619 | 494 | 521 | 343 | 59 | 0 |
| Run duration p50 / p95 (s) | 493.4 / 579.3 | 486.2 / 563.6 | 454.8 / 536.5 | 380.3 / 444.3 | 466.6 / 699.2 | 480.5 / 722.6 |
| Bedrock cost, all 8 runs (USD) | 0.354 | 0.349 | 0.362 | 0.290 | 0.410 | 0.414 |
| LostWorriers alarm went to ALARM | 0 times (no alarm yet) | 1 | 2 | 0 | 0 | 0 |

In the "After F05 (b)" column, 3 of 8 runs failed in the tally, and their 2,900 saved worriers did not reach a result. Each failed run's META `error` is a Bedrock `ThrottlingException` from the tally's Converse calls. The F04 tally lease and Pro pacing removed those failures. The F04 runs take longer at p95 because runs now queue for the tally: tally queue wait p95 was 239.4 s and 359.5 s in the two F04 columns.

The lock tests in the same file passed 8 of 8 checks after F04. After F05, check T3 ("C acquired an expired lease and finished") failed; it passed in the F04 test.

## What the re-review found that we left alone

The build is frozen, so we fixed nothing from the re-review. Four backup BPs (REL09-BP01, -BP03, -BP04, -BP05) scored worse, although no backup code changed: the second run judged the live table, with 73,817 items, point-in-time recovery off and no backups, as the only copy of the runs, and the first run had accepted it as regenerable demo data.
