"""Transfer scans retain missing pages without losing the rest of the course."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from transfer_api import TransferAPI


class Page:
    url = 'https://learn.example.test/d2l/home/1'

    def __init__(self, status=404):
        self.status = status

    async def evaluate(self, script, args=None):
        if args is None:
            return ['1.82']
        if len(args) == 2:
            if args[1] == '11':
                raise RuntimeError(f'Page.evaluate: Error: GET topic file {self.status}: Not Found')
            return '<p>Readable page.</p>'
        path, method, payload = args
        assert method == 'GET' and payload is None
        if '/courses/' in path:
            return {'Name': 'Example Sandbox', 'Path': '/content/enforced/1-sandbox/'}
        if path.endswith('/content/root/'):
            return [{'Id': 10, 'Type': 0, 'Title': 'Week one'}]
        if '/modules/10/structure/' in path:
            return copy.deepcopy([
                {'Id': 11, 'Type': 1, 'ActivityType': 1, 'TopicType': 1, 'Title': 'Missing reading',
                 'Url': '/content/enforced/1-sandbox/missing.html'},
                {'Id': 12, 'Type': 1, 'ActivityType': 1, 'TopicType': 1, 'Title': 'Good reading',
                 'Url': '/content/enforced/1-sandbox/good.html'},
            ])
        if path.endswith(('/quizzes/', '/surveys/')):
            return {'Objects': [], 'Next': None}
        if path.endswith('/grades/setup/'):
            return {'GradingSystem': 'Weighted'}
        return []


@pytest.mark.asyncio
async def test_scan_keeps_missing_page_and_reads_other_pages_without_any_writes():
    messages = []
    inventory = await TransferAPI(Page(), '1').inventory(lambda *args: messages.append(args))
    missing, good = inventory['modules'][0]['children']
    assert 'html' not in missing
    assert 'missing' in missing['html_error'].lower()
    assert good['html'] == '<p>Readable page.</p>'
    assert inventory['surveys'] == []
    assert any('Missing reading' in text and '11' in text and level == 'warning' for text, level in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [403, 500])
async def test_scan_does_not_treat_permission_or_server_errors_as_missing_files(status):
    with pytest.raises(ValueError, match='Example Sandbox.*Missing reading.*11'):
        await TransferAPI(Page(status), '1').inventory()
