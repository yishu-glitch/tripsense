import os
import shutil
import unittest
from pathlib import Path


class MvpLauncherTests(unittest.TestCase):
    def test_default_host_binds_all_interfaces_for_lan_phone_access(self):
        from tripsense.mvp import build_parser

        args = build_parser().parse_args([])
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 8000)

    def test_launcher_configures_local_database_and_starts_one_web_service(self):
        from tripsense.mvp import main

        temp_dir = Path(__file__).with_name("_mvp_launcher_test")
        shutil.rmtree(temp_dir, ignore_errors=True)
        database = temp_dir / "data" / "demo.db"
        calls = []

        def run_server(app, **options):
            calls.append((app, options))

        previous = os.environ.get("TRIPSENSE_DB_PATH")
        try:
            main(
                ["--host", "127.0.0.1", "--port", "8123", "--db", str(database)],
                run_server=run_server,
            )
            self.assertEqual(os.environ["TRIPSENSE_DB_PATH"], str(database.resolve()))
            self.assertEqual(
                calls,
                [
                    (
                        "tripsense.api.app:app",
                        {"host": "127.0.0.1", "port": 8123, "reload": False},
                    )
                ],
            )
        finally:
            if previous is None:
                os.environ.pop("TRIPSENSE_DB_PATH", None)
            else:
                os.environ["TRIPSENSE_DB_PATH"] = previous
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
