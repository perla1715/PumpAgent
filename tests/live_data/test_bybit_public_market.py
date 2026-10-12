from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from pumpagent.live_data.adapters import bybit_public_market


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


class BybitPublicMarketTests(unittest.TestCase):
    def test_closed_1m_bars_preserve_native_closed_provenance(self):
        payload = (
            b'{"result":{"list":[["1791849600000","1","2","0.5","1.5","10"]]}}'
        )
        with patch.object(bybit_public_market.request, "urlopen", return_value=_Response(payload)) as urlopen:
            bars = bybit_public_market.fetch_closed_1m_bars("ALPHAUSDT")

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["timestamp"], datetime(2026, 10, 13, tzinfo=timezone.utc).isoformat())
        self.assertTrue(bars[0]["closed"])
        self.assertEqual(bars[0]["native_or_reconstructed"], "NATIVE")
        self.assertIn("category=linear", urlopen.call_args.args[0].full_url)
        self.assertIn("symbol=ALPHAUSDT", urlopen.call_args.args[0].full_url)


if __name__ == "__main__":
    unittest.main()
