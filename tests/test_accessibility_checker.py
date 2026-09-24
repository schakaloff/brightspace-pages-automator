import sys

sys.path.insert(0, "src")

from accessibility_checker import log_report, parse_axe_result


def test_parse_axe_result_counts_rules_and_affected_elements():
    report = parse_axe_result(
        {
            "violations": [
                {
                    "id": "image-alt",
                    "impact": "critical",
                    "help": "Images must have alternative text",
                    "helpUrl": "https://example.test/image-alt",
                    "nodes": [
                        {"target": ["img:nth-child(1)"]},
                        {"target": ["img:nth-child(2)"]},
                    ],
                }
            ],
            "incomplete": [{"id": "color-contrast"}],
            "passes": [{"id": "document-title"}, {"id": "html-has-lang"}],
        }
    )

    assert report.violation_count == 1
    assert report.affected_elements == 2
    assert report.serious_or_critical == 1
    assert report.needs_review == 1
    assert report.passed_rules == 2


def test_log_report_keeps_rule_details_in_detail_mode():
    report = parse_axe_result(
        {
            "violations": [
                {
                    "id": "heading-order",
                    "impact": "moderate",
                    "help": "Heading levels should increase by one",
                    "nodes": [{"target": ["h4"]}],
                }
            ],
            "incomplete": [],
            "passes": [],
        }
    )
    messages = []

    log_report(report, lambda message, tag: messages.append((message, tag)), label="Week 2")

    assert messages[0][1] == "warning"
    assert "Week 2" in messages[0][0]
    assert messages[1][1] == "detail"
    assert "heading-order" in messages[1][0]
