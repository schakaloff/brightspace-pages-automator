"""The native picker must reach forum topics and the Surveys entry under More."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,tool,activity_type,events", [
    ("DISCUSS", "Discussions", 6, ["forum", "activity"]),
    ("SURVEY", "Surveys", 12, ["lessons-more", "surveys", "activity"]),
])
async def test_real_picker_reaches_activity_without_clicking_navbar_more(kind, tool, activity_type, events):
    from playwright.async_api import async_playwright
    from content_checker import ContentChecker
    recorded = []
    async with async_playwright() as pw:
        if not Path(pw.chromium.executable_path).exists(): pytest.skip("Local Chromium is not installed")
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            await context.expose_binding("record", lambda source, event: recorded.append(event))
            main = f'''<button onclick="record('navbar-more')">More</button>
                <button onclick="record('sidebar-activity')">Exact activity</button>
                <button onclick="record('sidebar-forum')">Copied forum</button>
                <iframe src="/d2l/ui/apps/smart-curriculum/test.html"></iframe>
                <script>window.linked=false; window.fetch=async()=>({{ok:true,json:async()=>window.linked
                    ?[{{ActivityType:{activity_type},ToolItemId:95}}]:[]}});</script>'''
            cards = "<button onclick=\"record('lessons-more');chooser()\">More</button>" if kind == "SURVEY" else "<button onclick='chooser()'>Discussion</button>"
            forum_button = json.dumps('<button onclick="record(\'forum\');activity()">Copied forum</button>')
            survey_button = json.dumps('<button onclick="record(\'surveys\');activity()">Surveys</button>')
            activity_button = json.dumps('<button onclick="record(\'activity\');parent.parent.linked=true">Exact activity</button>')
            frame = f'''<d2l-button aria-label="Add Existing" onclick="cards()">Add Existing</d2l-button>
                <div id="picker"></div><script>
                function cards(){{document.querySelector('#picker').innerHTML={json.dumps(cards)};}}
                function chooser(){{document.querySelector('#picker').innerHTML='<iframe src="/d2l/lp/quicklinks/manage/2/CreateDialog"></iframe>';}}
                </script>'''
            first_choice = '<button onclick="activity();record(\'surveys\')">Surveys</button>' if kind == 'SURVEY' else '<button onclick="record(\'forum\');activity()">Copied forum</button>'
            chooser = f'''<div id="picker">{first_choice}</div><script>
                function forum(){{document.querySelector('#picker').innerHTML={forum_button};}}
                function surveys(){{document.querySelector('#picker').innerHTML={survey_button};}}
                function activity(){{document.querySelector('#picker').innerHTML={activity_button};}}
                </script>'''
            async def route(request):
                path = request.request.url
                await request.fulfill(content_type="text/html", body=frame if "/smart-curriculum/" in path else chooser if "/quicklinks/manage/" in path else main)
            await context.route("https://learn.okanagancollege.ca/**", route)
            checker = object.__new__(ContentChecker)
            ok, reason = await checker._attach_existing_activity(context, "https://learn.okanagancollege.ca", "2", {
                "target_module_id": 7, "activity_id": 95, "activity_kind": kind, "name": "Exact activity",
                "content_activity_type": activity_type, "tool_name": tool,
                "tool_singular": "Survey" if kind == "SURVEY" else "Discussion", "forum_name": "Copied forum"})
            assert ok, reason
            assert recorded == events
        finally:
            await browser.close()
