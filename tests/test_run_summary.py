import sys

sys.path.insert(0, "src")

from accessibility_checker import AccessibilityReport, AccessibilityViolation
from run_summary import RestyleRunSummary


def test_summary_records_pages_accessibility_and_usage():
    summary = RestyleRunSummary(started_at=0)
    summary.pages_selected = 2
    summary.record_page(True)
    summary.record_page(False)
    summary.record_accessibility(
        AccessibilityReport(
            violations=(
                AccessibilityViolation(
                    rule_id="image-alt",
                    impact="critical",
                    help="Images need alternative text",
                    help_url="",
                    targets=("img", "img.second"),
                ),
            ),
            needs_review=1,
            passed_rules=10,
        )
    )
    messages = []

    summary.log(
        lambda message, tag: messages.append((message, tag)),
        {"input_tokens": 100, "output_tokens": 20, "cost_cad": 0.04},
    )
    text = "\n".join(message for message, _tag in messages)

    assert "Pages selected:       2" in text
    assert "Pages changed:        1" in text
    assert "Pages needing review: 1" in text
    assert "1 violation(s)" in text
    assert "Estimated AI cost:    $0.0400 CAD" in text
