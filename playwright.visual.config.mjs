import { defineConfig } from '@playwright/test';

const installedChannel = process.env.BPA_VISUAL_CHANNEL
  || (process.platform === 'win32' ? 'msedge' : undefined);

export default defineConfig({
  testDir: './tests/visual',
  testMatch: '**/*.spec.mjs',
  fullyParallel: false,
  retries: 0,
  reporter: 'line',
  snapshotPathTemplate: '{testDir}/baselines/{platform}/{projectName}/{arg}{ext}',
  expect: {
    toHaveScreenshot: {
      animations: 'disabled',
      maxDiffPixelRatio: 0.003,
      threshold: 0.2,
    },
  },
  use: {
    browserName: 'chromium',
    channel: installedChannel,
    colorScheme: 'light',
    locale: 'en-CA',
    reducedMotion: 'reduce',
    serviceWorkers: 'block',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1200, height: 900 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 } } },
  ],
});
