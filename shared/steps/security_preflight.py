"""Refuse to run unless the default branch is protected against the bot itself.

Shared verbatim by both harness actions (``claude/``, ``codex/``), where the
step is ``id: security`` and a non-zero exit is what the "Report failure" step
reads off ``steps.security.outcome``.

The step runs with the bot's own token, so ``current_user_can_bypass`` on each
applying ruleset is GitHub's answer to "can this bot bypass?" — teams, custom
roles, and org/enterprise-sourced rulesets are all evaluated server-side. One
update rule the bot cannot bypass proves the bot cannot update the branch. If
update rules exist but the bot can bypass every one, the merge restriction
provably does not restrict the bot and the run aborts. Repos protected by
required reviews alone have no update rule and fall back to the ``.protected``
floor.

Decisions this encodes:

- A ruleset whose ``current_user_can_bypass`` cannot be read proves nothing
  either way, so it neither blocks nor counts as bypassable; the run falls
  through to the ``.protected`` floor if no other update rule settles it.
- Any readable value other than ``never`` counts as bypassable, ``null``
  included — an answer that isn't "never" is not a restriction.
- A rules listing that cannot be read — or that comes back in a shape this
  cannot make rules out of — is treated as "no update rules apply", the same
  fallback the shell body took, so a token that cannot see rulesets still
  meets the ``.protected`` floor rather than passing unchecked.

Inputs (env): ``GITHUB_REPOSITORY`` (from Actions), plus the bot's
``GITHUB_TOKEN``, which reaches ``gh`` through the environment.
"""

from __future__ import annotations

import subprocess
from typing import Any

import _common

BYPASS_ERROR = (
    "The bot can bypass every restrict-updates ruleset on '{branch}' "
    "(current_user_can_bypass != never), so the merge restriction does not "
    "restrict the bot. Remove the bot — or any team, role, or user exemption "
    "covering it — from the rulesets' bypass actors. See docs/security-model.md "
    "in the Tend repo."
)

UNPROTECTED_ERROR = (
    "Default branch '{branch}' is NOT protected. Without branch protection, "
    "the bot can merge PRs without review. Add a branch protection rule or "
    "ruleset before using Tend. See docs/security-model.md in the Tend repo."
)


def update_ruleset_ids(rules: Any) -> list[int]:
    """The ids of the rulesets contributing an ``update`` rule, deduped.

    A ruleset can contribute several rules to one branch, and only the
    ``update`` ones restrict who may move the branch.

    Anything that is not a rule naming both a type and a ruleset id
    contributes nothing, which is what the jq ``select`` this replaced did.
    The listing is read best-effort, so a body that is an error object rather
    than an array has to fall through to the ``.protected`` floor rather than
    abort the gate.
    """
    if not isinstance(rules, list):
        return []
    return sorted(
        {
            rule["ruleset_id"]
            for rule in rules
            if isinstance(rule, dict)
            and rule.get("type") == "update"
            and isinstance(rule.get("ruleset_id"), int)
        }
    )


def main() -> int:
    repo = _common.require_env("GITHUB_REPOSITORY")["GITHUB_REPOSITORY"]

    # The two reads the gate cannot proceed without are left to raise: `_common`
    # relays gh's own explanation and `run` turns the failure into one error.
    default_branch = _common.gh(
        "api", f"repos/{repo}", "--jq", ".default_branch"
    ).strip()

    # A GitHub blip can answer this with an HTML page under a 200, so the parse
    # fails rather than the call: catching only the non-zero exit would abort
    # the gate on an outage, and "Report failure" keys on this step's outcome,
    # so the outage would go unrecorded as well.
    try:
        rules = _common.gh_json("api", f"repos/{repo}/rules/branches/{default_branch}")
    except _common.GH_READ_FAILED:
        rules = []

    verdict = "no-update-rules"  # or: blocked | bypassable
    for ruleset_id in update_ruleset_ids(rules):
        try:
            can_bypass = _common.gh(
                "api",
                f"repos/{repo}/rulesets/{ruleset_id}",
                "--jq",
                ".current_user_can_bypass",
            ).strip()
        except subprocess.CalledProcessError:
            continue
        if can_bypass == "never":
            verdict = "blocked"
            break
        verdict = "bypassable"

    if verdict == "blocked":
        print(
            "Security preflight passed: bot cannot bypass the restrict-updates "
            f"ruleset on '{default_branch}'",
            flush=True,
        )
        return 0
    if verdict == "bypassable":
        return _common.fail(BYPASS_ERROR.format(branch=default_branch))

    # No update rules apply (or none were readable): fall back to requiring
    # that the branch is protected at all, e.g. by required reviews.
    protected = _common.gh(
        "api", f"repos/{repo}/branches/{default_branch}", "--jq", ".protected"
    ).strip()
    if protected != "true":
        return _common.fail(UNPROTECTED_ERROR.format(branch=default_branch))
    print(
        f"Security preflight passed: default branch '{default_branch}' is protected",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    _common.run(main)
