from resource_restyle import mark_resource_directory, remove_generated_resource_markers


def test_removes_old_badges_from_resource_rows_without_changing_links():
    source = (
        '<p class="resource-row"><span class="resource-badge">PDF</span>'
        '<a class="resource-link" href="/content/enforced/file.pdf">Course outline</a>'
        '<span class="resource-meta">PDF document</span></p>'
        '<p class="link-row"><span class="resource-badge">LINK</span>'
        '<a class="link-title" href="https://example.org">Example</a></p>'
    )

    result, removed = remove_generated_resource_markers(source)

    assert removed == 2
    assert "resource-badge" not in result
    assert 'href="/content/enforced/file.pdf"' in result
    assert 'href="https://example.org"' in result
    assert "Course outline" in result
    assert "PDF document" in result


def test_preserves_authored_text_and_unrecognized_badges():
    source = (
        '<p>Bring the PDF document to class.</p>'
        '<p class="resource-row"><span class="resource-badge">Important</span>'
        '<a href="file.pdf">Instructions</a></p>'
        '<p><span class="resource-badge">PDF</span> is a file format.</p>'
    )

    assert remove_generated_resource_markers(source) == (source, 0)


def test_removes_only_decorative_icons_and_is_idempotent():
    source = (
        '<p class="resource-row"><span class="resource-icon" aria-hidden="true">'
        '<i class="bi bi-file-earmark-pdf"></i></span><a href="file.pdf">File</a></p>'
        '<p class="resource-row"><span class="resource-icon">An authored note</span>'
        '<a href="notes.html">Notes</a></p>'
    )

    result, removed = remove_generated_resource_markers(source)

    assert removed == 1
    assert "bi-file-earmark-pdf" not in result
    assert "An authored note" in result
    assert remove_generated_resource_markers(result) == (result, 0)


def test_marks_resource_only_page_for_compact_spacing():
    source = (
        '<div class="main-container"><main class="content-body">'
        '<h2>Files</h2><p class="resource-row"><a href="file.pdf">Case study</a></p>'
        '</main></div>'
    )

    result, marked = mark_resource_directory(source)

    assert marked
    assert 'class="main-container resource-directory"' in result
    assert mark_resource_directory(result) == (result, True)


def test_does_not_compact_mixed_reading_page():
    source = (
        '<div class="main-container resource-directory"><main class="content-body">'
        '<p>' + ("This week's reading and discussion topic. " * 8) + '</p>'
        '<h2>Resources</h2><p class="link-row"><a href="https://example.org">Read</a></p>'
        '</main></div>'
    )

    result, marked = mark_resource_directory(source)

    assert not marked
    assert "resource-directory" not in result
