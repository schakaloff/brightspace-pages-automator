"""Quiz workspace using Transfer Studio's authenticated loopback launcher."""
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
from urllib.parse import urlsplit, parse_qs, unquote, quote
import uuid
import zipfile

from transfer_studio import TransferStudio, _Handler as LocalHandler
from quiz_workspace import QuizWorkspace
from quiz_repair import MAX_PACKAGE


class QuizStudio(TransferStudio):
    def __init__(self, data_dir, request_file):
        super().__init__(data_dir, lambda request: None)
        self.workspace = QuizWorkspace(data_dir)
        self.request_file = request_file
        self.files = {'candidate': '', 'reference': '', 'pictures': ''}
        self.pending_file = ''
        self.busy = False
        self.notice = ''
        self.error = ''
        self.download_path = None
        self.download_id = ''

    def snapshot(self):
        with self._lock:
            return {'files': self.files, 'pending_file': self.pending_file, 'notice': self.notice,
                    'error': self.error, 'busy': self.busy, 'revision': self.revision, 'workspace': self.workspace.snapshot(),
                    'files_changed': bool(self.workspace.rows) and any(self.files.get(k, '') != self.workspace.paths.get(k, '') for k in ('candidate', 'reference', 'pictures')),
                    'download_url': '/api/download?id=' + self.download_id if self.download_path else ''}

    def receive_upload(self, field, name, stream, length, revision, prefix=b''):
        if field not in ('candidate', 'reference', 'pictures', 'draft'):
            raise ValueError('Choose a course, original course, pictures, or saved review.')
        if not name or len(name) > 180 or any(c in name for c in '<>:"/\\|?*\x00\r\n') or name in ('.', '..'):
            raise ValueError('The file name is not supported. Rename the file and try again.')
        reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{base}{n}' for base in ('COM', 'LPT') for n in range(1, 10)}
        if Path(name).stem.split('.')[0].upper() in reserved:
            raise ValueError('Rename the file before adding it.')
        suffixes = ('.zip', '.mbz') if field == 'reference' else ('.json',) if field == 'draft' else ('.zip',)
        if not name.lower().endswith(suffixes):
            raise ValueError('Choose ' + ('a saved review JSON file.' if field == 'draft' else 'a ZIP or Moodle backup.' if field == 'reference' else 'a ZIP file.'))
        limit = 32 * 1024 * 1024 if field == 'draft' else MAX_PACKAGE
        if not 0 < length <= limit:
            raise ValueError('The file is empty or too large. For a large course, export only Quizzes and Question Library.')
        with self._lock:
            if self.busy or self.pending_file:
                raise ValueError('Wait for the current file to finish.')
            if revision != self.revision:
                raise ValueError('The file selection changed in another tab. Reload this page and try again.')
            self.busy = True
        folder = self.data_dir / 'uploads' / uuid.uuid4().hex
        part, destination = folder / 'upload.part', folder / name
        adopted = False
        try:
            folder.mkdir(parents=True)
            remaining = length - len(prefix)
            if remaining < 0:
                raise ValueError('The file size did not match the upload.')
            with part.open('xb') as target:
                target.write(prefix)
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError('The file copy was interrupted. Add the file again.')
                    target.write(chunk)
                    remaining -= len(chunk)
            os.replace(part, destination)
            if field != 'draft' and not zipfile.is_zipfile(destination):
                if field != 'reference' or not tarfile.is_tarfile(destination):
                    raise ValueError('This is not a valid course ZIP or Moodle backup.')
            with self._lock:
                if field == 'draft':
                    fresh = QuizWorkspace(self.data_dir)
                    fresh.restore(destination)
                    self.workspace = fresh
                    self.files = {'candidate': '', 'reference': '', 'pictures': '', **fresh.paths}
                    self.notice = 'Saved review opened. Your choices are ready to continue.'
                else:
                    self.files[field] = str(destination)
                    self.notice = name + ' added on this computer.'
                self.download_path = None
                self.download_id = ''
                self.error = ''
                self.revision += 1
                adopted = True
        finally:
            part.unlink(missing_ok=True)
            if not adopted:
                destination.unlink(missing_ok=True)
                if folder.exists():
                    folder.rmdir()
            with self._lock:
                self.busy = False

    def export_for_browser(self):
        if any(self.files.get(k, '') != self.workspace.paths.get(k, '') for k in ('candidate', 'reference', 'pictures')):
            raise ValueError('The selected files changed. Click Start checking before downloading.')
        self.busy = True
        try:
            folder = self.data_dir / 'exports' / uuid.uuid4().hex
            folder.mkdir(parents=True)
            name = Path(self.workspace.paths.get('candidate', 'quizzes.zip')).stem + '_FIXED.zip'
            saved = self.workspace.export(folder / name)
            self.download_path = Path(saved)
            self.download_id = uuid.uuid4().hex
            self.notice = 'Fixed quizzes are ready. Download them with your browser.'
        finally:
            self.busy = False

    def choose(self, field):
        if field not in ('candidate', 'reference', 'pictures', 'draft', 'output'):
            raise ValueError('Choose a supported file field.')
        if self.pending_file or self.busy:
            raise ValueError('Finish the current file selection first.')
        self.pending_file = field
        self.error = ''
        self.request_file(field)

    def picked(self, field, path):
        with self._lock:
            self.pending_file = ''
            self.error = ''
            try:
                if not path:
                    return
                if field in self.files:
                    self.files[field] = path
                    self.revision += 1
                elif field == 'draft':
                    self.busy = True
                    self.workspace.restore(path)
                    self.files = dict(self.workspace.paths)
                    self.files.setdefault('reference', '')
                    self.files.setdefault('pictures', '')
                    self.notice = 'Saved review reopened.'
                    self.revision += 1
                elif field == 'output':
                    self.busy = True
                    saved = self.workspace.export(path)
                    self.notice = 'Candidate ZIP saved to ' + saved + '. Review its appearance and grading in Brightspace before using it in the real course.'
            except Exception as exc:
                self.error = str(exc)
            finally:
                self.busy = False

    def load(self):
        self.busy = True
        try:
            fresh = QuizWorkspace(self.data_dir)
            fresh.load(**self.files)
            self.workspace = fresh
            self.download_path = None
            self.download_id = ''
            self.notice = 'Exports opened. Choose a question to compare its stored versions.'
            self.error = ''
        finally:
            self.busy = False


class QuizHandler(LocalHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        ok, cookie = self._authorized(allow_launch=path == '/')
        if not ok:
            return
        studio = self.server.studio
        try:
            if path == '/api/state':
                self._send(200, studio.snapshot())
            elif path in ('/api/download', '/api/review-file'):
                with studio._lock:
                    if path == '/api/download':
                        token = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
                        if not studio.download_path or token != studio.download_id:
                            raise ValueError('Prepare the fixed ZIP before downloading it.')
                        self._send_file(studio.download_path, studio.download_path.name, 'application/zip')
                    elif studio.workspace.draft_path:
                        self._send_file(studio.workspace.draft_path, 'saved-quiz-review.quiz-review.json', 'application/json')
                    else:
                        raise ValueError('Open a course before saving a review.')
            elif path == '/api/question':
                key = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
                with studio._lock:
                    self._send(200, studio.workspace.detail(key))
            elif path == '/api/preview':
                query = parse_qs(urlsplit(self.path).query)
                key = query.get('id', [''])[0]
                variant = query.get('variant', [None])[0]
                with studio._lock:
                    self._send(200, studio.workspace.preview(key, variant), 'text/html; charset=utf-8', preview=True)
            else:
                assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                          '/quiz.css': ('quiz.css', 'text/css; charset=utf-8'),
                          '/quiz.js': ('quiz.js', 'text/javascript; charset=utf-8')}
                if path == '/HankenGrotesk.ttf':
                    from transfer_studio import asset_directory
                    self._send(200, (asset_directory() / 'HankenGrotesk.ttf').read_bytes(), 'font/ttf')
                    return
                if path not in assets:
                    self._send(404, {'error': 'Not found.'})
                    return
                root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
                name, mime = assets[path]
                self._send(200, (root / 'assets/quiz-studio' / name).read_bytes(), mime, cookie)
        except ConnectionError:
            return
        except (KeyError, ValueError, StopIteration, OSError) as exc:
            self._send(400, {'error': str(exc) or 'Choose a question from this review.'})

    def _send_file(self, path, name, content_type):
        studio = self.server.studio
        if studio.busy:
            raise ValueError('Wait for the current file to finish.')
        studio.busy = True
        try:
            with Path(path).open('rb') as stream:
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(name))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
                self.end_headers()
                shutil.copyfileobj(stream, self.wfile, 1024 * 1024)
        finally:
            studio.busy = False

    def _upload(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_PACKAGE or self.headers.get('Transfer-Encoding'):
                self.close_connection = True
                raise ValueError('Choose a file up to 4 GB. A quiz-only export is usually much smaller.')
            # Bound the pre-authentication read; no file is written before authorization.
            prefix = self.rfile.read(min(length, 65536))
            ok, _ = self._authorized()
            if not ok:
                self.close_connection = True
                return
            studio = self.server.studio
            if self.headers.get('Origin') != studio.origin or self.headers.get('X-Studio-Request') != '1':
                self.close_connection = True
                self._send(403, {'error': 'Open Quiz Repair from MTB Writer.'})
                return
            if self.headers.get_content_type() != 'application/octet-stream':
                raise ValueError('Choose a file using the browser file button or drop it into a file box.')
            field = parse_qs(urlsplit(self.path).query).get('field', [''])[0]
            studio.receive_upload(field, unquote(self.headers.get('X-Quiz-Filename', '')), self.rfile,
                                  length, int(self.headers.get('X-Quiz-Revision', '-1')), prefix)
            self._send(200, studio.snapshot())
        except ConnectionError:
            self.close_connection = True
            return
        except (ValueError, OSError, tarfile.TarError) as exc:
            self.close_connection = True
            self._send(400, {'error': str(exc)})
        except Exception:
            self.close_connection = True
            self._send(400, {'error': 'The file could not be opened. Check that it is a course export or saved quiz review.'})

    def do_POST(self):
        if urlsplit(self.path).path == '/api/upload':
            self._upload()
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8 * 1024 * 1024 or self.headers.get('Transfer-Encoding'):
                raise ValueError('The edit is too large.')
            raw = self.rfile.read(length)
            ok, _ = self._authorized()
            if not ok:
                return
            studio = self.server.studio
            if self.headers.get('Origin') != studio.origin or self.headers.get('X-Studio-Request') != '1':
                self._send(403, {'error': 'Open Quiz Repair from the desktop app.'})
                return
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('Send a JSON edit.')
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError('Send a JSON object.')
            with studio._lock:
                if studio.busy:
                    raise ValueError('Wait for the current operation to finish.')
                route = urlsplit(self.path).path
                if route in ('/api/load', '/api/edit', '/api/reset', '/api/clear', '/api/export'):
                    if studio.pending_file:
                        raise ValueError('Finish the file selection first.')
                    if body.get('revision') != studio.revision:
                        raise ValueError('This review changed in another tab. Reload the page before saving.')
                if route == '/api/choose':
                    studio.choose(body.get('field'))
                elif route == '/api/load':
                    studio.load()
                elif route == '/api/edit':
                    studio.workspace.edit(body.get('id'), body)
                    studio.notice = 'Question saved to your local review.'
                elif route == '/api/reset':
                    studio.workspace.reset(body.get('id'))
                    studio.notice = 'Question returned to the current exam copy.'
                elif route == '/api/export':
                    studio.export_for_browser()
                elif route == '/api/clear':
                    if body.get('field') not in ('reference', 'pictures'):
                        raise ValueError('Only optional files can be cleared.')
                    studio.files[body['field']] = ''
                    studio.notice = 'Optional file cleared. Open the review again to use the new selection.'
                else:
                    self._send(404, {'error': 'Not found.'})
                    return
                if route != '/api/choose':
                    studio.revision += 1
                if route in ('/api/load', '/api/edit', '/api/reset', '/api/clear'):
                    studio.download_path = None
                    studio.download_id = ''
                self._send(200, studio.snapshot())
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
            self._send(400, {'error': str(exc) or 'The request could not be completed.'})
        except Exception:
            self._send(500, {'error': 'Quiz Repair could not finish. Reopen the workspace and try again.'})


QuizStudio.handler_class = QuizHandler
