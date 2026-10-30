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
if __name__=='__main__':
    from alisa_cli.updates import UpdateScheduled
    try:raise SystemExit(main())
    except UpdateScheduled as update:
        import os
        if sys.platform=='win32':
            from alisa_cli.state import write_private
            try:
                write_private(os.environ['ALISA_WINDOWS_HANDOFF'], {'schema':1,
                    'executable':update.executable, 'arguments':update.arguments,
                    'directory':update.directory, 'relaunch':update.relaunch})
            except (OSError,KeyError,ValueError):
                print('Для обновления в Windows запустите установленный alisa.cmd.',file=sys.stderr)
                raise SystemExit(1)
            raise SystemExit(75)
        os.chdir(update.directory)
        os.execv(update.executable,update.arguments)
