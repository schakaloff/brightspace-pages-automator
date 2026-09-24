import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const FIXTURES = path.join(HERE, 'fixtures');
const REFERENCE = fs.readFileSync(path.join(ROOT, 'templates', 'style_reference.html'), 'utf8');

function referenceHead() {
  const match = REFERENCE.match(/<head>([\s\S]*?)<\/head>/i);
  if (!match) throw new Error('style_reference.html has no <head>');
  // Remote font/icon styles make screenshots dependent on network timing.
  return match[1].replace(/<link\b[^>]*>/gi, '');
}

function fixtureDocument(fragment) {
  return `<!doctype html><html lang="en"><head>${referenceHead()}
    <style>*,*::before,*::after{animation:none!important;transition:none!important}</style>
    </head><body>${fragment}</body></html>`;
}

const fixtures = fs.readdirSync(FIXTURES)
  .filter(name => name.endsWith('.html'))
  .sort();

for (const filename of fixtures) {
  const name = filename.replace(/\.html$/, '');
  test(name, async ({ page }) => {
    const fragment = fs.readFileSync(path.join(FIXTURES, filename), 'utf8');
    await page.setContent(fixtureDocument(fragment), { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => document.fonts?.ready);
    await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: true });
  });
}
