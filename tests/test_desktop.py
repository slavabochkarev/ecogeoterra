import multiprocessing as mp
import os
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from desktop import reserve_listener, serve


class DesktopTests(unittest.TestCase):
    def test_occupied_port_is_skipped_and_listener_stays_reserved(self):
        with reserve_listener(0) as occupied:
            port = occupied.getsockname()[1]
            with reserve_listener(port) as available:
                self.assertEqual(available.getsockname()[0], "127.0.0.1")
                self.assertNotEqual(available.getsockname()[1], port)
                with socket.socket() as competing:
                    with self.assertRaises(OSError):
                        competing.bind(available.getsockname())

    def test_child_serves_local_assets_and_shuts_down(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
            listener = reserve_listener(0)
            port = listener.getsockname()[1]
            context = mp.get_context("spawn")
            stop = context.Event()
            child = context.Process(target=serve, args=(listener, stop), name="GeoServer")
            child.start()
            listener.close()
            try:
                with httpx.Client(trust_env=False, timeout=.5) as client:
                    deadline = time.monotonic() + 20
                    while True:
                        try:
                            response = client.get(f"http://127.0.0.1:{port}/api/health")
                            break
                        except httpx.HTTPError:
                            if time.monotonic() > deadline:
                                self.fail("Server child did not become ready")
                            time.sleep(.1)
                    self.assertEqual(response.json()["status"], "ok")
                    self.assertIn("ЭкоГеоТерра", client.get(f"http://127.0.0.1:{port}/").text)
                    self.assertEqual(client.get(f"http://127.0.0.1:{port}/static/vendor/leaflet.js").status_code, 200)
                stop.set()
                child.join(timeout=8)
                self.assertFalse(child.is_alive())
                self.assertEqual(child.exitcode, 0)
                with socket.socket() as probe:
                    self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
            finally:
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
