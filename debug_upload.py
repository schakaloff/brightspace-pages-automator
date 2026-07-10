#!/usr/bin/env python3
"""
Minimal debug script to test file upload failure.
Skips comparison steps, goes straight to upload testing.
"""
import asyncio
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright

async def test_api_upload():
    """Test the D2L API upload method (currently failing)."""
    print("=" * 60)
    print("Testing D2L API Upload (broken method)")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # Navigate to Brightspace login
            print("1. Navigating to Brightspace...")
            await page.goto("https://learn.okanagancollege.ca/d2l/le/content/10309/home")

            # Wait for login (user will do this manually)
            print("2. Waiting for login... (please log in manually in the browser)")
            await page.wait_for_url("**/content/10309/**", timeout=120000)
            print("   ✓ Logged in")

            # Extract course ID
            course_id = "10309"

            # Create a test file
            test_content = b"Test file for upload"
            with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
                test_file = Path(f.name)
                f.write(test_content)

            print(f"\n3. Testing API upload for file: {test_file.name}")
            print(f"   Course ID: {course_id}")

            # Attempt API upload (this is where it fails)
            import base64
            b64 = base64.b64encode(test_content).decode()

            result = await page.evaluate("""async ([courseId, b64, filename, mimeType]) => {
                try {
                    console.log('API Upload - Starting');
                    const binary = atob(b64);
                    const bytes  = new Uint8Array(binary.length);
                    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                    const blob = new Blob([bytes], { type: mimeType });

                    const form = new FormData();
                    form.append('file', blob, filename);

                    console.log(`API Upload - POSTing to /d2l/api/le/1.0/${courseId}/managefiles/file/`);
                    const resp = await fetch(
                        `/d2l/api/le/1.0/${courseId}/managefiles/file/`,
                        { method: 'POST', body: form, credentials: 'include' }
                    );

                    console.log(`API Upload - Response status: ${resp.status}`);
                    const bodyText = await resp.text().catch(() => '');
                    return {
                        status: resp.status,
                        ok: resp.ok,
                        body: bodyText.slice(0, 500),
                        headers: Object.fromEntries(resp.headers.entries())
                    };
                } catch (e) {
                    console.error('API Upload - Exception:', e);
                    return { status: 0, ok: false, body: String(e), error: true };
                }
            }""", [course_id, b64, test_file.name, "text/plain"])

            print(f"\n4. API Response:")
            print(f"   Status: {result.get('status')}")
            print(f"   OK: {result.get('ok')}")
            print(f"   Body: {result.get('body', '')}")

            if result.get('error'):
                print(f"\n   ✗ ERROR: {result.get('body')}")
            elif not result.get('ok'):
                print(f"\n   ✗ FAILED: API returned {result.get('status')}")
                print(f"   Headers: {result.get('headers', {})}")
            else:
                print(f"\n   ✓ Upload succeeded!")

            # Keep browser open for inspection
            print("\n5. Keeping browser open. Press Ctrl+C to exit.")
            await asyncio.sleep(999999)

        except Exception as e:
            print(f"\n✗ Exception: {e}")
            await asyncio.sleep(5)
        finally:
            test_file.unlink(missing_ok=True)
            await browser.close()

if __name__ == "__main__":
    asyncio.run(test_api_upload())
