from dataclasses import replace
import sys

import pytest

sys.path.insert(0, "src")

from link_review import LinkDraft, sample_links, selected_changes, valid_web_url


def test_missing_link_needs_replacement_even_when_only_opening_is_changed():
    missing = LinkDraft("missing", "Missing.pdf", "Page", "https://example.test/missing.pdf",
                        opening="new", selected=True)
    with pytest.raises(ValueError):
        selected_changes([missing])


def test_healthy_link_can_change_opening_without_replacing_its_url():
    healthy = LinkDraft("good", "Guide.pdf", "Page", "https://example.test/guide.pdf",
                        broken=False, opening="new", selected=True)
    assert selected_changes([healthy]) == [healthy]
    healthy.opening = "same"
    with pytest.raises(ValueError):
        selected_changes([healthy])  # Already opens in the same tab: no change.


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///secret.pdf", "https://", "relative.pdf",
                                  "https://user:pass@example.test/file.pdf", "https://[bad"])
def test_replacement_must_be_an_absolute_browser_url(url):
    assert not valid_web_url(url)
    draft = replace(sample_links()[0], replacement=url)
    with pytest.raises(ValueError):
        selected_changes([draft])


def test_only_checked_links_are_included_in_the_plan():
    links = sample_links()
    links[1].selected = False
    assert [link.id for link in selected_changes(links)] == ["notes"]
