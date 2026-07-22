"""Test-runner subagent handler — execute tests, analyze failures."""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from dendrophis.subagents.messages import SubagentRequest, SubagentResponse
from dendrophis.tools.builtins.filesystem import BashTool, ReadTool


class TestRunnerHandler:
    """Handler for test-runner subagent."""

    __test__ = False

    def __init__(
        self,
        bash_tool: BashTool | None = None,
        read_tool: ReadTool | None = None,
    ) -> None:
        self.bash_tool = bash_tool or BashTool()
        self.read_tool = read_tool or ReadTool()

    async def execute(self, request: SubagentRequest) -> SubagentResponse:
        """Execute test task."""
        command = request.payload.get("command", "pytest")
        target = request.payload.get("target", ".")
        options = request.payload.get("options", {})
        context = request.context

        try:
            # Build test command
            test_cmd = self._build_command(command, target, options)

            # Run tests
            result = await self.bash_tool.execute(
                command=test_cmd,
                description=f"Run {command} tests on {target}",
                timeout=options.get("timeout", 300000),  # 5 min default
            )

            # Parse results
            output = result.get("stdout", "") + "\n" + result.get("stderr", "")
            returncode = result.get("returncode", 1)

            if command == "pytest":
                parsed = self._parse_pytest_output(output, returncode)
            elif command == "unittest":
                parsed = self._parse_unittest_output(output, returncode)
            else:
                parsed = self._parse_generic_output(output, returncode)

            # Categorize failures if we have changed files context
            if context.get("changed_files"):
                parsed["failures"] = self._categorize_failures(
                    parsed.get("failures", []),
                    context["changed_files"],
                )

            # Run coverage if requested
            if options.get("coverage"):
                coverage = await self._run_coverage(command, target)
                parsed["coverage"] = coverage

            # Generate recommendations
            parsed["recommendations"] = self._generate_recommendations(parsed)

            status = "success" if parsed.get("summary", {}).get("failed", 0) == 0 else "failure"

            return SubagentResponse(
                agent="test-runner",
                task_id=request.task_id,
                status=status,
                result=parsed,
            )

        except Exception as e:
            return SubagentResponse(
                agent="test-runner",
                task_id=request.task_id,
                status="failure",
                result={"error": str(e), "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0}},
            )

    def _build_command(self, command: str, target: str, options: dict[str, Any]) -> str:
        """Build test command with options."""
        parts = [command]

        if options.get("verbose"):
            parts.append("-v")

        if command == "pytest":
            # JSON output for parsing if available
            parts.append("--tb=short")
            if options.get("parallel"):
                parts.append("-n auto")
            if options.get("coverage"):
                parts.append("--cov")

        elif command == "unittest":
            parts.append("-v" if options.get("verbose") else "")

        parts.append(target)
        return " ".join(p for p in parts if p)

    def _parse_pytest_output(self, output: str, returncode: int) -> dict[str, Any]:
        """Parse pytest output."""
        summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0}
        failures = []

        # Parse summary line
        # Example: "5 passed, 2 failed, 1 skipped in 0.45s" or "1 passed, 1 failed, 1 error, 1 skipped in 0.01s"
        # Match each count independently
        passed_match = re.search(r"(\d+) passed", output)
        failed_match = re.search(r"(\d+) failed", output)
        skipped_match = re.search(r"(\d+) skipped", output)
        error_match = re.search(r"(\d+) error", output)

        summary["passed"] = int(passed_match.group(1)) if passed_match else 0
        summary["failed"] = int(failed_match.group(1)) if failed_match else 0
        summary["skipped"] = int(skipped_match.group(1)) if skipped_match else 0
        summary["error"] = int(error_match.group(1)) if error_match else 0
        summary["total"] = sum(summary.values()) - summary["total"]  # exclude total itself

        # Parse failure details
        # Look for FAILED sections
        failure_pattern = r"FAILED\s+([\w/\.]+::\w+)\s*-\s*(.+?)(?=\n\n|\nFAILED|\Z)"
        for match in re.finditer(failure_pattern, output, re.DOTALL):
            test_name = match.group(1)
            error_text = match.group(2).strip()

            # Extract file and line
            location = self._extract_location(error_text)

            failures.append(
                {
                    "test": test_name,
                    "error": error_text[:500],  # Truncate long errors
                    "location": location,
                    "category": "unknown",
                }
            )

        # Parse errors (setup/teardown failures)
        error_pattern = r"ERROR\s+([\w/\.]+)\s*-\s*(.+?)(?=\n\n|\nERROR|\Z)"
        failures.extend(
            [
                {
                    "test": match.group(1),
                    "error": match.group(2).strip()[:500],
                    "location": self._extract_location(match.group(2)),
                    "category": "env",
                }
                for match in re.finditer(error_pattern, output, re.DOTALL)
            ]
        )

        return {
            "summary": summary,
            "failures": failures,
            "raw_output": output[-5000:] if len(output) > 5000 else output,  # Last 5k chars
            "artifacts": [],
        }

    def _parse_unittest_output(self, output: str, returncode: int) -> dict[str, Any]:
        """Parse unittest output."""
        summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0}
        failures = []

        # Parse summary line
        # Example: "Ran 5 tests in 0.45s"
        ran_match = re.search(r"Ran (\d+) tests? in", output)
        if ran_match:
            summary["total"] = int(ran_match.group(1))

        # Parse OK/FAIL
        if "OK" in output:
            summary["passed"] = summary["total"]
        elif "FAIL" in output or returncode != 0:
            # Count failures
            fail_match = re.search(r"failures=(\d+)", output)
            error_match = re.search(r"errors=(\d+)", output)
            summary["failed"] = int(fail_match.group(1)) if fail_match else 0
            summary["error"] = int(error_match.group(1)) if error_match else 0
            summary["passed"] = summary["total"] - summary["failed"] - summary["error"]

        # Parse failure details
        # Look for traceback sections
        fail_pattern = r"(FAIL|ERROR):\s+(\w+)\s*\((\w+)\).*?\n(-+\n)(.+?)(?=\n\n\n|=+)"
        for match in re.finditer(fail_pattern, output, re.DOTALL):
            test_method = match.group(2)
            test_class = match.group(3)
            error_text = match.group(5).strip()

            failures.append(
                {
                    "test": f"{test_class}.{test_method}",
                    "error": error_text[:500],
                    "location": self._extract_location(error_text),
                    "category": "unknown",
                }
            )

        return {
            "summary": summary,
            "failures": failures,
            "raw_output": output[-5000:] if len(output) > 5000 else output,
            "artifacts": [],
        }

    def _parse_generic_output(self, output: str, returncode: int) -> dict[str, Any]:
        """Parse generic test output."""
        return {
            "summary": {
                "total": 0,
                "passed": 0 if returncode != 0 else 1,
                "failed": 1 if returncode != 0 else 0,
                "skipped": 0,
                "error": 0,
            },
            "failures": [],
            "raw_output": output[-5000:] if len(output) > 5000 else output,
            "artifacts": [],
        }

    def _extract_location(self, error_text: str) -> str:
        """Extract file:line from error text."""
        # Look for File "...", line N
        match = re.search(r'File "([^"]+)", line (\d+)', error_text)
        if match:
            return f"{match.group(1)}:{match.group(2)}"
        return ""

    def _categorize_failures(self, failures: list[dict[str, Any]], changed_files: list[str]) -> list[dict[str, Any]]:
        """Categorize failures based on changed files and error patterns."""
        changed_set = set(changed_files)

        for failure in failures:
            error = failure.get("error", "").lower()
            location = failure.get("location", "")

            # Check if failure is in changed file
            in_changed = any(cf in location for cf in changed_set)

            # Check for environment issues
            if any(x in error for x in ["connection", "timeout", "permission", "not found", "module"]):
                failure["category"] = "env"
            # Check for flaky indicators
            elif any(x in error for x in ["race", "deadlock", "timeout", "temporary"]):
                failure["category"] = "flaky"
            # New failure in changed code
            elif in_changed:
                failure["category"] = "new"
            # Regression in unchanged code
            else:
                failure["category"] = "regression"

        return failures

    async def _run_coverage(self, command: str, target: str) -> dict[str, Any]:
        """Run coverage analysis."""
        try:
            result = await self.bash_tool.execute(
                command="coverage report --format=json",
                description="Get coverage report",
            )

            # Try to parse JSON output
            try:
                cov_data = json.loads(result.get("stdout", "{}"))
                return {
                    "overall": cov_data.get("totals", {}).get("percent_covered", 0.0),
                    "by_file": {f: d.get("percent_covered", 0.0) for f, d in cov_data.get("files", {}).items()},
                }
            except json.JSONDecodeError:
                # Fallback: parse text output
                return self._parse_coverage_text(result.get("stdout", ""))

        except Exception as e:
            return {"overall": 0.0, "by_file": {}, "error": str(e)}

    def _parse_coverage_text(self, output: str) -> dict[str, Any]:
        """Parse text coverage output."""
        overall = 0.0
        by_file = {}

        # Look for total line
        total_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
        if total_match:
            overall = float(total_match.group(1))

        # Parse file lines
        for line in output.split("\n"):
            parts = line.split()
            if len(parts) >= 4 and parts[0].endswith(".py"):
                with contextlib.suppress(ValueError):
                    by_file[parts[0]] = float(parts[-1].rstrip("%"))

        return {"overall": overall, "by_file": by_file}

    def _generate_recommendations(self, parsed: dict[str, Any]) -> list[str]:
        """Generate recommendations based on test results."""
        recommendations = []
        summary = parsed.get("summary", {})
        failures = parsed.get("failures", [])

        total = summary.get("total", 0)
        failed = summary.get("failed", 0)
        errors = summary.get("error", 0)

        if total == 0:
            recommendations.append("No tests were run. Check test discovery and target path.")
            return recommendations

        if failed == 0 and errors == 0:
            recommendations.append("All tests passed. No action needed.")
            return recommendations

        # Categorize failures
        categories = {}
        for f in failures:
            cat = f.get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1

        if categories.get("env", 0) > 0:
            recommendations.append(
                f"Found {categories['env']} environment-related failure(s). "
                "Check dependencies, permissions, and network."
            )

        if categories.get("flaky", 0) > 0:
            recommendations.append(
                f"Found {categories['flaky']} potentially flaky test(s). "
                "Consider adding retries or investigating race conditions."
            )

        if categories.get("new", 0) > 0:
            recommendations.append(f"Found {categories['new']} new failure(s) in changed code. Review recent changes.")

        if categories.get("regression", 0) > 0:
            recommendations.append(
                f"Found {categories['regression']} regression(s) in unchanged code. Check for side effects."
            )

        if failed > 0 and not recommendations:
            recommendations.append(f"{failed} test(s) failed. Review failure details above.")

        return recommendations
