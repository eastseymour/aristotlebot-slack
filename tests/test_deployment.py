"""Tests for deployment artifacts and documentation.

These tests verify that the redeployment documentation and reports are
complete and consistent. They do not require live credentials or a
running service — they validate the deployment procedure specification.

Invariants:
- The redeployment procedure must include a `git checkout main` step
  (to prevent the branch-checkout bug discovered in ARI-13).
- The redeployment report must exist and document all verification steps.
- CLAUDE.md deployment section must match the documented procedure.
"""

import os
import pathlib

import pytest

# ── Paths ──────────────────────────────────────────────────────────────

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
CLAUDE_MD = PROJECT_ROOT / "CLAUDE.md"
README_MD = PROJECT_ROOT / "README.md"
REPORTS_DIR = PROJECT_ROOT / "reports"
REDEPLOY_REPORT = REPORTS_DIR / "ari-13-redeploy-report.md"


class TestDeploymentDocumentation:
    """Verify CLAUDE.md contains the required deployment procedure."""

    @pytest.fixture(autouse=True)
    def _load_docs(self) -> None:
        self.claude_md = CLAUDE_MD.read_text()
        self.readme_md = README_MD.read_text()

    def test_claude_md_exists(self) -> None:
        assert CLAUDE_MD.is_file(), "CLAUDE.md must exist in project root"

    def test_readme_md_exists(self) -> None:
        assert README_MD.is_file(), "README.md must exist in project root"

    def test_deployment_section_exists_in_claude_md(self) -> None:
        assert "Deployment" in self.claude_md, (
            "CLAUDE.md must contain a Deployment section"
        )

    def test_deployment_procedure_includes_checkout_main(self) -> None:
        """Invariant: procedure must include git checkout main.

        The ARI-13 branch-checkout bug occurred because the previous
        worker ran `git pull origin main` without first switching to
        main, causing the service to run feature-branch code.
        """
        assert "git checkout main" in self.claude_md, (
            "CLAUDE.md deployment procedure must include 'git checkout main' "
            "to prevent the branch-checkout bug (ARI-13)"
        )

    def test_deployment_procedure_includes_pip_install(self) -> None:
        assert "pip install -e" in self.claude_md, (
            "CLAUDE.md deployment procedure must include 'pip install -e .'"
        )

    def test_deployment_procedure_includes_systemctl_restart(self) -> None:
        assert "systemctl restart aristotlebot" in self.claude_md, (
            "CLAUDE.md must include systemctl restart command"
        )

    def test_deployment_procedure_includes_health_check(self) -> None:
        assert "localhost:8080/health" in self.claude_md, (
            "CLAUDE.md must include health check URL"
        )

    def test_deployment_procedure_includes_journal_check(self) -> None:
        assert "journalctl" in self.claude_md, (
            "CLAUDE.md must include journalctl log check"
        )

    def test_branch_checkout_warning_present(self) -> None:
        """The branch-checkout warning must be in the docs (ARI-13 lesson)."""
        assert "feature branch" in self.claude_md.lower() or "working directory" in self.claude_md.lower(), (
            "CLAUDE.md must warn about the service running from the working "
            "directory branch"
        )

    def test_readme_has_redeployment_steps(self) -> None:
        assert "Redeployment" in self.readme_md or "redeployment" in self.readme_md, (
            "README.md must include redeployment quick steps"
        )

    def test_readme_deployment_includes_checkout_main(self) -> None:
        """README redeployment steps must also include git checkout main."""
        assert "git checkout main" in self.readme_md, (
            "README.md redeployment steps must include 'git checkout main'"
        )


class TestRedeploymentReport:
    """Verify the redeployment report is complete and well-structured."""

    @pytest.fixture(autouse=True)
    def _load_report(self) -> None:
        if REDEPLOY_REPORT.is_file():
            self.report = REDEPLOY_REPORT.read_text()
        else:
            self.report = ""

    def test_report_exists(self) -> None:
        assert REDEPLOY_REPORT.is_file(), (
            f"Redeployment report must exist at {REDEPLOY_REPORT}"
        )

    def test_report_has_title(self) -> None:
        assert self.report.startswith("# ARI-13"), (
            "Report must start with ARI-13 title"
        )

    def test_report_documents_git_pull(self) -> None:
        assert "git pull" in self.report, (
            "Report must document git pull step"
        )

    def test_report_documents_pip_install(self) -> None:
        assert "pip install" in self.report, (
            "Report must document pip install step"
        )

    def test_report_documents_service_restart(self) -> None:
        assert "systemctl restart" in self.report or "restart" in self.report.lower(), (
            "Report must document service restart"
        )

    def test_report_documents_status_verification(self) -> None:
        assert "active (running)" in self.report, (
            "Report must confirm service is active (running)"
        )

    def test_report_documents_health_check(self) -> None:
        assert "health" in self.report.lower(), (
            "Report must document health endpoint check"
        )

    def test_report_documents_socket_mode(self) -> None:
        assert "socket_mode_connected" in self.report or "Socket Mode" in self.report, (
            "Report must confirm Socket Mode connection"
        )

    def test_report_documents_zero_startup_errors(self) -> None:
        assert "zero" in self.report.lower() and "error" in self.report.lower(), (
            "Report must confirm zero startup errors"
        )

    def test_report_documents_commit_hash(self) -> None:
        """Report must document which commit was deployed."""
        assert "558cebd" in self.report or "commit" in self.report.lower(), (
            "Report must document the deployed commit hash"
        )

    def test_report_documents_test_results(self) -> None:
        assert "passed" in self.report, (
            "Report must document test results"
        )

    def test_report_has_conclusion(self) -> None:
        assert "Conclusion" in self.report or "complete" in self.report.lower(), (
            "Report must have a conclusion section"
        )


class TestDeploymentInvariants:
    """Test invariants for the deployment setup."""

    def test_reports_directory_exists(self) -> None:
        assert REPORTS_DIR.is_dir(), "reports/ directory must exist"

    def test_pyproject_toml_exists(self) -> None:
        """Package must be installable via pip install -e ."""
        pyproject = PROJECT_ROOT / "pyproject.toml"
        assert pyproject.is_file(), (
            "pyproject.toml must exist for pip install -e . to work"
        )

    def test_main_py_exists(self) -> None:
        """The systemd service runs main.py."""
        main_py = PROJECT_ROOT / "main.py"
        assert main_py.is_file(), (
            "main.py must exist (systemd service entry point)"
        )

    def test_module_entry_point_exists(self) -> None:
        """python -m aristotlebot must work."""
        main_module = PROJECT_ROOT / "src" / "aristotlebot" / "__main__.py"
        assert main_module.is_file(), (
            "src/aristotlebot/__main__.py must exist for module invocation"
        )

    def test_health_module_exists(self) -> None:
        """Health check server must be present."""
        health = PROJECT_ROOT / "src" / "aristotlebot" / "health.py"
        assert health.is_file(), (
            "src/aristotlebot/health.py must exist (health check server)"
        )

    def test_requirements_files_exist(self) -> None:
        """Both requirements files must exist for deployment."""
        assert (PROJECT_ROOT / "requirements.txt").is_file()
        assert (PROJECT_ROOT / "requirements-dev.txt").is_file()

    def test_gitignore_exists(self) -> None:
        assert (PROJECT_ROOT / ".gitignore").is_file()
