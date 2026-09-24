"""Run axe-core against generated Brightspace HTML previews.

The audit is advisory: accessibility findings are written to the run summary,
but they never silently rewrite content and never block the existing save flow.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


def axe_script_path() -> Path:
    root = (
        Path(sys._MEIPASS)
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent
    )
    return root / "assets" / "axe-core" / "axe.min.js"


@dataclass(frozen=True)
class AccessibilityViolation:
    rule_id: str
    impact: str
    help: str
    help_url: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class AccessibilityReport:
    violations: tuple[AccessibilityViolation, ...]
    needs_review: int
    passed_rules: int

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def affected_elements(self) -> int:
        return sum(len(item.targets) for item in self.violations)

    @property
    def serious_or_critical(self) -> int:
        return sum(item.impact in {"serious", "critical"} for item in self.violations)


def _target_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(part) for part in value)
    return str(value or "")


def parse_axe_result(result: dict[str, Any]) -> AccessibilityReport:
    violations = []
    for item in result.get("violations", []):
        targets = tuple(
            _target_text(node.get("target", ""))
            for node in item.get("nodes", [])
            if _target_text(node.get("target", ""))
        )
        violations.append(
            AccessibilityViolation(
                rule_id=str(item.get("id", "unknown")),
                impact=str(item.get("impact") or "unknown"),
                help=str(item.get("help") or item.get("description") or "Accessibility issue"),
                help_url=str(item.get("helpUrl") or ""),
                targets=targets,
            )
        )
    return AccessibilityReport(
        violations=tuple(violations),
        needs_review=len(result.get("incomplete", [])),
        passed_rules=len(result.get("passes", [])),
    )


async def scan_html(context, html: str, *, script_path: Path | None = None) -> AccessibilityReport:
    """Render ``html`` in an isolated tab and return axe-core findings."""

    script = Path(script_path) if script_path else axe_script_path()
    if not script.is_file():
        raise FileNotFoundError(f"axe-core script is missing: {script}")

    page = await context.new_page()
    try:
        await page.set_content(html or "", wait_until="domcontentloaded")
        await page.add_script_tag(path=str(script))
        raw = await page.evaluate(
            """async () => await axe.run(document, {
                resultTypes: ['violations', 'incomplete', 'passes']
            })"""
        )
        return parse_axe_result(raw)
    finally:
        await page.close()


def log_report(
    report: AccessibilityReport,
    log: Callable[[str, str], None],
    *,
    label: str = "Page",
) -> None:
    if not report.violations:
        suffix = f"; {report.needs_review} item(s) need manual review" if report.needs_review else ""
        log(f"♿ {label}: no automatic accessibility violations{suffix}", "success")
        return

    log(
        f"♿ {label}: {report.violation_count} accessibility rule violation(s) "
        f"affecting {report.affected_elements} element(s); "
        f"{report.needs_review} item(s) need manual review",
        "warning",
    )
    for violation in report.violations:
        target = f" — {violation.targets[0]}" if violation.targets else ""
        log(
            f"   [{violation.impact}] {violation.help} ({violation.rule_id}){target}",
            "detail",
        )
