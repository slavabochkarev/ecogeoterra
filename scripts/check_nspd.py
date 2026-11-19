r"""Limited, explicit live probes. Never disables TLS or scans an area.

Run from the repository: .venv\Scripts\python.exe scripts/check_nspd.py
"""
import argparse
import json
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.cadastre import CadastreService, EnrichInput
from app.nspd import NspdFailure, NspdGateway, SecureNspd, NSPD_ROOT_SHA256


class RecordingGateway(NspdGateway):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.exchanges = []
        self.responses = {}

    def _json(self, client, method, url, **kwargs):
        try:
            data = super()._json(client, method, url, **kwargs)
            self.responses[url] = data
            return data
        finally:
            response = client._last_response
            self.exchanges.append({"method": method.upper(), "url": "https://nspd.gov.ru" + url,
                                   "http_status": response.status_code if response is not None else None})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--osm-feature", type=Path, default=ROOT / "docs/nspd-control-osm.geojson")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/nspd-integration-fixed.json")
    parser.add_argument("--cache-dir", type=Path, help="Persist the spatial result for subsequent UI verification without another NSPD request")
    args = parser.parse_args()
    feature = json.loads(args.osm_feature.read_text(encoding="utf-8"))
    p = feature["properties"]
    payload = EnrichInput(osm_type=p["osm_type"], osm_id=p["osm_id"], geometry=feature["geometry"], tags=p.get("tags", {}))
    with TemporaryDirectory() as cache:
        service = CadastreService(cache_dir=args.cache_dir or cache)
        service.gateway = RecordingGateway(timeout=service.gateway.timeout, ca_file=service.gateway.ca_file)
        client = SecureNspd(ca_file=service.gateway.ca_file)
        report = {"checked_at": datetime.now(timezone.utc).isoformat(), "pynspd": "1.1.15",
                  "tls": {"check_hostname": client.tls_context.check_hostname,
                          "certificate_required": client.tls_context.verify_mode == ssl.CERT_REQUIRED,
                          "custom_ca": bool(service.gateway.ca_file),
                          "ca_source": "NSPD_CA_FILE" if service.gateway.ca_file else "bundled official Gosuslugi root",
                          "bundled_root_der_sha256": NSPD_ROOT_SHA256},
                  "control_number": "63:09:0102159:753",
                  "osm_object": {"osm_type": payload.osm_type, "osm_id": payload.osm_id,
                                 "geometry_file": args.osm_feature.name, "geometry_type": feature["geometry"]["type"]}}
        try:
            found = service.gateway.by_number(report["control_number"])
            report["number_lookup"] = {"status": "received", "count": len(found),
                                       "exact_number_found": any(report["control_number"] in
                                           (f.get("properties", {}).get("options", {}).get("cad_num"),
                                            f.get("properties", {}).get("options", {}).get("cad_number")) for f in found)}
        except NspdFailure as exc:
            report["number_lookup"] = {"status": "unavailable", "error_code": exc.code, "message": str(exc)}
        report["spatial_lookup"] = service.enrich(payload)
        report["http_exchanges"] = service.gateway.exchanges
        raw_number = service.gateway.responses.get("/api/geoportal/v2/search/geoportal")
        if raw_number is not None:
            raw_path = args.output.with_name(args.output.stem + "-number.json")
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(json.dumps(raw_number, ensure_ascii=False, indent=2), encoding="utf-8")
            report["number_response_file"] = raw_path.name
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
