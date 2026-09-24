import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const AXE_SOURCE = fs.readFileSync(
  path.join(ROOT, 'assets', 'axe-core', 'axe.min.js'),
  'utf8',
);

test('bundled axe-core reports inaccessible generated HTML', async ({ page }) => {
  await page.setContent(`<!doctype html>
    <html lang="en">
      <head><title>Accessibility fixture</title></head>
      <body><main><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></main></body>
    </html>`);
  await page.addScriptTag({ content: AXE_SOURCE });

  const results = await page.evaluate(async () => axe.run(document));
  expect(results.violations.map(item => item.id)).toContain('image-alt');
});
