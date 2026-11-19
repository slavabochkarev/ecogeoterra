"""Read-only TLS diagnostics: no unverified handshakes or trust-store changes."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.nspd import NSPD_ROOT_CA, NSPD_ROOT_SHA256, SecureNspd

HOST = "nspd.gov.ru"
ENDPOINT = "https://nspd.gov.ru/api/geoportal/v2/search/geoportal?query=63%3A09%3A0102159%3A753&thematicSearchId=1"


def handshake(context):
    try:
        with socket.create_connection((HOST, 443), timeout=10) as connection:
            with context.wrap_socket(connection, server_hostname=HOST) as secure:
                return {"success": True, "tls_version": secure.version(), "cipher": secure.cipher()[0],
                        "leaf_der_sha256": hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest(),
                        "check_hostname": context.check_hostname, "verify_mode": int(context.verify_mode)}
    except ssl.SSLCertVerificationError as exc:
        return {"success": False, "exception_type": type(exc).__name__, "exception": str(exc),
                "verify_code": exc.verify_code, "verify_message": exc.verify_message}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/nspd-tls-diagnostic.json")
    parser.add_argument("--openssl", default=shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe")
    args = parser.parse_args()
    context = ssl.create_default_context()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "hostname": HOST, "url": ENDPOINT,
              "python": sys.version, "python_openssl": ssl.OPENSSL_VERSION,
              "packages": {name: importlib.metadata.version(name) for name in ("certifi", "httpx", "pynspd")},
              "environment": {name: {"set": name in os.environ, "nonempty": bool(os.environ.get(name))}
                              for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NSPD_CA_FILE")},
              "system_context_store_stats": context.cert_store_stats(),
              "python_system_only": handshake(context),
              "adapter": handshake(SecureNspd(ca_file=os.getenv("NSPD_CA_FILE")).tls_context),
              "official_root_source": "https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer",
              "official_root_provenance": "app/certs/README.md",
              "bundled_root_der_sha256": NSPD_ROOT_SHA256}
    paths = ssl.get_default_verify_paths()
    report["default_verify_paths"] = {"cafile_exists": bool(paths.cafile), "capath_exists": bool(paths.capath),
                                      "openssl_default_cafile": paths.openssl_cafile,
                                      "openssl_default_capath": paths.openssl_capath}
    if sys.platform == "win32":
        report["windows_stores"] = {store: {"certificate_count": len(ssl.enum_certificates(store)),
            "nspd_root_present": any(hashlib.sha256(cert).hexdigest() == NSPD_ROOT_SHA256
                                     for cert, encoding, trust in ssl.enum_certificates(store))} for store in ("ROOT", "CA")}
        # Schannel validation stays enabled. No verbose headers or proxy values.
        win = subprocess.run(["curl.exe", "--noproxy", "*", "--head", "--silent", "--show-error",
                              "--connect-timeout", "5", "--max-time", "12", ENDPOINT],
                             capture_output=True, text=True, timeout=15)
        report["windows_schannel_system_only"] = {"exit_code": win.returncode,
                                                   "error": win.stderr.strip(), "tls_trusted": win.returncode == 0}
    if Path(args.openssl).is_file() or shutil.which(args.openssl):
        report["diagnostic_openssl"] = subprocess.run([args.openssl, "version"], capture_output=True, text=True, check=True).stdout.strip()
        # Abort handshake at the first validation failure; still capture the public chain.
        probe = subprocess.run([args.openssl, "s_client", "-connect", HOST + ":443", "-servername", HOST,
                                "-showcerts", "-verify_return_error", "-verify_hostname", HOST, "-no_ticket"],
                               input="", capture_output=True, text=True, timeout=15)
        pems = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", probe.stdout, re.S)
        report["server_chain"] = []
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for index, pem in enumerate(pems):
                path = folder / f"cert-{index}.pem"
                path.write_text(pem + "\n", encoding="ascii")
                parsed = ssl._ssl._test_decode_cert(str(path))
                parsed["subjectAltName"] = parsed.get("subjectAltName", [])
                parsed["der_sha256"] = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
                report["server_chain"].append(parsed)
            if len(pems) >= 2:
                intermediates = folder / "intermediates.pem"
                intermediates.write_text("\n".join(pems[1:]) + "\n", encoding="ascii")
                base = [args.openssl, "verify", "-CAfile", str(NSPD_ROOT_CA), "-untrusted", str(intermediates), "-purpose", "sslserver"]
                for label, hostname in (("verified_server_chain", HOST), ("negative_hostname_test", "example.invalid")):
                    verified = subprocess.run([*base, "-verify_hostname", hostname, str(folder / "cert-0.pem")], capture_output=True, text=True)
                    report[label] = {"exit_code": verified.returncode, "hostname": hostname,
                                     "result": (verified.stdout + verified.stderr).replace(str(folder), "<temporary>").strip()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.output), "system_only": report["python_system_only"],
                      "adapter": report["adapter"], "chain_certificates": len(report.get("server_chain", []))}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
