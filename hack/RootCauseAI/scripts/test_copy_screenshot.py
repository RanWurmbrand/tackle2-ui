#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

_root_dir = Path(__file__).parent.parent
_artifacts_dir = _root_dir / 'artifacts'

project_path = os.getenv('PROJECT_PATH')
if not project_path:
    print('PROJECT_PATH not set')
    exit(1)
script = _root_dir / 'scripts' / 'copy_cypress_screenshot.sh'
try:
    result = subprocess.run(
        [str(script), project_path, str(_artifacts_dir)],
        capture_output=True, text=True, timeout=30
    )
    print(result.stdout or result.stderr or 'No output')
except (subprocess.TimeoutExpired, FileNotFoundError) as e:
    print(e)
