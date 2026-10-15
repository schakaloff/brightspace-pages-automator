"""Human-readable summaries for Restyle runs."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


def _duration_text(seconds: float) -> str:
    seconds = max(0, round(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


@dataclass
class RestyleRunSummary:
    started_at: float = field(default_factory=time.monotonic)
    pages_selected: int = 0
    pages_attempted: int = 0
    pages_changed: int = 0
    pages_failed: int = 0
    accessibility_pages_checked: int = 0
    accessibility_violations: int = 0
    accessibility_affected_elements: int = 0
    accessibility_needs_review: int = 0
    accessibility_checks_unavailable: int = 0
    content_repairs: int = 0

    def record_page(self, success: bool) -> None:
        self.pages_attempted += 1
        if success:
            self.pages_changed += 1
        else:
            self.pages_failed += 1

    def record_usage(self, usage: dict | None) -> None:
        if usage and usage.get("content_retry"):
            self.content_repairs += 1

    def record_accessibility(self, report) -> None:
        self.accessibility_pages_checked += 1
        self.accessibility_violations += report.violation_count
        self.accessibility_affected_elements += report.affected_elements
        self.accessibility_needs_review += report.needs_review

    def log(
        self,
        logger: Callable[[str, str], None],
        token_usage: dict | None = None,
    ) -> None:
        elapsed = _duration_text(time.monotonic() - self.started_at)
        logger("", "dim")
        logger("RUN SUMMARY", "step")
        logger(f"Pages selected:       {self.pages_selected}", "info")
        logger(f"Pages changed:        {self.pages_changed}", "success")
        logger(
            f"Pages needing review: {self.pages_failed}",
            "warning" if self.pages_failed else "info",
        )
        if self.accessibility_pages_checked:
            logger(
                f"Accessibility:        {self.accessibility_violations} violation(s) "
                f"across {self.accessibility_pages_checked} checked page(s)",
                "warning" if self.accessibility_violations else "success",
            )
            logger(
                f"Affected elements:    {self.accessibility_affected_elements}",
                "detail",
            )
            logger(
                f"Needs manual review:  {self.accessibility_needs_review}",
                "detail",
            )
        if self.accessibility_checks_unavailable:
            logger(
                f"Accessibility skipped: {self.accessibility_checks_unavailable} page(s)",
                "warning",
            )
        if self.content_repairs:
            logger(
                f"Content repair retries: {self.content_repairs} of {self.pages_attempted} page(s)",
                "warning",
            )
        if token_usage and (token_usage.get("input_tokens") or token_usage.get("output_tokens")):
            logger(
                f"AI usage:             {token_usage.get('input_tokens', 0):,} tokens in / "
                f"{token_usage.get('output_tokens', 0):,} out",
                "info",
            )
            logger(f"Estimated AI cost:    ${token_usage.get('cost_cad', 0):.4f} CAD", "info")
        logger(f"Processing time:      {elapsed}", "info")
