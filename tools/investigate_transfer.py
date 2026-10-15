"""Read-only tenant investigation. Never sends course mutations."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from browser import launch_browser, wait_for_login


async def main():
    import keyring
    config = json.loads(Path("user_config.json").read_text(encoding="utf-8"))
    username = config.get("bs_username", "")
    email = config.get("sso_email", "")
    p, browser, context, page = await launch_browser()
    try:
        await asyncio.wait_for(wait_for_login(page, context, username,
            keyring.get_password("BrightspacePagesAutomator", username) if username else None,
            email, keyring.get_password("BrightspacePagesAutomator_SSO", email) if email else None), 150)
        result = await page.evaluate("""async () => {
          const output = {};
          for (const id of ['25708','25707']) {
            const course = {};
            for (const [name, path] of Object.entries({
              details:`/d2l/api/lp/1.49/courses/${id}`,
              toc:`/d2l/api/le/1.82/${id}/content/toc`,
              assignments:`/d2l/api/le/1.82/${id}/dropbox/folders/`,
              rubrics:`/d2l/api/le/1.97/${id}/rubrics/`,
              associations:`/d2l/api/le/1.100/${id}/rubrics/associations/`,
              gradeSetup:`/d2l/api/le/1.82/${id}/grades/setup/`,
              categories:`/d2l/api/le/1.82/${id}/grades/categories/`,
              grades:`/d2l/api/le/1.89/${id}/grades/`
            })) {
              const r = await fetch(path,{credentials:'include',headers:{Accept:'application/json'}});
              const text = await r.text();
              try { course[name] = {status:r.status, data:JSON.parse(text)}; }
              catch { course[name] = {status:r.status, error:'Non-JSON response'}; }
            }
            output[id] = course;
          }
          return output;
        }""")
        destination = Path("transfer-investigation")
        destination.mkdir(exist_ok=True)
        (destination / "inventory.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        for course_id, course in result.items():
            print(course_id, {key: value["status"] for key, value in course.items()}, flush=True)
            print(json.dumps(course["details"], indent=2), flush=True)
            print("Modules:", [m.get("Title") for m in course["toc"].get("data", {}).get("Modules", [])], flush=True)
    finally:
        await browser.close()
        await p.stop()


if __name__ == "__main__":
    asyncio.run(main())
