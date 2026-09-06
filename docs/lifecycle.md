# Engineering lifecycle fork

This fork adds an **opt-in coordinator**, not a second release engine. Tend
can direct planning, implementation, independent review and delivery follow-up.
Protected integration and release remain the adopter's checked executors.
The coordinator supplies requests and evidence; it never receives their keys.

## Configure

Run the generator from the reviewed fork commit, not PyPI's upstream package:

```sh
uvx --from 'git+https://github.com/OWNER/FORK@FULL_SHA#subdirectory=generator' tend init
```

Add `action_source: OWNER/FORK@FULL_SHA` to `.config/tend.yaml`, replacing
`FULL_SHA` with the same full lowercase commit SHA. It pins the action and
bundled Skills as well as the regeneration command. Existing defaults remain
unchanged when this field and the lifecycle workflow are omitted.

```yaml
workflows:
  lifecycle:
    enabled: true
    watched_workflows: [Verify, Release] # use the adopter's actual names
  triage:
    enabled: false # lifecycle owns intake in this profile
```

The coordinator wakes on Issue and PR changes, watched workflow completion,
manual dispatch and an hourly recovery tick. One repository-wide concurrency
group coalesces wakeups; every invocation re-reads live state. It advances one
handoff, not an unbounded background agent. Jobs retain the `tend` Environment
and existing harness security preflight. Enabling configuration does not grant
access or satisfy those checks.

## Adopter contract

The existing `running-tend` overlay must name these concrete interfaces before
activation; missing information blocks the run:

1. Canonical, reviewed Skill paths and their revision: planning/specification,
   implementation/TDD, GitNexus, and three-axis code review. Verify them in the
   actual sandbox, not just on the developer's machine.
2. A capability command that checks those Skills, the exact checkout's GitNexus
   index and a real Honcho read. Memory is context, never completion evidence.
3. Integration and release request interfaces for existing trusted executors.
   Executors independently resolve the requested repository, Issue and SHA and
   verify holds, checks and review provenance. Labels/comments are not authority.
4. The actual post-release verification command and repair/revert interface.
5. A durable Issue checkpoint with phase, SHA, run/evidence links, repair attempt
   count and next handoff. Two failed repair attempts stop automatic retries.

Keep three independent reviewers: Standards, Spec and Ponytail Ultra. Checks
and reviews bind to the exact candidate. If integration changes the commit,
verify and review the integrated result before release. Publication without
post-release verification is unfinished work.

## Evidence and limitations

The Claude action accepts `metadata_only: "true"` for private-context runs.
It disables native session persistence, discards stderr, filters stdout before
writing it, and publishes only structural native launch/return events and numeric
usage. Agent-written summaries and session-log artifacts are not published;
`show_full_output` cannot override this mode, and Gist memory is incompatible.
Malformed or over-4-MiB records fail the capture. Interrupted usage without a
final cost record is marked partial with unknown cost, never a free run.

This minimizes Tend-managed diagnostics, not the agent's other tools: adopter
setup must not print private context, and the agent must not post it to an Issue
or write it into repository artifacts. Native event metadata does not establish
review correctness or protected-write authorization. Qualify with dummy context
on the actual pinned runtime before admitting a private service.

For that synthetic qualification only, set `metadata_probe_file` to a workspace
file containing a 64-character lowercase hex canary (optional final newline).
The supervisor reads it once before launch; missing, invalid or outside-workspace
files refuse the launch. The same pre-persistence filter derives `probe_seen`
booleans from native return text and final result text, ignoring supplied flags
and launch prompts. Require correlated successful native returns and a successful
final result with these booleans true, then verify the canary is absent from
retained diagnostics. An absent canary alone is not positive proof of suppression.
Never point this qualification input at real private context or credentials.
Tool returns also carry `probe_text_bytes`, the observed UTF-8 text size when
the probe was present (zero otherwise). Use it to qualify representative-sized
responses; supplied counts are ignored. It does not establish file-free native
I/O: the adopter must inspect native temporary/output paths after execution.
Retained message events also preserve validated `parent_tool_use_id` values:
explicit null means a root event, while a tool ID identifies the spawning
native call ([native message contract](https://code.claude.com/docs/en/agent-sdk/subagents)).
Missing lineage stays missing; malformed lineage refuses capture. This lets a
consumer correlate observed child tool responses without asking the child to
regenerate their bodies. Actual runtime lineage still needs qualification.

For a previously observed native launch with matching session and parent,
a single successful return may carry `native_agent: {id, status: "completed"}`.
These fields come only from the native structured `tool_use_result`
([Agent output contract](https://code.claude.com/docs/en/agent-sdk/typescript#sdkusermessage)),
not decorated tool-result text, launch arguments or supplied metadata flags.
Missing output stays missing; invalid native IDs refuse capture. The currently
supported native ID is `a` followed by 16 lowercase hexadecimal characters.

The adopter supplies a local MCP tool named `mcp__tend_review__submit_review`.
Its input schema accepts this object; the child submits it through the native
tool interface, not as formatted final text. The encoded declaration is at most
1024 UTF-8 bytes, with no extra or duplicate keys:

```json
{"axis":"standards","candidate":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","fixed_point":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","verdict":"ship","complete":true,"findings":[]}
```

Axes are `standards`, `spec` and `ponytail`; verdicts are `ship` or `revise`.
Both commits must be full lowercase SHA-1 strings. `ship` requires `complete:true`
and no findings. `revise` requires incomplete review or at least one finding.
Up to eight distinct findings may contain only numeric `file` and `line` plus
`kind`: `correctness`, `security`, `spec` or `simplification`. `file` is a zero-based
index below 1000000 into the adopter's trusted sorted changed-file list for the
exact diff. `line` is 0 through 10000000; zero means file-level. The adopter must
resolve and validate these indices against that immutable diff before using them
to investigate. Boolean numbers, unknown fields and free-form text are refused.

The filter observes the named tool's native launch and successful return in the
same known direct-child lineage. Its launch retains only tool identity and the
validated object under `review`; its successful return retains `review_submission`.
Only after the containing Agent/Task actually completes can that submission
become `native_agent.review`. Root claims, unknown parents, mismatched sessions,
failed calls and unfinished agents do not supply reviews. Duplicate calls/returns
or a second successful submission for the same child fail the capture.

On native completion, derived `review_parse` is `accepted` or
`missing-submission`. Supplied flags and final child text cannot set it.
There is no fallback to final-text JSON, even when well-formed. Pin the adopter's
tool, prompt and guard together; the guard must independently correlate the
submission events with the completed child. The tool has no delivery authority,
external write or memory access, and validates the adopter's exact target.

No free-form findings, paths, rule quotes or private context are retained, and
the harness performs no retry. This is an observed model declaration, not proof
that the review is correct. Adopters still validate independent trusted jobs and
sessions, exact commits, baseline, holds and artifact provenance. Actual native
tool output requires qualification on the pinned runtime.

The [throwaway prototype](https://github.com/Reid-Surmeier/tend/blob/510e4b71ad743b85411d277b991a3b36bfd1a8a2/generator/prototype-lifecycle-203.html)
is archived outside production. Its six browser walkthroughs and eight targeted
rejection checks exercise the handoff model, not live agent execution. The HTML
shell and synthetic state are not production state or authorization checks.

Generator tests cover the opt-in workflow, pin validation, both harness renderings
and pinned regeneration. They do **not** prove model authentication, reviewer
independence, retry behavior, tool availability inside a remote runner, protected
executor integration or live delivery. Those require an adopter run with actual
capabilities and separately protected executor credentials. This fork ships no
adopter secrets, host configuration or new memory system.
