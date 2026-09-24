# Visual regression tests

These screenshots protect the existing Brightspace page designs at desktop and
mobile widths. They run only during development; instructors do not wait for
them during a Restyle or Collect run.

Run the checks:

```powershell
pnpm visual:test
```

After intentionally approving a design change, regenerate the baselines:

```powershell
pnpm visual:update
```

On Windows the suite uses installed Microsoft Edge. On another platform,
install Playwright Chromium first or set `BPA_VISUAL_CHANNEL` to an installed
Chromium channel. Review every generated diff before updating a baseline.
