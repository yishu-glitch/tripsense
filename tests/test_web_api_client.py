import json
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is required for browser client tests")
class WebApiClientTests(unittest.TestCase):
    def test_client_uses_xhr_when_fetch_is_unavailable(self):
        client_path = Path(__file__).parents[1] / "web" / "tripsense-api.js"
        script = f"""
const {{ TripSenseApi }} = require({json.dumps(str(client_path))});
const calls = [];
class FakeXhr {{
  open(method, url) {{ this.method = method; this.url = url; }}
  setRequestHeader() {{}}
  send(body) {{
    calls.push({{ method: this.method, url: this.url, body: body || null }});
    this.status = 200;
    this.responseText = JSON.stringify({{ status: 'ok' }});
    this.onload();
  }}
}}
(async () => {{
  const api = new TripSenseApi({{ fetchImpl: null, xhrFactory: () => new FakeXhr() }});
  const result = await api.health();
  process.stdout.write(JSON.stringify({{ calls, result }}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)

        self.assertEqual(result["result"], {"status": "ok"})
        self.assertEqual(result["calls"], [{"method": "GET", "url": "/api/v1/health", "body": None}])

    def test_client_prefers_window_in_an_electron_style_browser(self):
        client_path = Path(__file__).parents[1] / "web" / "tripsense-api.js"
        script = f"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync({json.dumps(str(client_path))}, 'utf8');
const sandbox = {{ window: {{ document: {{}} }}, module: {{ exports: {{}} }} }};
vm.runInNewContext(source, sandbox);
process.stdout.write(JSON.stringify({{
  browserExport: typeof sandbox.window.TripSenseApi,
  commonJsExport: typeof sandbox.module.exports.TripSenseApi
}}));
"""
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)

        self.assertEqual(result["browserExport"], "function")

    def test_client_calls_the_mvp_contract_with_expected_methods_and_payloads(self):
        client_path = Path(__file__).parents[1] / "web" / "tripsense-api.js"
        script = f"""
const {{ TripSenseApi }} = require({json.dumps(str(client_path))});
const calls = [];
const responses = {{
  '/api/v1/health': {{ status: 'ok' }},
  '/api/v1/journeys': {{ items: [{{ id: 'j1' }}], journey_id: 'j1' }},
  '/api/v1/journeys/j1/records': {{ id: 'r1', items: [] }},
  '/api/v1/journeys/j1/diaries/generate': {{ id: 'd1' }},
  '/api/v1/journeys/j1/diaries/latest': {{ id: 'd1' }}
}};
const fetchImpl = async (url, options = {{}}) => {{
  calls.push({{ url, method: options.method || 'GET', body: options.body || null }});
  return {{
    ok: true,
    status: 200,
    json: async () => responses[url]
  }};
}};
(async () => {{
  const api = new TripSenseApi({{ fetchImpl }});
  await api.health();
  await api.chat({{ city: 'shanghai', text: '想慢慢走' }});
  await api.listJourneys();
  await api.saveJourney({{ city: 'shanghai', text: '慢慢走四小时' }});
  await api.addRecord('j1', {{ day_index: 1, mood: '松弛', note: '树影很好看' }});
  await api.listRecords('j1');
  await api.generateDiary('j1');
  const latest = await api.latestDiary('j1');
  process.stdout.write(JSON.stringify({{ calls, latest }}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""

        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)

        self.assertEqual(
            [(call["method"], call["url"]) for call in result["calls"]],
            [
                ("GET", "/api/v1/health"),
                ("POST", "/api/v1/chat/respond"),
                ("GET", "/api/v1/journeys"),
                ("POST", "/api/v1/journeys"),
                ("POST", "/api/v1/journeys/j1/records"),
                ("GET", "/api/v1/journeys/j1/records"),
                ("POST", "/api/v1/journeys/j1/diaries/generate"),
                ("GET", "/api/v1/journeys/j1/diaries/latest"),
            ],
        )
        self.assertEqual(result["latest"]["id"], "d1")
        self.assertEqual(
            json.loads(result["calls"][4]["body"]),
            {"day_index": 1, "mood": "松弛", "note": "树影很好看"},
        )


if __name__ == "__main__":
    unittest.main()
