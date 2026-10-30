import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import state

class Encoding(unittest.TestCase):
 def test_cyrillic_phrases_survive_save_and_load_in_any_locale(self):
  # Windows reads text as cp1251 by default; the saved file is UTF-8.
  c=state.defaults('native');c['host']='192.0.2.10';c['phrases']['open']='открой чат'
  with tempfile.TemporaryDirectory() as folder,patch.object(state,'HOME',Path(folder)):
   state.save(c)
   with patch('locale.getpreferredencoding',return_value='cp1251'),patch('locale.getencoding',return_value='cp1251',create=True):
    loaded=state.load('native')
  self.assertEqual(loaded['phrases']['open'],'открой чат')
  self.assertEqual(state.command_file(loaded['phrases']).decode().splitlines()[0],'1\tоткрой чат')

if __name__=='__main__':unittest.main()
