"""Render one frame headlessly when pygame is installed (also run in CI)."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class VisualTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('pygame') is not None,
                         'pygame is not installed in this test environment')
    def test_screenshot(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'screenshot.png'
            env=os.environ.copy()
            env['SDL_VIDEODRIVER']='dummy'
            env['SDL_AUDIODRIVER']='dummy'
            proc=subprocess.run([sys.executable,'main.py','--windowed','--no-voice','--no-speech',
                                 '--no-ai','--screenshot',str(output)],
                                cwd=root,env=env,capture_output=True,text=True,timeout=35)
            self.assertEqual(proc.returncode,0,proc.stdout+'\n'+proc.stderr)
            self.assertGreater(output.stat().st_size,2000)


if __name__=='__main__':
    unittest.main()
