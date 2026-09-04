---
name: lifecycle
description: Coordinate an engineering Issue from scope through verified delivery using the adopter's Skills and protected executors.
metadata:
  internal: true
---

# Coordinate the lifecycle

Load `/tend-ci-runner:running-in-ci` and the adopter's `running-tend` overlay.
The overlay supplies the canonical, pinned Skill locations, capability check,
integration/release request interfaces and live-verification command. Missing
configuration is a named blocker, not permission to invent those interfaces.

1. **Reconcile.** Read live Issues, PRs, checks and Releases, including the
   current commit and prior handoff evidence. Reconcile unfinished delivery
   before starting another Issue; otherwise select the oldest eligible Issue.
   Read its acceptance criteria, repository instructions, glossary, applicable
   ADRs and the affected module's README. Honor `hands-off`,
   `needs-human-review`, `ready-for-human`, `needs-info`, `blocked` and
   `wontfix`; only the owner changes the human-review hold. Recheck holds before
   every write or handoff. GitHub is work state; Honcho is contextual memory.
2. **Check capabilities.** Run the overlay's setup check as the actual sandbox
   user. Verify the pinned engineering Skills, GitNexus index for this exact
   checkout and a real Honcho read. Missing or unknown required capability
   stops this Issue. Keep credentials in Tend's existing proxy/Environment;
   use no alternate memory store, copied home configuration or other provider.
3. **Advance one handoff.** Use the adopter's procedures: `wayfinder` for
   unresolved direction, `to-spec` for acceptance, `to-tickets` for a larger
   spec, and `implement` plus `tdd` for a ready ticket. Read those Skill files
   as procedures when they are human-invoked commands. GitNexus exploration,
   upstream impact before edits and change analysis before committing are
   required. Stage work on an unprotected candidate branch; the protected
   build branch belongs to the integration executor. Resume existing work,
   not a duplicate branch. Record the attempt before implementation. At most
   two failed repair attempts per Issue across runs; exhaustion is a named
   blocker and is never cleared automatically.
4. **Review and request.** After the candidate baseline passes, invoke the
   adopter's code-review procedure with three independent subagents:
   Standards, Spec and Ponytail Ultra. Give each the same fixed point, full
   candidate SHA, diff and Issue; never substitute the implementer's verdict.
   Resolve findings or record scoped, justified exceptions. Request integration
   only with checks and all three reviews bound to the current SHA. Integration
   can produce a different commit: check and independently review that
   integrated tree before requesting release. A request is data, not permission.
   The existing executor independently re-fetches the current refs, holds,
   checks and trusted review evidence; agent-written comments or labels alone
   cannot authorize protected writes. No agent or child agent holds an executor
   credential, merges a protected branch or cuts a release tag.
5. **Verify delivery.** Follow the executor's actual run and published commit.
   Run the adopter's live-verification procedure before recording completion.
   Failed delivery stays unfinished: open/update the scoped repair or revert
   request using the existing steward interface. Leave one concise Issue
   checkpoint naming the current phase, exact SHA, evidence links, attempt
   count and next handoff. A model exit code, generated workflow, passing local
   test or release request is never a live-success receipt.

Ponytail Ultra applies to every proposal: delete first, reuse existing Skills
and steward code, use native GitHub routing, add only what the current Issue
requires. No persistent coordinator database or generic agent framework.
