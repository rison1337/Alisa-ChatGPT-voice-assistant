#!/usr/bin/env python3
import sys
from pathlib import Path
if sys.platform=='win32':
    sys.path.insert(0,str(Path(__file__).resolve().parent/'windows/vendor'))
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    from alisa_cli.windows import console_setup
    console_setup()
from alisa_cli.cli import main
if __name__=='__main__':raise SystemExit(main())
