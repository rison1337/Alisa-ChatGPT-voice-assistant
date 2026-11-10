"""Speaking-only eyes preserve the clock values and never own auxiliary icons."""
import os,subprocess,sys,tempfile,unittest
from pathlib import Path
from test_midi_clock_recovery import HARNESS
from alisa_cli import runtime,state

ROOT=Path(__file__).resolve().parents[1]

class EyesPreference(unittest.TestCase):
    def test_explicit_eyes_override_legacy_matrix_only_on_midi(self):
        c=state.defaults('native');c.update(host='192.0.2.10',model='cucumber',midi_matrix=True,midi_eyes=True)
        state.validate(c)
        env=runtime.native_environment(c)
        self.assertIn(b'ALISA_MIDI_EYES=1',env);self.assertNotIn(b'ALISA_MIDI_MATRIX',env)
        c['model']='bergamot';self.assertNotIn(b'ALISA_MIDI_EYES',runtime.native_environment(c))
        c['midi_eyes']='yes'
        with self.assertRaises(ValueError):state.validate(c)

@unittest.skipUnless(sys.platform=='linux','requires Bash/flock')
class EyesFrames(unittest.TestCase):
    def run_case(self,body):
        with tempfile.TemporaryDirectory() as folder:
            uptime=Path(folder)/'uptime';uptime.write_text('100.00 0\n')
            env={**os.environ,'FIXTURE':folder,'HELPER':str(ROOT/'assets/native/midi-clock.sh'),'ALISA_MIDI_EYES':'1','ALISA_MIDI_EYES_UPTIME':str(uptime)}
            result=subprocess.run(['bash','-c',HARNESS+'\n'+body],env=env,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr.decode())

    def test_speech_blink_and_clock_with_both_alarm_states(self):
        for alarm in (0,255):
            with self.subTest(alarm=alarm):
                self.run_case(f"printf '{alarm}' > \"$FIXTURE/physical/alarm\"\n"+r'''
set -- $parts; [ "$#" = 28 ]
transaction acquire
[ "$(cat "$FIXTURE/count")" = 30 ]
# Acquisition must not blank the waiting clock.
[ "$(cat "$FIXTURE/physical/a1")" = 17 ]
eyes_tick=0;command='F 0 0 0 0 0 0 0 0 0';eyes_frame;transaction eyes_paint
[ "$speaking" = 0 ];[ "$(cat "$FIXTURE/physical/a1")" = 17 ]
command='F 10 0 0 0 0 0 0 0 0';eyes_frame;transaction eyes_paint
[ "$speaking" = 1 ];[ "$(cat "$FIXTURE/physical/a1")" = 17 ];[ "$(cat "$FIXTURE/physical/c1")" = 17 ]
[ "$(cat "$FIXTURE/physical/a2")" = 0 ]
printf '100.30 0\n' > "$ALISA_MIDI_EYES_UPTIME"
command='F 0 0 0 0 0 0 0 0 0';eyes_frame
[ "$speaking" = 1 ];[ "$eyes_tick" = 30 ]
eyes_tick=150;transaction eyes_paint
[ "$(cat "$FIXTURE/physical/a1")" = 0 ];[ "$(cat "$FIXTURE/physical/g1")" = 17 ]
# Stock minute/brightness updates arriving during speech must survive.
printf '42\n' > "$dir/shadow/a1"
printf '102.00 0\n' > "$ALISA_MIDI_EYES_UPTIME"
command='F 0 0 0 0 0 0 0 0 0';eyes_frame;transaction eyes_paint
[ "$eyes_tick" = 0 ];[ "$(cat "$FIXTURE/physical/a1")" = 42 ]
restore "$dir"
[ ! -d "$dir" ]
'''+f'\n[ "$(cat "$FIXTURE/physical/alarm")" = {alarm} ]\n')

    def test_invalid_frames_do_not_start_eyes(self):
        self.run_case(r'''
eyes_tick=0
for command in 'F 1 2' 'F 999 0 0 0 0 0 0 0 0' 'F x 0 0 0 0 0 0 0 0'; do
    if eyes_frame; then exit 5; fi
done
command=B;eyes_frame;[ "$speaking" = 0 ]
restore "$dir"
''')

    def test_partial_stock_write_skips_frame_then_recovers(self):
        self.run_case(r'''
transaction acquire
eyes_tick=0;command='F 10 0 0 0 0 0 0 0 0';eyes_frame
: > "$dir/shadow/g4"
if transaction eyes_paint; then exit 6; fi
# No partial eye frame is painted before every stock value is valid.
[ "$(cat "$FIXTURE/physical/c2")" = 17 ]
printf '17\n' > "$dir/shadow/g4"
transaction eyes_paint
[ "$(cat "$FIXTURE/physical/c2")" = 0 ]
restore "$dir"
''')

    def test_orphan_owner_is_released_even_with_fresh_lease(self):
        self.run_case(r'''
transaction acquire
# The owner is alive with a fresh lease, but its recorded parent no longer matches.
guard "$dir" "$$" 999999999
[ ! -d "$dir" ]
''')
