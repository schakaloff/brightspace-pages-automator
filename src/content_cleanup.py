"""Remove selected topic links from a Brightspace course Content outline.

Only Content topic IDs are accepted. This module never calls Manage Files or
the APIs for assignments, quizzes, discussions, or other linked activities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


BRIGHTSPACE_HOST = "learn.okanagancollege.ca"


@dataclass(frozen=True)
class ContentTopic:
    id: str
    title: str
    module_id: str
    unit_path: str
    activity_type: str


@dataclass(frozen=True)
class RemovalResult:
    removed: tuple[ContentTopic, ...]
    failed: ContentTopic | None = None
    error: str = ""


class ChangedSelectionError(ValueError):
    """The preview no longer matches the live course outline."""


def course_id_from_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname != BRIGHTSPACE_HOST:
        raise ValueError(f"Enter a Brightspace course URL on {BRIGHTSPACE_HOST}.")
    match = re.search(r"/d2l/le/(?:lessons|content)/(\d+)(?:/|$)", parsed.path)
    course_id = match.group(1) if match else parse_qs(parsed.query).get("ou", [""])[0]
    if not course_id.isdigit():
        raise ValueError("The URL needs a Brightspace course ID.")
    return course_id


def _topic_from_entry(entry: dict) -> ContentTopic:
    if not isinstance(entry, dict) or not all(
        isinstance(entry.get(key), str) and entry[key]
        for key in ("id", "title", "module_id", "unit_path")
    ):
        raise ValueError("Brightspace returned an incomplete Content topic.")
    if not entry["id"].isdigit() or not entry["module_id"].isdigit():
        raise ValueError("Brightspace returned an invalid Content topic ID.")
    return ContentTopic(
        id=entry["id"], title=entry["title"],
        module_id=entry["module_id"], unit_path=entry["unit_path"],
        activity_type=str(entry.get("activity_type") or "Topic"),
    )


class BrowserContentCleanupAPI:
    """Authenticated browser adapter. All requests stay on Brightspace's origin."""

    def __init__(self, page, course_id: str):
        self.page = page
        self.course_id = course_id

    async def list_topics(self) -> list[ContentTopic]:
        entries = await self.page.evaluate(
            """async (courseId) => {
                async function getJson(path) {
                    const response = await fetch(path, {
                        credentials: 'include',
                        headers: { Accept: 'application/json' },
                        cache: 'no-store'
                    });
                    if (!response.ok) throw new Error(
                        `GET Content ${response.status}: ${(await response.text()).slice(0, 180)}`);
                    const type = response.headers.get('content-type') || '';
                    if (!type.toLowerCase().includes('json'))
                        throw new Error('Brightspace returned a login page instead of Content data');
                    const data = await response.json();
                    if (!Array.isArray(data)) throw new Error('Invalid Content structure');
                    return data;
                }
                const topics = [];
                const visited = new Set();
                async function walk(entries, path, parentId) {
                    for (const entry of entries) {
                        const id = String(entry.Id || '');
                        if (!/^\\d+$/.test(id)) throw new Error('Invalid Content ID');
                        if (entry.Type === 0) {
                            if (visited.has(id)) throw new Error('Repeated Content module');
                            visited.add(id);
                            const name = String(entry.Title || '(untitled unit)');
                            const children = await getJson(
                                `/d2l/api/le/1.75/${courseId}/content/modules/${id}/structure/`);
                            await walk(children, [...path, name], id);
                        } else if (entry.Type === 1) {
                            if (!parentId) throw new Error('Content topic has no parent unit');
                            topics.push({
                                id, title: String(entry.Title || '(untitled topic)'),
                                module_id: parentId, unit_path: path.join(' / '),
                                activity_type: String(entry.ActivityType || entry.TopicType || 'Topic')
                            });
                        } else {
                            throw new Error(`Unsupported Content object type ${entry.Type}`);
                        }
                    }
                }
                await walk(await getJson(`/d2l/api/le/1.75/${courseId}/content/root/`), [], null);
                return topics;
            }""",
            self.course_id,
        )
        topics = [_topic_from_entry(entry) for entry in entries]
        if len({topic.id for topic in topics}) != len(topics):
            raise ValueError("Brightspace returned duplicate topic IDs.")
        return topics

    async def delete_topic(self, topic_id: str) -> None:
        if not topic_id.isdigit():
            raise ValueError("Invalid Content topic ID.")
        await self.page.evaluate(
            """async ([courseId, topicId]) => {
                const xsrf = localStorage.getItem('XSRF.Token');
                if (!xsrf) throw new Error('No Brightspace security token; sign in again');
                const path = `/d2l/api/le/1.75/${courseId}/content/topics/${topicId}`;
                const response = await fetch(path,
                    { method: 'DELETE', credentials: 'include',
                      headers: { 'X-Csrf-Token': xsrf } });
                if (!response.ok) throw new Error(
                    `DELETE Content topic ${response.status}: ${(await response.text()).slice(0, 180)}`);
                if (new URL(response.url).pathname !== path)
                    throw new Error('Brightspace redirected the delete request');
                for (let attempt = 0; attempt < 3; attempt++) {
                    const check = await fetch(path, {
                        credentials: 'include', headers: { Accept: 'application/json' },
                        cache: 'no-store'
                    });
                    if (check.status === 404) return;
                    if (!check.ok) throw new Error(`Verify deleted topic ${check.status}`);
                    if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 300));
                }
                throw new Error('Brightspace still shows this topic after the delete request');
            }""",
            [self.course_id, topic_id],
        )

    async def get_topic_identity(self, topic_id: str) -> tuple[str, str, str]:
        """Recheck one topic without rescanning every module in the course."""
        return tuple(await self.page.evaluate(
            """async ([courseId, topicId]) => {
                const response = await fetch(
                    `/d2l/api/le/1.75/${courseId}/content/topics/${topicId}`,
                    { credentials: 'include', headers: { Accept: 'application/json' },
                      cache: 'no-store' });
                if (!response.ok) throw new Error(`GET topic ${response.status}`);
                const type = response.headers.get('content-type') || '';
                if (!type.toLowerCase().includes('json'))
                    throw new Error('Brightspace returned a login page instead of a topic');
                const topic = await response.json();
                return [String(topic.Id), String(topic.Title || '(untitled topic)'),
                        String(topic.ParentModuleId)];
            }""",
            [self.course_id, topic_id],
        ))


def validate_selection(selected: list[ContentTopic], current: list[ContentTopic]) -> None:
    if not selected:
        raise ValueError("Select at least one Content topic.")
    if len({item.id for item in selected}) != len(selected):
        raise ValueError("The selection contains duplicate topic IDs.")
    live = {item.id: item for item in current}
    for item in selected:
        if live.get(item.id) != item:
            raise ChangedSelectionError(
                f"The course changed since loading: {item.title}. Reload the outline and select again."
            )


async def remove_topics(api, selected: list[ContentTopic], progress=None) -> RemovalResult:
    """Validate the full batch first, then remove one topic at a time.

    Stop on the first error so the UI can report an exact partial result. Each
    topic is rechecked before its DELETE to catch moves or edits made mid-run.
    """
    validate_selection(selected, await api.list_topics())
    removed = []
    for item in selected:
        try:
            identity = await api.get_topic_identity(item.id)
            if identity != (item.id, item.title, item.module_id):
                raise ChangedSelectionError(
                    f"{item.title} changed during cleanup. Reload the outline before continuing."
                )
            await api.delete_topic(item.id)
        except Exception as exc:
            return RemovalResult(tuple(removed), item, str(exc))
        removed.append(item)
        if progress:
            progress(len(removed), len(selected), item)
    return RemovalResult(tuple(removed))


async def _with_browser(url: str, action, credentials: dict):
    from browser import launch_browser, wait_for_login

    course_id = course_id_from_url(url)
    p, browser, context, page = await launch_browser()
    try:
        await wait_for_login(
            page, context,
            credentials.get("bs_username") or None,
            credentials.get("bs_password") or None,
            credentials.get("sso_email") or None,
            credentials.get("sso_password") or None,
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if urlparse(page.url).hostname != BRIGHTSPACE_HOST:
            raise RuntimeError("Brightspace redirected away from the course. Sign in and retry.")
        return await action(BrowserContentCleanupAPI(page, course_id))
    finally:
        await context.close()
        await browser.close()
        await p.stop()


async def load_course_topics(url: str, credentials: dict) -> list[ContentTopic]:
    return await _with_browser(url, lambda api: api.list_topics(), credentials)


async def remove_course_topics(url: str, selected: list[ContentTopic],
                               credentials: dict, progress=None) -> RemovalResult:
    return await _with_browser(
        url, lambda api: remove_topics(api, selected, progress), credentials
    )
