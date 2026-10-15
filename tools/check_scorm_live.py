"""Read-only preparation using the same SCORM workflow as Media. Never adds content."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from scorm_runner import prepare_scorm


async def main():
    run, modules = await prepare_scorm(
        'https://learn.okanagancollege.ca/d2l/le/lessons/23840/units/994179',
        'https://mymoodle.okanagan.bc.ca/course/view.php?id=166972',
        lambda message, tag='info': print(message, flush=True))
    print('REVIEW:', [{k: row.get(k) for k in ('name', 'filename', 'module_title', 'state', 'error')}
                      for row in run.data['activities']], flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    asyncio.run(main())
