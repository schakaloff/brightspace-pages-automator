"""Keep the Moodle display name independent from package names and filenames."""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import shutil
import re
from tempfile import TemporaryDirectory
import zipfile
from urllib.parse import parse_qs, urlparse


def new_page_target(editor_url):
    parts = urlparse(editor_url)
    match = re.fullmatch(r'/d2l/le/lessons/(\d+)/edit/(\d+)/loadActivity/file/[^/]+/?', parts.path)
    if not match or parse_qs(parts.query).get('isNew') != ['true']:
        return None
    return match.groups()


def saved_page_id(editor_url, saved_url, existing_ids):
    target = new_page_target(editor_url)
    old, new = urlparse(editor_url), urlparse(saved_url)
    if not target or (old.scheme, old.netloc) != (new.scheme, new.netloc):
        return None
    match = re.fullmatch(rf'/d2l/le/lessons/{target[0]}/topics/(\d+)/?', new.path)
    if not match or match[1] in {str(id_) for id_ in existing_ids}:
        return None
    return match[1]


async def ensure_owned_page_title(api, topic_id, moodle_name):
    """Only repair the precise new page ID recorded by this transfer's save."""
    from content_checker import ContentChecker
    topics = await api.list_structure()
    if not any(str(t.get('Id')) == str(topic_id) for t in topics):
        raise RuntimeError('The saved H5P page is not in its recorded destination unit.')
    topic = await api.get_topic(topic_id)
    if str(topic.get('Id')) != str(topic_id):
        raise RuntimeError('Brightspace returned a different page while checking its name.')
    if topic.get('Title') != moodle_name:
        html = await api.get_topic_html(topic_id)
        if not ContentChecker._html_contains_h5p_embed(html):
            raise RuntimeError('The saved page could not be confirmed as H5P; its name was not changed.')
        topic = await api.rename_topic(topic_id, moodle_name)
    if topic.get('Title') != moodle_name:
        raise RuntimeError('The saved H5P page name does not match Moodle.')
    return topic


@contextmanager
def named_upload_package(source, moodle_name):
    """Name an upload copy; leave the downloaded package and activity content intact."""
    source = Path(source)
    if not moodle_name or not moodle_name.strip():
        raise ValueError('An H5P activity must have a Moodle name before upload.')
    with zipfile.ZipFile(source) as original:
        if original.namelist().count('h5p.json') != 1:
            raise ValueError('The H5P package must contain one h5p.json metadata file.')
        metadata = json.loads(original.read('h5p.json'))
        if metadata.get('title') == moodle_name:
            yield source
            return
        metadata['title'] = moodle_name
        with TemporaryDirectory(prefix='h5p-named-upload-') as directory:
            target = Path(directory) / source.name
            with zipfile.ZipFile(target, 'w') as renamed:
                for info in original.infolist():
                    copied_info = copy.copy(info)
                    if info.filename == 'h5p.json':
                        renamed.writestr(copied_info, json.dumps(metadata, ensure_ascii=False).encode('utf-8'))
                    else:
                        with original.open(info) as reader, renamed.open(copied_info, 'w') as writer:
                            shutil.copyfileobj(reader, writer)
            yield target
