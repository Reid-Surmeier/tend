"""Runs the agent and decides the step's verdict.

Composes the agent's settings and launch env, starts it as the non-sudo sandbox
user, supervises it to exit or timeout, then turns the finished stream-json into
the step's exit code and ``::error::`` annotation.

Reads (env): ``SANDBOX`` and ``AGENT_ENV_FILE`` (exported by
``proxy/setup-sandbox.sh`` via ``$GITHUB_ENV``), ``RUNNER_TEMP``,
``GITHUB_WORKSPACE``, ``GITHUB_OUTPUT``, ``TEND_MODEL``,
``TEND_ALLOWED_TOOLS``, ``TEND_SYSTEM_PROMPT``, ``TEND_PROMPT``,
``TEND_TIMEOUT_SEC``, ``SHOW_FULL_OUTPUT``, ``BOT_NAME``, ``BOT_ID``, optional
``TEND_AUTO_MEMORY_SETTINGS``, ``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB``, plus the
``GITHUB_*`` context from Actions. ``GITHUB_STEP_SUMMARY`` is read only when
rendering the transcript.
Publishes ``stream_json`` and, after supervision has stopped every sandbox
process, ``sandbox_reaped``. Used by the Claude harness action.

Decisions this module owns:

* **The supervisor times the run, so nothing infers the bound from an exit
  code.** Waiting with a timeout raises on the bound, which is the answer; a
  code cannot give it, since a killed agent and a crashing one both land on the
  same numbers and the agent may return them itself.
* **A zero exit does NOT mean the turn succeeded.** ``claude -p`` exits 0 on
  rate limits, max turns, auth failures and a failed final model request, and
  ``is_error: true`` occurs even on subtype ``success``. The last ``result``
  event decides; a turn with no result event never completed.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, BinaryIO

import _common
import _sandbox

#: Transcript lines rendered to the job summary, so a long session cannot flood it.
TRANSCRIPT_MAX_LINES = 400

#: Seconds the agent has to flush after the bound's TERM, before the KILL.
TERM_GRACE_SEC = 5

#: Stderr lines quoted on a failure, read from the last this many bytes.
STDERR_TAIL_LINES = 20
STDERR_TAIL_BYTES = 64 * 1024

#: Characters of the agent's own reason quoted in the failure annotation.
#: The reason is a whole assistant text block, which can be the agent's entire
#: final answer, and ``enrich-tend-outage-issues.sh`` pastes these annotations
#: into one batched issue comment under a 64 KiB cap — so an unbounded reason
#: crowds out the other runs' rows.
REASON_MAX_CHARS = 500


def settings(allowed_tools: str) -> dict[str, Any]:
    """``.claude/settings.local.json`` for the run.

    ``permissions.allow`` is built from the comma-separated ``allowed_tools``
    input. With ``defaultMode: bypassPermissions`` the allow list is largely
    moot (every tool is permitted), but it is retained so the listed tools stay
    granted via the layered settings if an adopter overrides ``defaultMode`` to
    a stricter mode.

    ``skipDangerousModePermissionPrompt`` pre-accepts the one-time bypass-mode
    "I accept the risks" disclaimer — the key the dialog's accept button writes,
    read from any settings layer. No Stop/StopFailure hooks: headless detects
    completion from the process exit plus the result event. ``attribution``
    (which supersedes the deprecated ``includeCoAuthoredBy``) empties Claude
    Code's ``Co-Authored-By: Claude`` trailer and ``Generated with Claude Code``
    PR footer, so the bot's commits and PRs are attributed to the bot alone.
    """
    return {
        "permissions": {
            "defaultMode": "bypassPermissions",
            "allow": [tool.strip() for tool in allowed_tools.split(",")],
        },
        "skipDangerousModePermissionPrompt": True,
        "attribution": {"commit": "", "pr": ""},
    }


def launch_argv(
    *,
    sandbox: str,
    agent_env_file: str,
    model: str,
    allowed_tools: str,
    system_prompt: str,
    prompt: str,
    subprocess_env_scrub: str,
    bot_name: str,
    bot_id: str,
    ci: str,
    settings_file: str = "",
    metadata_only: bool = False,
) -> list[str]:
    """The command that launches the agent as the non-sudo sandbox user.

    ``sudo env NAME=…`` replaces the environment with only what is listed, so
    :func:`_sandbox.launch_env` composes it; tend's own ``BOT_*``/``CI``
    assignments are the caller-appended names that docstring allows for.

    The model, tools and prompts are argv rather than environment: nothing on
    the far side reads them, and ``--permission-mode`` restates what
    ``settings.local.json`` already says so the mode survives an adopter
    overriding that file.
    """
    agent_env = _sandbox.launch_env(agent_env_file)
    if settings_file:
        # The explicit experimental config field wins over an adopter's general
        # Claude setting. Leaving this variable in the launch env would make a
        # successful restore silently inert even though the injected settings
        # enable and redirect auto memory.
        agent_env = [
            entry
            for entry in agent_env
            if not entry.startswith("CLAUDE_CODE_DISABLE_AUTO_MEMORY=")
        ]
    argv = [
        "sudo",
        "-u",
        sandbox,
        "env",
        *agent_env,
        f"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB={subprocess_env_scrub}",
        f"BOT_NAME={bot_name}",
        f"BOT_ID={bot_id}",
        f"CI={ci}",
        "claude",
        "-p",
        "--model",
        model,
        "--permission-mode",
        "bypassPermissions",
        "--allowedTools",
        allowed_tools,
        "--append-system-prompt",
        system_prompt,
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if settings_file:
        argv.extend(["--settings", settings_file])
    if metadata_only:
        argv.append("--no-session-persistence")
    argv.append(prompt)
    return argv


@dataclass(frozen=True)
class Supervised:
    """How the supervised launch ended."""

    #: The agent's exit code, or None when the bound killed the run.
    exit_code: int | None
    elapsed: int


class Cancelled(BaseException):
    """The runner asked this process to stop, mid-supervision.

    A ``BaseException`` like ``KeyboardInterrupt``: it has to pass through an
    ``except Exception`` on its way to the reap rather than be caught as a
    failure of the run.
    """


@contextlib.contextmanager
def raise_on_cancel() -> Iterator[None]:
    """Turn SIGTERM and SIGINT into :class:`Cancelled` for the block's duration.

    A cancelled workflow — ``cancel-in-progress``, a maintainer pressing cancel
    — reaches this step as a signal. SIGTERM's default disposition ends the
    process where it stands, which would skip the reap below and leave the
    agent running as an orphan, still writing to the workspace, while the
    runner tears the job down. Raising instead routes the cancellation through
    the same ``finally`` every other exit takes.

    Restored on the way out, so a second signal during the reap ends the
    process outright, which is what an escalating runner means by it.
    """

    def cancel(number: int, frame: FrameType | None) -> None:
        raise Cancelled(f"signal {number}")

    previous = {
        number: signal.signal(number, cancel)
        for number in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def signal_sandbox(name: str, sandbox: str) -> None:
    """Send the signal *name* to every process the sandbox uid owns.

    By uid rather than to the child: the child is ``sudo``, which relays
    nothing, so a signal aimed at it reaches the agent not at all. Failure costs
    nothing — there may be no such process left, which is the outcome wanted.
    """
    subprocess.run(
        ["sudo", "pkill", f"-{name}", "-u", sandbox],
        stdin=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _review_declaration(review: Any) -> dict[str, Any] | None:
    """Keep only an exact, bounded declaration, never prose or private fields."""
    if len(json.dumps(review, allow_nan=False).encode()) > 1024:
        return None
    if (
        not isinstance(review, dict)
        or set(review)
        != {"axis", "candidate", "fixed_point", "verdict", "complete", "findings"}
        or not all(
            isinstance(review[key], str)
            for key in ("axis", "candidate", "fixed_point", "verdict")
        )
        or review["axis"] not in {"standards", "spec", "ponytail"}
        or review["verdict"] not in {"ship", "revise"}
        or any(
            not re.fullmatch(r"[0-9a-f]{40}", review[key])
            for key in ("candidate", "fixed_point")
        )
    ):
        return None
    findings = review["findings"]
    if (
        type(review["complete"]) is not bool
        or not isinstance(findings, list)
        or len(findings) > 8
        or any(
            not isinstance(item, dict)
            or set(item) != {"file", "line", "kind"}
            or type(item["file"]) is not int
            or not 0 <= item["file"] < 1_000_000
            or type(item["line"]) is not int
            or not 0 <= item["line"] <= 10_000_000
            or not isinstance(item["kind"], str)
            or item["kind"] not in {"correctness", "security", "spec", "simplification"}
            for item in findings
        )
    ):
        return None
    if len({(item["file"], item["line"], item["kind"]) for item in findings}) != len(
        findings
    ) or (review["verdict"] == "ship") != (review["complete"] and not findings):
        return None
    return review


def capture_metadata(source: BinaryIO, target: BinaryIO, *, probe: str = "") -> None:
    """Allowlist structural events before writing; never persist model text.

    A malformed or oversized record makes the entire capture unsuccessful.
    # ponytail: 4 MiB per record; increase only for a measured legitimate event.
    """
    limit = 4 * 1024 * 1024
    failed = False
    native_launches: dict[str, tuple[str, str | None]] = {}
    native_completions: set[str] = set()
    submission_launches: dict[
        str, tuple[tuple[str, str | None], dict[str, Any] | None]
    ] = {}
    returned_tools: set[str] = set()
    submitted_reviews: dict[str, dict[str, Any] | None] = {}
    review_tool = "mcp__tend_review__submit_review"
    tool_id = re.compile(r"toolu_[A-Za-z0-9]{10,64}\Z")
    session_id = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
    try:
        while line := source.readline(limit + 1):
            if len(line) > limit:
                failed = True
                while line and not line.endswith(b"\n"):
                    line = source.readline(limit + 1)
                continue
            try:
                event = json.loads(
                    line,
                    object_pairs_hook=lambda pairs: (
                        dict(pairs) if len(dict(pairs)) == len(pairs) else None
                    ),
                )
                if not isinstance(event, dict):
                    raise TypeError
                kind = event.get("type")
                record: dict[str, Any] = {"type": kind}
                if kind == "result":
                    success = (
                        event.get("subtype") == "success"
                        and event.get("is_error") is False
                    )
                    failed |= not success
                    record.update(
                        subtype="success" if success else "error", is_error=not success
                    )
                    if probe:
                        answer = event.get("result")
                        record["probe_seen"] = (
                            isinstance(answer, str) and probe in answer
                        )
                    for key in ("num_turns", "total_cost_usd"):
                        value = event.get(key)
                        if (
                            type(value) in (int, float)
                            and math.isfinite(value)
                            and 0 <= value <= 10**15
                        ):
                            record[key] = value
                    usage = event.get("usage")
                    record["usage"] = {
                        key: value
                        for key, value in (
                            usage.items() if isinstance(usage, dict) else ()
                        )
                        if key
                        in {
                            "input_tokens",
                            "output_tokens",
                            "cache_creation_input_tokens",
                            "cache_read_input_tokens",
                        }
                        and type(value) is int
                        and 0 <= value <= 10**15
                    }
                elif kind in ("assistant", "user"):
                    message = event.get("message")
                    if not isinstance(message, dict):
                        raise TypeError
                    blocks = message.get("content")
                    if kind == "user" and isinstance(blocks, str):
                        continue
                    if not isinstance(blocks, list):
                        raise TypeError
                    kept = []
                    for block in blocks:
                        if not isinstance(block, dict):
                            raise TypeError
                        if (
                            kind == "assistant"
                            and block.get("type") == "tool_use"
                            and block.get("name") in ("Agent", "Task", review_tool)
                        ):
                            value = block.get("id")
                            if not isinstance(value, str) or not tool_id.fullmatch(
                                value
                            ):
                                raise ValueError
                            kept.append(
                                {"type": "tool_use", "name": block["name"], "id": value}
                            )
                            if block["name"] == review_tool:
                                review = _review_declaration(block.get("input"))
                                if review is not None:
                                    kept[-1]["review"] = review
                        elif kind == "user" and block.get("type") == "tool_result":
                            value = block.get("tool_use_id")
                            if (
                                not isinstance(value, str)
                                or not tool_id.fullmatch(value)
                                or type(block.get("is_error", False)) is not bool
                            ):
                                raise ValueError
                            kept.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": value,
                                    "is_error": block.get("is_error", False),
                                }
                            )
                            if probe:
                                content = block.get("content")
                                texts = (
                                    [content]
                                    if isinstance(content, str)
                                    else [
                                        part.get("text")
                                        for part in (
                                            content if isinstance(content, list) else []
                                        )
                                        if isinstance(part, dict)
                                        and part.get("type") == "text"
                                    ]
                                )
                                kept[-1]["probe_seen"] = any(
                                    isinstance(text, str) and probe in text
                                    for text in texts
                                )
                                kept[-1]["probe_text_bytes"] = (
                                    sum(
                                        len(text.encode())
                                        for text in texts
                                        if isinstance(text, str)
                                    )
                                    if kept[-1]["probe_seen"]
                                    else 0
                                )
                    if not kept:
                        continue
                    record["message"] = {"content": kept}
                    if "parent_tool_use_id" in event:
                        parent = event["parent_tool_use_id"]
                        if parent is not None and (
                            not isinstance(parent, str) or not tool_id.fullmatch(parent)
                        ):
                            raise ValueError
                        record["parent_tool_use_id"] = parent
                else:
                    continue
                value = event.get("session_id")
                if isinstance(value, str) and session_id.fullmatch(value):
                    record["session_id"] = value
                has_lineage = "session_id" in record and "parent_tool_use_id" in record
                if (
                    kind == "assistant"
                    and not has_lineage
                    and any(block["name"] == review_tool for block in kept)
                ):
                    raise ValueError
                if kind == "user":
                    for block in kept:
                        returned = block["tool_use_id"]
                        if returned in submission_launches and (
                            not has_lineage
                            or len(kept) != 1
                            or returned in returned_tools
                        ):
                            raise ValueError
                        returned_tools.add(returned)
                if kind in ("assistant", "user") and has_lineage:
                    lineage = (record["session_id"], record["parent_tool_use_id"])
                    if kind == "assistant":
                        for block in kept:
                            if (
                                block["id"] in native_launches
                                or block["id"] in submission_launches
                            ):
                                raise ValueError
                            if block["name"] == review_tool:
                                if (
                                    block["id"] in returned_tools
                                    or native_launches.get(lineage[1])
                                    != (lineage[0], None)
                                    or lineage[1] in native_completions
                                ):
                                    raise ValueError
                                submission_launches[block["id"]] = (
                                    lineage,
                                    block.get("review"),
                                )
                            else:
                                native_launches[block["id"]] = lineage
                    elif (
                        len(kept) == 1 and kept[0]["tool_use_id"] in submission_launches
                    ):
                        submission = kept[0]["tool_use_id"]
                        expected, review = submission_launches[submission]
                        if lineage != expected or lineage[1] in native_completions:
                            raise ValueError
                        if not kept[0]["is_error"]:
                            if lineage[1] in submitted_reviews:
                                raise ValueError
                            submitted_reviews[lineage[1]] = review
                            if review is not None:
                                kept[0]["review_submission"] = review
                    elif (
                        len(kept) == 1
                        and native_launches.get(kept[0]["tool_use_id"]) == lineage
                    ):
                        if kept[0]["tool_use_id"] in native_completions:
                            raise ValueError
                        native = event.get("tool_use_result")
                        if (
                            isinstance(native, dict)
                            and native.get("status") == "completed"
                            and not kept[0]["is_error"]
                        ):
                            agent = native.get("agentId")
                            if not isinstance(agent, str) or not re.fullmatch(
                                r"a[0-9a-f]{16}", agent
                            ):
                                raise ValueError
                            kept[0]["native_agent"] = {
                                "id": agent,
                                "status": "completed",
                            }
                            review = submitted_reviews.get(kept[0]["tool_use_id"])
                            kept[0]["review_parse"] = (
                                "accepted"
                                if review is not None
                                else "missing-submission"
                            )
                            if review is not None:
                                kept[0]["native_agent"]["review"] = review
                            native_completions.add(kept[0]["tool_use_id"])
                encoded = json.dumps(record, allow_nan=False).encode()
                if probe and probe.encode() in encoded:
                    raise ValueError
                target.write(encoded + b"\n")
            except (ValueError, TypeError, OverflowError, RecursionError):
                failed = True
    finally:
        if failed:
            target.write(b'{"type":"result","subtype":"error","is_error":true}\n')


def supervise(
    argv: list[str],
    *,
    sandbox: str,
    timeout_sec: int,
    stream_json: Path,
    stderr_log: Path,
    metadata_only: bool = False,
    probe: str = "",
) -> Supervised:
    """Run *argv* under the bound, capturing its streams to runner-owned files.

    The files are opened by this process on purpose: the sandbox writes through
    the inherited fds regardless of who owns them, so the run's record cannot be
    rewritten from the far side of the boundary.

    stdin is closed explicitly. A backgrounded command got ``/dev/null`` for
    free; this one is in the foreground and would inherit the step's, which
    costs ``claude -p`` a three-second wait for input that never comes and feeds
    it whatever does arrive.

    Overrunning the bound asks the agent to stop before making it: a TERM to the
    sandbox uid, :data:`TERM_GRACE_SEC` to flush, then the KILL. Without the
    grace the agent's session JSONL loses its tail mid-write, and that file is
    what the token accounting falls back on when a run produces no result event
    — the runs that time out are exactly the ones with no result event.

    The KILL is this function's ``finally`` and the only unconditional step: it
    is what actually stops a run the TERM did not, so no path out of here,
    exception included, may skip it. :func:`raise_on_cancel` is what makes
    "every path" include a cancelled job, which arrives as a signal rather than
    as anything Python would raise on its own.

    The KILL is followed by a wait on the child, so the step does not return
    while ``sudo`` and the agent under it are still alive: the steps after this
    one hand the workspace back and read the run's files, and a survivor would
    race them.
    """
    start = time.monotonic()
    agent: subprocess.Popen[bytes] | None = None
    capture: threading.Thread | None = None
    captured = []
    try:
        with (
            raise_on_cancel(),
            stream_json.open("wb") as out,
            open(os.devnull, "wb") if metadata_only else stderr_log.open("wb") as err,
        ):
            agent = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if metadata_only else out,
                stderr=err,
            )
            if metadata_only:
                assert agent.stdout is not None

                def collect() -> None:
                    try:
                        capture_metadata(agent.stdout, out, probe=probe)
                    except OSError:
                        # Never quote a capture exception that may contain input.
                        return
                    captured.append(True)

                capture = threading.Thread(target=collect)
                capture.start()
            try:
                returncode = agent.wait(timeout_sec)
                # Shell convention: 128 + N when signal N killed the child.
                code = 128 - returncode if returncode < 0 else returncode
            except subprocess.TimeoutExpired:
                # The bound decides, however the stop then goes: an agent that
                # takes the TERM and one that has to be killed are the same run
                # to a maintainer, and the code it leaves says nothing.
                code = None
                signal_sandbox("TERM", sandbox)
                # `sudo` exits once its child does, so this observes the agent.
                with contextlib.suppress(subprocess.TimeoutExpired):
                    agent.wait(TERM_GRACE_SEC)
            finally:
                # Reap before joining so no surviving child can hold the pipe open.
                if capture is not None:
                    signal_sandbox("KILL", sandbox)
                    agent.wait()
                    capture.join()
                    agent.stdout.close()
                    if not captured:
                        raise RuntimeError("Runtime metadata capture failed")
    finally:
        signal_sandbox("KILL", sandbox)
        if agent is not None:
            agent.wait()
    return Supervised(code, round(time.monotonic() - start))


def stream_events(stream_json: Path) -> Iterator[dict[str, Any]]:
    """The run's events, streamed. A stream that was never written has none.

    Never a list: a session runs for hours with every tool result in this file,
    and each consumer below needs only one pass. Call it again for a second.
    """
    if not stream_json.exists():
        return iter(())
    return _common.read_ndjson(stream_json)


def _assistant_blocks(events: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """The content blocks of the assistant events, whatever else the stream holds.

    A truncated or synthetic event can carry a null ``message`` or a
    non-list ``content``; the verdict is the last thing that may die on one, so
    a shape this does not recognise contributes nothing rather than raising.
    """
    for event in events:
        if event.get("type") != "assistant":
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            yield from (block for block in content if isinstance(block, dict))


def failure_reason(events: Iterable[dict[str, Any]]) -> str:
    """The last non-blank assistant text, capped at :data:`REASON_MAX_CHARS`.

    A session-limit exit is non-zero and emits a ``<synthetic>`` assistant
    message ("You've hit your session limit · resets 8:30am (UTC)"), so the last
    assistant text names the cause; ``enrich-tend-outage-issues.sh`` carries the
    annotation into the tend-outage issue. A ``tool_use`` block is not text and
    a tool name is not a failure cause, so only ``text`` blocks are considered,
    and a blank one is no reason at all.

    The cap is here rather than at the annotation because this is where the
    agent's own text enters: the block that names the cause is the same block
    that can be the agent's whole closing answer, and everything downstream
    quotes what this returns.
    """
    reason = ""
    for block in _assistant_blocks(events):
        if block.get("type") != "text":
            continue
        text = str(block.get("text", "")).strip()
        if text:
            reason = text
    if len(reason) > REASON_MAX_CHARS:
        return reason[:REASON_MAX_CHARS] + "…"
    return reason


def turn_outcome(events: Iterable[dict[str, Any]]) -> str | None:
    """Why the turn failed despite a zero exit, or None if it succeeded."""
    # A turn can emit more than one; the last is the turn's outcome.
    last: dict[str, Any] | None = None
    for event in events:
        if event.get("type") == "result":
            last = event
    if last is None:
        return "produced no result event — the turn did not complete"
    subtype = last.get("subtype")
    if last.get("is_error") is True or subtype != "success":
        named = "unknown" if subtype is None else subtype
        return (
            f"turn ended in failure ({named}) — "
            "rate limit, auth, max turns, or server error"
        )
    return None


def transcript(events: Iterable[dict[str, Any]], limit: int) -> list[str]:
    """A readable transcript — assistant text and tool calls — at most *limit* lines.

    The cap is applied while accumulating, not after: one event's text can carry
    thousands of lines, and a long session's whole stream must never be held
    just to throw most of it away.
    """
    lines: list[str] = []
    for block in _assistant_blocks(events):
        if block.get("type") == "text":
            lines.extend(str(block.get("text", "")).split("\n"))
        elif block.get("type") == "tool_use":
            # `ensure_ascii=False` because the jq this replaced emitted raw
            # UTF-8. Escaping spends six of the 200 characters on every
            # non-ASCII one, so a non-English tool input renders as `\u….`
            rendered = json.dumps(
                block.get("input"), separators=(",", ":"), ensure_ascii=False
            )[:200]
            lines.extend(f"→ {block.get('name')}: {rendered}".split("\n"))
        if len(lines) >= limit:
            return lines[:limit]
    return lines


def stderr_tail(stderr_log: Path) -> list[str]:
    """The agent's last words, quoted on every failure.

    Read from the end and split on newlines alone: the agent decides both how
    much it writes here and what is in it, and this runs on the path where the
    annotation matters most — so neither an unbounded log nor a vertical tab in
    it may cost the diagnostic.
    """
    if not stderr_log.exists():
        return []
    with stderr_log.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - STDERR_TAIL_BYTES))
        chunk = handle.read()
    lines = chunk.decode("utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines[-STDERR_TAIL_LINES:]


def _quote_stderr(stderr_log: Path) -> None:
    """Print the agent's last words where they cannot issue workflow commands.

    The annotation above them needs no such bracket: the reason is flattened to
    one line and embedded mid-line rather than starting one.
    """
    with _common.stop_commands():
        for line in stderr_tail(stderr_log):
            print(line, flush=True)


def verdict(
    *,
    claude_exit: int | None,
    stream_json: Path,
    stderr_log: Path,
    timeout_sec: str,
    show_full_output: str,
    metadata_only: bool = False,
) -> int:
    """The step's exit code, with the annotation and job summary that explain it.

    ``claude_exit`` is None exactly when the bound killed the run.
    """
    if show_full_output == "true" and not metadata_only:
        lines = transcript(stream_events(stream_json), TRANSCRIPT_MAX_LINES)
        if lines:
            _common.append_summary(
                "## Claude transcript\n\n```\n" + "\n".join(lines) + "\n```"
            )

    if claude_exit is None:
        return _common.fail(f"Claude headless run exceeded {timeout_sec}s timeout")

    if claude_exit:
        reason = "" if metadata_only else failure_reason(stream_events(stream_json))
        named = f": {reason}" if reason else ""
        artifact = "runtime metadata" if metadata_only else "session-logs artifact"
        _common.annotate(
            "error",
            f"claude -p exited non-zero (exit={claude_exit}){named}"
            f" — see the {artifact}",
        )
        if not metadata_only:
            _quote_stderr(stderr_log)
        return claude_exit

    why = turn_outcome(stream_events(stream_json))
    if why is None:
        return 0
    _common.annotate("error", f"claude -p {why}")
    if not metadata_only:
        _quote_stderr(stderr_log)
    return 1


def main() -> int:
    mode = os.environ.get("TEND_METADATA_ONLY", "false")
    if mode not in ("true", "false"):
        raise SystemExit("TEND_METADATA_ONLY must be true or false")
    metadata_only = mode == "true"
    env = _common.require_env(
        "SANDBOX",
        "AGENT_ENV_FILE",
        "RUNNER_TEMP",
        "GITHUB_WORKSPACE",
        "GITHUB_OUTPUT",
        "TEND_MODEL",
        "TEND_ALLOWED_TOOLS",
        "TEND_SYSTEM_PROMPT",
        "TEND_PROMPT",
        "TEND_TIMEOUT_SEC",
        "SHOW_FULL_OUTPUT",
        "BOT_NAME",
        "BOT_ID",
        "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
    )
    sandbox = env["SANDBOX"]
    workspace = Path(env["GITHUB_WORKSPACE"])
    probe = ""
    if probe_file := os.environ.get("TEND_METADATA_PROBE_FILE", ""):
        try:
            path = (workspace / probe_file).resolve()
            if (
                not metadata_only
                or not path.is_relative_to(workspace.resolve())
                or not path.is_file()
            ):
                raise ValueError
            with path.open("rb") as source:
                raw = source.read(66)
            if not re.fullmatch(rb"[0-9a-f]{64}\n?", raw):
                raise ValueError
            probe = raw.decode("ascii").strip()
        except (OSError, ValueError, RuntimeError):
            raise SystemExit("Invalid synthetic metadata probe configuration") from None
    stream_json = Path(env["RUNNER_TEMP"]) / "tend-stream.json"
    stderr_log = Path(env["RUNNER_TEMP"]) / "tend-claude-stderr.log"

    # Written as the sandbox user so the agent can read it back. It lands in the
    # adopter's checkout untracked, next to the `.claude/skills/` they do track;
    # setup-sandbox.sh's global gitignore for the sandbox user keeps a broad
    # `git add -A` from committing `bypassPermissions` into the session's PR.
    # `stdin` is closed on every `sudo` here: without a tty a `sudo` that needs
    # a password fails instead of waiting for one on the step's stdin. The `tee`
    # gets the same guarantee from `input=`, which binds its stdin to a pipe.
    subprocess.run(
        ["sudo", "-u", sandbox, "mkdir", "-p", str(workspace / ".claude")],
        stdin=subprocess.DEVNULL,
        check=True,
    )
    subprocess.run(
        ["sudo", "-u", sandbox, "tee", str(workspace / ".claude/settings.local.json")],
        input=json.dumps(settings(env["TEND_ALLOWED_TOOLS"])),
        stdout=subprocess.DEVNULL,
        text=True,
        check=True,
    )

    # The agent's launch env is $AGENT_ENV_FILE (written by setup-sandbox.sh;
    # shared with the plugin-install step so the two can't drift): proxy
    # routing, CA trust for every client family, and DUMMY GitHub + Anthropic
    # credentials in the production schemes — the proxy replaces them with the
    # real secrets for their hosts. Non-allowlisted traffic tunnels through
    # untouched. No real secret is in this env.
    argv = launch_argv(
        sandbox=sandbox,
        agent_env_file=env["AGENT_ENV_FILE"],
        model=env["TEND_MODEL"],
        allowed_tools=env["TEND_ALLOWED_TOOLS"],
        system_prompt=env["TEND_SYSTEM_PROMPT"],
        prompt=env["TEND_PROMPT"],
        subprocess_env_scrub=env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"],
        bot_name=env["BOT_NAME"],
        bot_id=env["BOT_ID"],
        ci=os.environ.get("CI") or "true",
        settings_file=os.environ.get("TEND_AUTO_MEMORY_SETTINGS", ""),
        metadata_only=metadata_only,
    )
    # Published before the launch: the path does not depend on the run, and the
    # steps that read it — Token usage, the session-logs artifact — are
    # `if: always()`, so they must still find it when the launch itself blows up.
    _common.set_output("stream_json", str(stream_json))

    try:
        run = supervise(
            argv,
            sandbox=sandbox,
            timeout_sec=int(env["TEND_TIMEOUT_SEC"]),
            stream_json=stream_json,
            stderr_log=stderr_log,
            metadata_only=metadata_only,
            probe=probe,
        )
    finally:
        # supervise() kills and reaps the sandbox uid on every exit, including
        # cancellation and launch failure. The memory save keys on this output
        # so it never reads agent-owned files while a sandbox process survives.
        _common.set_output("sandbox_reaped", "true")
    timed_out = run.exit_code is None
    print(
        f"Supervisor: status={'timeout' if timed_out else 'exited'} "
        f"elapsed={run.elapsed}s "
        f"claude_exit={'none' if timed_out else run.exit_code}",
        flush=True,
    )

    return verdict(
        claude_exit=run.exit_code,
        stream_json=stream_json,
        stderr_log=stderr_log,
        timeout_sec=env["TEND_TIMEOUT_SEC"],
        show_full_output=env["SHOW_FULL_OUTPUT"],
        metadata_only=metadata_only,
    )


if __name__ == "__main__":
    _common.run(main)
