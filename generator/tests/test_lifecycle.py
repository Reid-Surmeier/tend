"""The fork's opt-in contract, exercised through the existing generator seam."""

import shlex

import click
import pytest
from tend.config import Config
from tend.workflows import generate_all

from tests import _yaml as yaml


def test_lifecycle_uses_pinned_fork_and_existing_security(tmp_path):
    path = tmp_path / "tend.yaml"
    path.write_text(
        "bot_name: test-bot\n"
        "action_source: example/tend@" + "a" * 40 + "\n"
        "workflows:\n  lifecycle:\n    enabled: true\n"
    )
    cfg = Config.load(path)
    workflows = {w.filename: w.content for w in generate_all(cfg)}
    data = yaml.safe_load(workflows["tend-lifecycle.yaml"])
    job = data["jobs"]["lifecycle"]
    assert job["environment"]["name"] == "tend"
    agent = next(s for s in job["steps"] if "/claude@" in s.get("uses", ""))
    assert agent["uses"] == "example/tend/claude@" + "a" * 40
    assert agent["with"]["prompt"].strip() == "/tend-ci-runner:lifecycle"
    assert agent["with"]["github_token"] == "${{ secrets.TEND_BOT_TOKEN }}"
    assert "workflow_dispatch" in data["on"]
    assert "issues" in data["on"]
    assert data["concurrency"]["cancel-in-progress"] is False
    assert "git+https://github.com/example/tend@" in workflows["tend-lifecycle.yaml"]


@pytest.mark.parametrize(
    "setting", ['"false"', "1", "null", "[]", "{enabled: 'false'}"]
)
def test_lifecycle_requires_explicit_boolean_opt_in(tmp_path, setting):
    path = tmp_path / "tend.yaml"
    path.write_text(
        "bot_name: test-bot\naction_source: example/tend@"
        + "a" * 40
        + "\nworkflows:\n  lifecycle: "
        + setting
        + "\n"
    )
    with pytest.raises(click.ClickException, match="lifecycle"):
        Config.load(path)


@pytest.mark.parametrize(
    "source",
    [
        "example/tend@main",
        "example/tend@123",
        "../tend@" + "a" * 40,
        "example/tend@" + "A" * 40,
        "https://github.com/example/tend@" + "a" * 40,
    ],
)
def test_fork_source_rejects_floating_or_malformed_references(tmp_path, source):
    path = tmp_path / "tend.yaml"
    path.write_text("bot_name: test-bot\naction_source: " + source + "\n")
    with pytest.raises(click.ClickException, match="action_source"):
        Config.load(path)


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_lifecycle_only_harness_and_fork_install_check(tmp_path, harness):
    from tend.config import STANDARD_WORKFLOWS

    path = tmp_path / "tend.yaml"
    path.write_text(
        "bot_name: test-bot\nharness: "
        + harness
        + "\naction_source: example/tend@"
        + "a" * 40
        + "\nworkflows:\n"
        + "".join(f"  {name}: false\n" for name in sorted(STANDARD_WORKFLOWS))
        + "  lifecycle: true\n"
    )
    cfg = Config.load(path)
    assert cfg.enabled_harnesses() == {harness}
    first = generate_all(cfg, with_install_test=True)
    assert first == generate_all(cfg, with_install_test=True)
    assert {w.filename for w in first} == {
        "tend-lifecycle.yaml",
        "tend-install-test.yaml",
    }
    data = yaml.safe_load(first[0].content)
    agent = next(
        s
        for s in data["jobs"]["lifecycle"]["steps"]
        if "@" in s.get("uses", "") and "example/tend" in s["uses"]
    )
    assert agent["uses"] == f"example/tend/{harness}@" + "a" * 40
    install = next(w.content for w in first if w.filename == "tend-install-test.yaml")
    install_data = yaml.safe_load(install)
    run = install_data["jobs"]["install-test"]["steps"][-1]["run"]
    command = next(
        line.strip() for line in run.splitlines() if line.strip().startswith("uvx ")
    )
    assert shlex.split(command) == [
        "uvx",
        "--from",
        "git+https://github.com/example/tend@" + "a" * 40 + "#subdirectory=generator",
        "tend",
        "init",
        "--with-install-test",
    ]
    assert 'uvx "tend@$TEND_VERSION" init --with-install-test' not in install


def test_lifecycle_cannot_use_upstream_runtime(tmp_path):
    path = tmp_path / "tend.yaml"
    path.write_text("bot_name: test-bot\nworkflows:\n  lifecycle: true\n")
    with pytest.raises(click.ClickException, match="requires action_source"):
        Config.load(path)
