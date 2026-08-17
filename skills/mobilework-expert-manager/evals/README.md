# Evaluation extension contract

`evals.json` keeps the portable `prompt`, `files`, and `expectations` fields used by
`skill-creator`. The optional fields below are compatible extensions consumed by the
MobileWork evaluation runner:

- If `conversation` is non-empty, `conversation` replaces `prompt` as the complete
  ordered input sequence; the duplicated first prompt is only a fallback for runners
  that do not support multi-turn execution.
- The runner must generate one assistant response after every user turn in the same
  persistent session and save the complete result as `transcript.json` and
  `transcript.md`.
- `suites` and `critical_expectation_indexes` must be copied unchanged into each
  run's metadata. Expectation indexes are zero-based.
- `host_expectation_indexes` is an optional, zero-based list of unique expectation
  indexes that must also be copied unchanged into run metadata. It is a compatible
  extension: catalogs without it behave as though the list were empty.
- Visible-response grading always uses all expectation indexes minus
  `host_expectation_indexes` (`visible = all - host`). A host expectation may also be
  critical, but it is never moved into the visible-response evidence surface.
- Multi-turn grading consumes the whole transcript, never only the final response.
- Side-effect assertions require a real host tool/event ledger. `metrics.json` records
  filesystem-write, network, process/preflight, Plugin, MCP, and generator-invocation
  counts plus their evidence source. Question-channel assertions additionally require
  interaction-tool events with `decision_id` and channel. When the host does not expose
  an event class, that assertion has exact `status: "not-verified"`; absence from
  assistant text is not a pass. A host-channel assertion is supported only by a
  complete, independent host question-channel ledger; Skill text and assistant
  self-report are not host evidence.
- Creation-location assertions use ordered `question.asked` and `question.replied`
  events plus the pre-answer side-effect ledger when the host exposes a question tool.
  That ledger separately covers preflight/process, file-directory-configuration writes,
  network/data egress, Plugin/MCP, permission expansion, generator, and validation.
  When no equivalent tool is available, the actual assistant question and following
  user reply prove only the conversation fallback; they never fabricate host events.
- Authoritative grading statuses are exactly `passed`, `failed`, and `not-verified`.
  The compatibility boolean is true only for `status: "passed"`. Because the stock
  Viewer is binary, adapters encode `not-verified` as `passed: false` and prefix its
  evidence with `[not-verified]`; release gates must read the authoritative status,
  never infer it from the Viewer icon.
- Real token and timing metrics are recorded when exposed by the host. Missing numeric
  values are `null` with exact `status: "not-verified"`, never zero and never estimated
  from text length.

These extensions do not make a one-run smoke a release benchmark. The runner must
label the executed suite, repetition count, host, and every unrun release gate.

## Trigger evaluation contract

`trigger-evals.json` deliberately keeps the portable two-field schema consumed by
`mobilework-skill-creator`: every item contains only `query` and `should_trigger`.
The catalog contains 40 unique queries, balanced as 20 positive and 20 negative.
At least 10 positive queries omit the explicit strings `MobileWork`, `专家`, and
`expert.json`, so selection cannot rely only on product-name matching. Negative
queries are close alternatives in three required groups: MobileWork UI work,
general-purpose Skill authoring or installation, and ordinary OpenCode work.

The shared `mobilework-skill-creator/scripts/run_loop.py` runner performs the split;
do not add a second split field or a second source of truth to this catalog. Invoke
its existing stratified splitter with holdout `0.4` and seed `42`. For this balanced
catalog, the deterministic result is a 24-item train set with 12 positive and 12
negative queries, plus a 16-item held-out set with 8 positive and 8 negative queries.
The held-out results must stay blinded from description improvement. Re-running the
split with the same catalog, ratio, and seed must return the same ordered partitions.
As a dataset-hardness control, the frozen keyword baseline predicts positive when a
query contains `MobileWork`, `专家`, or `expert.json`; its full-set accuracy must remain
below 70%, so passing cannot be reduced to those obvious product words.

## Behavior suites

Every behavior case in `evals.json` declares at least one executable `suites` tag.
The three primary suites are nested; selecting a suite means running every catalog
item whose `suites` list contains that exact tag.

| Suite | Cases | Purpose |
|---|---:|---|
| `pr-smoke` | 8 | Fast design, generation, diagnosis, installation, critical, and multi-turn coverage |
| `release-benchmark` | 13 | PR coverage plus packaging, hostile-archive preflight, and three paired role-autonomy/import/mode cases |
| `full` | 44 | Complete behavior catalog, including dynamic capability-resource selection |

The exact PR case IDs are `101, 104, 106, 108, 109, 114, 139, 140`. The release
suite adds `110`, `127`, `141`, `142`, and `143`. Cases `139` and `140` retain the supplemental
`requirements-discovery` and `multi-turn` tags; case `104` now uses the same tags for
the confirmation → creation-location question → preflight sequence. `critical_expectation_indexes` and
`host_expectation_indexes` remain independent metadata and must be copied unchanged
into run metadata; suite membership does not turn unavailable host evidence into a
visible pass.

Cases `101`–`103` assert that role presence and responsibility text do not directly
create resources while the manager may still propose evidence-backed capability
candidates and choose the minimal-fit carrier. Case `104` retains explicit managed
Skill creation with semantic names and no expert or role prefix. Cases `112`, `116`,
`136`, and `138` lock the Custom Tool, deterministic executor, Shell-in-Skill, and
MCP-plus-Plugin boundaries. Case `144` covers one confirmed design that generates
exactly one shared Skill, one role-owned namespaced Custom Tool, and one package-wide
namespaced local Plugin for three distinct runtime responsibilities.

`contract-regressions.json` contains 33 machine-contract cases. Case `33` locks the
same dynamic mapping, least-runtime-power, one-resource-per-responsibility and
material-impact `full-card-first` zero-write rules independently of behavior grading.
