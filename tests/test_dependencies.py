"""Linux sign-in needs its import display even when the desktop uses WSLg."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import dependencies

class LoginDependencies(unittest.TestCase):
    def test_both_modes_install_the_session_import_display(self):
        managers={
            'pacman':({'xorg-server-xvfb','xorg-xauth'},'libpulse'),
            'apt-get':({'xvfb','xauth'},'pulseaudio-utils'),
            'dnf':({'xorg-x11-server-Xvfb','xorg-x11-xauth'},'pulseaudio-utils')}
        for manager,(display,audio) in managers.items():
            for mode in ('native','bridge'):
                with self.subTest(manager=manager,mode=mode),patch.object(dependencies.sys,'platform','linux'),patch.object(dependencies.shutil,'which',side_effect=lambda tool: '/usr/bin/'+tool if tool==manager else None):
                    argv=dependencies.command(mode)
                self.assertTrue(display.issubset(argv))
                self.assertEqual(audio in argv,mode=='bridge')
                self.assertEqual(len(argv),len(set(argv)))

    def test_bundled_display_is_accepted_but_xauth_is_still_required(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(dependencies,'ROOT',Path(folder)),patch.object(dependencies.sys,'platform','linux'),patch.object(dependencies.shutil,'which',return_value=None):
            self.assertEqual(dependencies.login_missing(),['Xvfb','xauth'])
            bundled=Path(folder)/'engines/bridge/display-tools/usr/bin/Xvfb'
            bundled.parent.mkdir(parents=True);bundled.touch()
            self.assertEqual(dependencies.login_missing(),['xauth'])

    def test_missing_display_reports_an_install_command_before_login(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(dependencies,'ROOT',Path(folder)),patch.object(dependencies.sys,'platform','linux'),patch.object(dependencies.shutil,'which',side_effect=lambda tool:'/usr/bin/apt-get' if tool=='apt-get' else None):
            with self.assertRaisesRegex(RuntimeError,'sudo apt-get install.*xvfb xauth'):
                dependencies.require_login_tools()

    def test_windows_does_not_require_a_linux_import_display(self):
        with patch.object(dependencies.sys,'platform','win32'),patch.object(dependencies.shutil,'which') as which:
            self.assertEqual(dependencies.login_missing(),[])
            dependencies.require_login_tools()
        which.assert_not_called()
