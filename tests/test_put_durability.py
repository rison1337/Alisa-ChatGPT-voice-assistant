import hashlib,unittest
from unittest.mock import patch
from alisa_cli.ssh import Station

class Durability(unittest.TestCase):
 def test_put_flushes_data_before_and_after_replacing_the_file(self):
  data=b'1\tsome phrase\n';commands=[]
  def run(self,command,data=None,check=True,timeout=60):commands.append(command)
  with patch.object(Station,'run',run),patch.object(Station,'text',return_value=hashlib.sha256(data).hexdigest()+'  x'):
   Station({'host':'192.0.2.10'}).put('/data/opt/alisa-standalone/commands.txt',data)
  lines=commands[0].splitlines()
  replace=next(i for i,line in enumerate(lines) if line.startswith('mv '))
  # A power cut right after the CLI finishes must not leave an empty file.
  self.assertIn('sync',lines[:replace])
  self.assertEqual(lines[replace+1],'sync')

if __name__=='__main__':unittest.main()
