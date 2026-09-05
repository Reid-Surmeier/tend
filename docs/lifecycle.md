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
