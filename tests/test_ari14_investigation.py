"""Tests for ARI-14: Runaway Verification Worker Loop Investigation.

These tests verify the documentation, invariants, and findings from the
ARI-14 investigation into the runaway verification worker loop where
workers w43-w75 kept respawning for the same completed task.

The actual Klaw fix is in https://github.com/eastseymour/Klaw/pull/67.
These tests verify the *aristotlebot-slack* side: documentation, report
completeness, and architectural notes in CLAUDE.md.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# ── Paths ──

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "ari-14-runaway-worker-loop-investigation.md"
CLAUDE_MD = ROOT / "CLAUDE.md"
README_MD = ROOT / "README.md"


# ── Report Existence & Structure ──


class TestInvestigationReportExists:
    """The investigation report must exist and have required sections."""

    def test_report_file_exists(self) -> None:
        assert REPORT_PATH.exists(), (
            f"Investigation report missing: {REPORT_PATH}"
        )

    def test_report_is_nonempty(self) -> None:
        content = REPORT_PATH.read_text()
        assert len(content) > 500, "Report is too short to be meaningful"


class TestReportContent:
    """Verify the report documents all required findings."""

    @pytest.fixture(autouse=True)
    def _load_report(self) -> None:
        self.content = REPORT_PATH.read_text()

    def test_has_executive_summary(self) -> None:
        assert "Executive Summary" in self.content

    def test_has_root_cause_analysis(self) -> None:
        assert "Root Cause Analysis" in self.content

    def test_documents_worker_range(self) -> None:
        """Report should mention the w43-w75 worker range."""
        assert "w43" in self.content
        assert "w75" in self.content

    def test_documents_tracking_set_bug(self) -> None:
        """Report should explain the _linear_issues_with_workers tracking set bug."""
        assert "_linear_issues_with_workers" in self.content

    def test_documents_three_layer_dedup(self) -> None:
        """Report should document the three-layer dedup system."""
        assert "Three-Layer Deduplication" in self.content or \
               "three dedup mechanisms" in self.content.lower()

    def test_documents_fix_approach(self) -> None:
        """Report should document the fix (failure counter)."""
        assert "_failed_verification_attempts" in self.content
        assert "MAX_VERIFICATION_ATTEMPTS" in self.content

    def test_documents_klaw_pr(self) -> None:
        """Report should reference the Klaw PR."""
        assert "eastseymour/Klaw/pull/67" in self.content

    def test_documents_periodic_loops(self) -> None:
        """Report should document the periodic loops involved."""
        assert "_linear_task_check_loop" in self.content
        assert "_worker_monitor_loop" in self.content

    def test_documents_check_in_review(self) -> None:
        """Report should document _check_in_review_issues."""
        assert "_check_in_review_issues" in self.content

    def test_documents_discard_helper(self) -> None:
        """Report should document _discard_issue_keys_for_worker."""
        assert "_discard_issue_keys_for_worker" in self.content

    def test_documents_invariants(self) -> None:
        """Report should state the invariants enforced by the fix."""
        assert "Invariants" in self.content

    def test_documents_design_decision(self) -> None:
        """Report should explain the design decision to always discard."""
        assert "Always Discard" in self.content or \
               "always discard" in self.content

    def test_documents_redeployment(self) -> None:
        """Report should document the redeployment verification (Task 1)."""
        assert "Redeployment" in self.content or "redeployment" in self.content
        assert "558cebd" in self.content

    def test_documents_concurrency_limits(self) -> None:
        """Report should document concurrency limits."""
        assert "MAX_VERIFY" in self.content
        assert "MAX_AUTO_CREATE" in self.content

    def test_documents_worker_status_progression(self) -> None:
        """Report should document the Linear status progression."""
        assert "Todo" in self.content
        assert "In Progress" in self.content
        assert "In Review" in self.content
        assert "Done" in self.content


class TestReportInvariants:
    """Verify the report's internal consistency."""

    @pytest.fixture(autouse=True)
    def _load_report(self) -> None:
        self.content = REPORT_PATH.read_text()

    def test_max_verification_attempts_is_2(self) -> None:
        """The report should state MAX_VERIFICATION_ATTEMPTS = 2."""
        assert "MAX_VERIFICATION_ATTEMPTS" in self.content
        # Check the value is documented
        assert ": 2" in self.content or "= 2" in self.content or \
               "(default: 2)" in self.content

    def test_scan_interval_is_10_minutes(self) -> None:
        """The periodic scan runs every 10 minutes (600s)."""
        assert "10 min" in self.content or "600" in self.content

    def test_failure_counter_monotonic(self) -> None:
        """Report should state the failure counter is monotonically non-decreasing."""
        assert "monotonically non-decreasing" in self.content or \
               "only incremented" in self.content.lower()


# ── CLAUDE.md Documentation ──


class TestClaudeMdArchitectureNotes:
    """CLAUDE.md should contain Klaw worker architecture notes from ARI-14."""

    @pytest.fixture(autouse=True)
    def _load_claude_md(self) -> None:
        self.content = CLAUDE_MD.read_text()

    def test_claude_md_exists(self) -> None:
        assert CLAUDE_MD.exists()

    def test_has_klaw_worker_architecture_section(self) -> None:
        """CLAUDE.md should have a section about Klaw Worker Architecture."""
        assert "Klaw Worker Architecture" in self.content

    def test_documents_worker_status_progression(self) -> None:
        """Should show the Linear status progression."""
        assert "In Progress" in self.content
        assert "In Review" in self.content

    def test_documents_verification_workers(self) -> None:
        """Should explain verification workers."""
        assert "verification" in self.content.lower() or \
               "Verification" in self.content

    def test_documents_deduplication(self) -> None:
        """Should mention deduplication mechanism."""
        assert "dedup" in self.content.lower() or \
               "Dedup" in self.content or \
               "_linear_issues_with_workers" in self.content

    def test_documents_failure_tracking(self) -> None:
        """Should mention failure tracking or retry limits."""
        assert "_failed_verification_attempts" in self.content or \
               "MAX_VERIFICATION_ATTEMPTS" in self.content or \
               "failure tracking" in self.content.lower()

    def test_references_investigation_report(self) -> None:
        """Should reference the full investigation report."""
        assert "ari-14" in self.content.lower()


# ── README.md Documentation ──


class TestReadmeMdUpdates:
    """README.md should reference the ARI-14 investigation report."""

    @pytest.fixture(autouse=True)
    def _load_readme(self) -> None:
        self.content = README_MD.read_text()

    def test_readme_exists(self) -> None:
        assert README_MD.exists()

    def test_readme_references_ari14_report(self) -> None:
        """README should link to the ARI-14 investigation report."""
        assert "ari-14" in self.content.lower()


# ── Deployment Invariants ──


class TestDeploymentInvariants:
    """Critical project files and structure must be intact."""

    def test_reports_directory_exists(self) -> None:
        assert (ROOT / "reports").is_dir()

    def test_pyproject_toml_exists(self) -> None:
        assert (ROOT / "pyproject.toml").exists()

    def test_main_py_exists(self) -> None:
        assert (ROOT / "main.py").exists()

    def test_module_entry_point_exists(self) -> None:
        assert (ROOT / "src" / "aristotlebot" / "__main__.py").exists()

    def test_health_module_exists(self) -> None:
        assert (ROOT / "src" / "aristotlebot" / "health.py").exists()

    def test_gitignore_exists(self) -> None:
        assert (ROOT / ".gitignore").exists()

    def test_requirements_files_exist(self) -> None:
        assert (ROOT / "requirements.txt").exists()
        assert (ROOT / "requirements-dev.txt").exists()


# ── Bug Reproduction Logic (unit-level simulation) ──


class TestRunawayLoopSimulation:
    """Simulate the runaway verification worker loop to verify understanding.

    These tests model the Klaw controller's three-layer dedup logic and
    demonstrate how the bug manifested and how the fix prevents it.
    """

    def _make_controller_state(self) -> dict:
        """Create a minimal model of the Klaw controller's tracking state.

        Invariants:
        - _linear_issues_with_workers: set of issue keys with active workers
        - _failed_verification_attempts: dict of issue key -> failure count
        - MAX_VERIFICATION_ATTEMPTS: int >= 1
        """
        return {
            "tracking_set": set(),
            "failed_attempts": {},
            "MAX_VERIFICATION_ATTEMPTS": 2,
            "running_workers": set(),  # worker_ids currently RUNNING
            "workers": {},  # worker_id -> {"task": str, "state": str}
        }

    def _get_running_issue_keys(self, state: dict, issue_key: str) -> set:
        """Simulate _get_running_issue_keys."""
        keys = set(state["tracking_set"])
        for wid, w in state["workers"].items():
            if w["state"] in ("RUNNING", "CREATING"):
                if issue_key in w["task"]:
                    keys.add(issue_key)
        return keys

    def _should_spawn_verification(
        self, state: dict, issue_key: str,
    ) -> bool:
        """Simulate _check_in_review_issues gating logic (FIXED version)."""
        running = self._get_running_issue_keys(state, issue_key)
        if issue_key in running:
            return False
        attempts = state["failed_attempts"].get(issue_key, 0)
        if attempts >= state["MAX_VERIFICATION_ATTEMPTS"]:
            return False
        return True

    def _on_verification_success(
        self, state: dict, issue_key: str, worker_id: str,
    ) -> None:
        """Simulate successful verification completion."""
        state["tracking_set"].discard(issue_key)
        state["failed_attempts"].pop(issue_key, None)
        state["workers"][worker_id]["state"] = "COMPLETED"

    def _on_verification_failure(
        self, state: dict, issue_key: str, worker_id: str,
    ) -> None:
        """Simulate failed verification completion (FIXED version)."""
        attempts = state["failed_attempts"].get(issue_key, 0) + 1
        state["failed_attempts"][issue_key] = attempts
        state["tracking_set"].discard(issue_key)
        state["workers"][worker_id]["state"] = "FAILED"

    def _on_verification_failure_buggy(
        self, state: dict, issue_key: str, worker_id: str,
    ) -> None:
        """Simulate the BUGGY version (no failure counter)."""
        state["tracking_set"].discard(issue_key)
        state["workers"][worker_id]["state"] = "FAILED"

    def test_buggy_version_allows_infinite_respawn(self) -> None:
        """Demonstrate the bug: without failure counter, workers respawn forever."""
        state = self._make_controller_state()
        issue = "ARI-13"
        spawned = 0

        for scan in range(10):  # 10 periodic scans
            # Check if we should spawn (buggy: no failure counter check)
            running = self._get_running_issue_keys(state, issue)
            if issue not in running:
                # Spawn verification worker
                wid = f"w{43 + spawned}"
                state["workers"][wid] = {
                    "task": f"VERIFICATION TASK for {issue}",
                    "state": "RUNNING",
                }
                state["tracking_set"].add(issue)
                spawned += 1

                # Worker fails immediately
                self._on_verification_failure_buggy(state, issue, wid)
                # Issue is now NOT in tracking set, NOT in running workers
                # Next scan will spawn again!

        assert spawned == 10, (
            "Bug demonstration: all 10 scans spawned a new worker"
        )

    def test_fixed_version_caps_at_max_attempts(self) -> None:
        """Demonstrate the fix: failure counter caps respawns at MAX."""
        state = self._make_controller_state()
        issue = "ARI-13"
        spawned = 0

        for scan in range(10):  # 10 periodic scans
            if self._should_spawn_verification(state, issue):
                wid = f"w{43 + spawned}"
                state["workers"][wid] = {
                    "task": f"VERIFICATION TASK for {issue}",
                    "state": "RUNNING",
                }
                state["tracking_set"].add(issue)
                spawned += 1

                # Worker fails immediately
                self._on_verification_failure(state, issue, wid)

        # Should only spawn MAX_VERIFICATION_ATTEMPTS times
        assert spawned == state["MAX_VERIFICATION_ATTEMPTS"], (
            f"Expected {state['MAX_VERIFICATION_ATTEMPTS']} spawns, got {spawned}"
        )

    def test_success_resets_failure_counter(self) -> None:
        """After success, the failure counter resets for future cycles."""
        state = self._make_controller_state()
        issue = "ARI-13"

        # Fail once
        state["workers"]["w1"] = {"task": f"VERIFICATION TASK for {issue}", "state": "RUNNING"}
        state["tracking_set"].add(issue)
        self._on_verification_failure(state, issue, "w1")
        assert state["failed_attempts"][issue] == 1

        # Succeed
        state["workers"]["w2"] = {"task": f"VERIFICATION TASK for {issue}", "state": "RUNNING"}
        state["tracking_set"].add(issue)
        self._on_verification_success(state, issue, "w2")
        assert issue not in state["failed_attempts"]

        # Can spawn again (counter was reset)
        assert self._should_spawn_verification(state, issue)

    def test_running_worker_blocks_spawn(self) -> None:
        """While a worker is RUNNING, no new worker should be spawned."""
        state = self._make_controller_state()
        issue = "ARI-13"

        state["workers"]["w1"] = {"task": f"VERIFICATION TASK for {issue}", "state": "RUNNING"}
        state["tracking_set"].add(issue)

        assert not self._should_spawn_verification(state, issue)

    def test_failure_counter_is_monotonically_nondecreasing(self) -> None:
        """Failure counter only goes up (never decremented, only reset on success)."""
        state = self._make_controller_state()
        issue = "ARI-13"
        prev_count = 0

        for i in range(5):
            wid = f"w{i}"
            state["workers"][wid] = {"task": f"VERIFICATION TASK for {issue}", "state": "RUNNING"}
            state["tracking_set"].add(issue)
            self._on_verification_failure(state, issue, wid)
            current = state["failed_attempts"][issue]
            assert current >= prev_count, (
                f"Counter went backwards: {prev_count} -> {current}"
            )
            prev_count = current

    def test_max_verification_attempts_must_be_positive(self) -> None:
        """MAX_VERIFICATION_ATTEMPTS invariant: must be >= 1."""
        state = self._make_controller_state()
        assert state["MAX_VERIFICATION_ATTEMPTS"] >= 1
