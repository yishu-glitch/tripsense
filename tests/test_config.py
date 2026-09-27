import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from tripsense.config import load_project_env


class ProjectEnvTests(unittest.TestCase):
    def test_loads_local_env_without_overriding_process_values(self):
        directory = Path(__file__).with_name("_config_test")
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir()
        try:
            env_path = directory / ".env"
            env_path.write_text(
                "TRIPSENSE_LLM_MODEL=from-file\n"
                "TRIPSENSE_LLM_API_KEY='local-secret'\n"
                "# ignored\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"TRIPSENSE_LLM_MODEL": "from-process"},
                clear=False,
            ):
                os.environ.pop("TRIPSENSE_LLM_API_KEY", None)

                loaded = load_project_env(env_path)

                self.assertEqual(loaded, {"TRIPSENSE_LLM_API_KEY"})
                self.assertEqual(os.environ["TRIPSENSE_LLM_MODEL"], "from-process")
                self.assertEqual(os.environ["TRIPSENSE_LLM_API_KEY"], "local-secret")
        finally:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
