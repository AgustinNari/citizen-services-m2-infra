"""Safety regressions: the outage demo must never stop a remote Docker broker."""
import importlib.util
import json
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('int04_demo', Path(__file__).resolve().parents[1] / 'compose/qa/demo-int04.py')
DEMO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEMO)


class Int04DemoSafetyTests(unittest.TestCase):
    def test_synthetic_sql_text_round_trips_as_utf8_on_the_host(self):
        result = DEMO.command([sys.executable, '-c',
                               "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read().decode('utf-8').encode('utf-8'))"],
                              'Prueba sintética INT04 iniciada')
        self.assertEqual(result, 'Prueba sintética INT04 iniciada')

    def test_remote_docker_host_is_rejected_before_any_docker_operation(self):
        with patch.dict(os.environ, {'DOCKER_HOST': 'tcp://shared.invalid:2376'}), \
                patch('sys.argv', ['demo-int04.py']), patch.object(DEMO, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'LOCAL_DOCKER_HOST_REQUIRED'):
                DEMO.main()
            command.assert_not_called()

    def test_remote_active_context_is_rejected_before_inspection_or_mutation(self):
        with patch.dict(os.environ, {'DOCKER_HOST': ''}), patch('sys.argv', ['demo-int04.py']), \
                patch.object(DEMO, 'command', return_value=json.dumps([
                    {'Endpoints': {'docker': {'Host': 'ssh://shared.invalid'}}}])) as command:
            with self.assertRaisesRegex(RuntimeError, 'LOCAL_DOCKER_CONTEXT_REQUIRED'):
                DEMO.main()
            command.assert_called_once_with(['docker', 'context', 'inspect'])


if __name__ == '__main__':
    unittest.main()
