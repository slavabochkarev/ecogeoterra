import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main


class EsriConfigurationTests(unittest.TestCase):
    def test_missing_configuration_omits_key_and_disables_caching(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            static = Path(directory) / "app" / "static"
            with patch.object(main, "STATIC", static):
                response = TestClient(main.app).get("/api/cadastre-experiment/basemaps")

        self.assertEqual(response.json(), {"esriApiKey": None})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")

    def test_local_dotenv_and_environment_precedence(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            (root / ".env").write_text("# Local config\nESRI_API_KEY='fixture-file-value'\n", encoding="utf-8-sig")
            with patch.object(main, "STATIC", root / "app" / "static"):
                self.assertEqual(main.esri_api_key(), "fixture-file-value")
                with patch.dict(os.environ, {"ESRI_API_KEY": " fixture-process-value "}):
                    self.assertEqual(main.esri_api_key(), "fixture-process-value")
