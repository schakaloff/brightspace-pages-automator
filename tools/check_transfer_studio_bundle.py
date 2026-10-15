"""Check Studio in a built Windows app without using login or any live course."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
import http.client


def main():
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="studio-bundle-") as folder:
        output = Path(folder)
        startup = (output / "startup.log").open("wb")
        command = [str(executable), str(Path(__file__).resolve().parents[1] / "gui.py")] if executable.name.lower().startswith("python") else [str(executable)]
        process = subprocess.Popen([*command, "--studio-smoke", str(output)],
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "BPA_SIMPLE_PREVIEW": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            stdout=startup, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 50
            while not (output / "ready.json").exists():
                if process.poll() is not None: raise RuntimeError("Packaged smoke mode exited before starting Studio. " + (output / "startup.log").read_text(errors="replace")[-4000:])
                if time.monotonic() > deadline: raise TimeoutError("Packaged Studio did not start. " + (output / "startup.log").read_text(errors="replace")[-4000:])
                time.sleep(.1)
            url = urlsplit(json.loads((output / "ready.json").read_text(encoding="utf-8"))["url"])
            (output / "ready.json").unlink()
            cookie = None
            def request(path, body=None):
                nonlocal cookie
                connection = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
                headers = {"Cookie": cookie} if cookie else {}
                if body is not None: headers.update({"Origin": f"http://{url.netloc}", "Content-Type": "application/json", "X-Studio-Request": "1"})
                connection.request("POST" if body is not None else "GET", path, json.dumps(body) if body is not None else None, headers)
                response = connection.getresponse()
                if response.getheader("Set-Cookie"): cookie = response.getheader("Set-Cookie").split(";", 1)[0]
                result = response.read(); status = response.status; connection.close()
                assert status in (200,202), (path, status)
                return result
            request("/?" + url.query)
            for asset in ("/studio.js", "/studio-workflow.js", "/studio.css", "/HankenGrotesk.ttf"): assert request(asset)
            request("/api/scan", {"source_url": "https://learn.okanagancollege.ca/d2l/home/1", "target_url": "https://learn.okanagancollege.ca/d2l/home/2"})
            state = json.loads(request("/api/state"))
            assert state["plan"]["source"]["name"] == "Offline source"
            assert state["plan"]["looks"], "Built-in template assets missing"
            page = json.loads(request("/api/page?row=11"))
            assert page["ok"] and page["title"] == "Example page"
            assert b"Offline bundle check" in request("/api/preview?row=11&side=after")
            print(json.dumps({"result": "passed", "executable": str(executable), "checks": "Qt panel, HTTP session, scan, web assets, font, built-in looks, page preview"}))
        finally:
            (output / "stop").touch()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=5)
            startup.close()


if __name__ == "__main__": main()
