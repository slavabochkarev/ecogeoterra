"""Validate the onedir allowlist, add user docs, then ZIP only that directory."""
import hashlib
from pathlib import Path
import shutil
import ssl
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "dist" / "EcoburoGeo"
ARCHIVE = ROOT / "EcoburoGeo-v0.2-win64.zip"


def validate_bundle():
    exe = BUNDLE / "EcoburoGeo.exe"
    with exe.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError("Expected a Windows PE executable")
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        if stream.read(6) != b"PE\0\0\x64\x86":
            raise RuntimeError("Expected a Windows x64 executable")
    required = ["_internal/python312.dll", "_internal/app/static/index.html",
                "_internal/app/static/app.js", "_internal/app/static/styles.css",
                "_internal/app/static/vendor/leaflet.js", "_internal/certifi/cacert.pem",
                "_internal/app/certs/russian_trusted_root_ca.pem", "_internal/app/certs/README.md",
                "_internal/pyproj/proj_dir/share/proj/proj.db", "_internal/_tcl_data/init.tcl",
                "_internal/_tk_data/tk.tcl"]
    for name in required:
        if not (BUNDLE / name).is_file():
            raise RuntimeError(f"Missing runtime file: {name}")
    for path in BUNDLE.rglob("*"):
        relative = path.relative_to(BUNDLE)
        if any(part in {".cache", ".venv", ".git", "__pycache__", "tests", "docs"} for part in relative.parts):
            raise RuntimeError(f"Development artifact in bundle: {relative}")
        if path.is_file() and (path.name.startswith(".env") or path.suffix.lower() in {".log", ".tmp", ".key", ".pem"}):
            # Only these two explicitly identified public CA resources may ship.
            if relative.as_posix() not in {"_internal/certifi/cacert.pem", "_internal/app/certs/russian_trusted_root_ca.pem"}:
                raise RuntimeError(f"Unexpected private/temporary file: {relative}")
    root_ca = BUNDLE / "_internal/app/certs/russian_trusted_root_ca.pem"
    der = ssl.PEM_cert_to_DER_cert(root_ca.read_text(encoding="ascii"))
    if hashlib.sha256(der).hexdigest() != "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31":
        raise RuntimeError("NSPD CA fingerprint mismatch in distribution")
    page = (BUNDLE / "_internal/app/static/index.html").read_text(encoding="utf-8")
    if "ЭкоГеоТерра v0.2" not in page:
        raise RuntimeError("Wrong application version in bundled UI")
    for forbidden in ("Аккурайд", "63:09:0102159:753", "Тестовый объект", "только для справки", "владение 36"):
        if forbidden.casefold() in page.casefold():
            raise RuntimeError("Old demonstration content in UI")


def main():
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("Package the Windows x64 distribution on Windows x64")
    validate_bundle()
    for name in ("README.md", "BUILD_WINDOWS.md", "START_HERE.txt", "VERIFICATION_WINDOWS.md"):
        shutil.copyfile(ROOT / name, BUNDLE / name)
    temporary = ARCHIVE.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(BUNDLE.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(BUNDLE.parent))
    with zipfile.ZipFile(temporary) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"Corrupt archive member: {bad}")
    temporary.replace(ARCHIVE)
    with ARCHIVE.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    ARCHIVE.with_suffix(".zip.sha256").write_text(f"{digest}  {ARCHIVE.name}\n", encoding="ascii")
    print(f"Validated {len(list(BUNDLE.rglob('*')))} entries; ZIP: {ARCHIVE}; SHA256: {digest}")


if __name__ == "__main__":
    main()
