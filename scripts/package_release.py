"""Create a source-only Docker handoff with no machine-specific metadata."""

import hashlib
import json
from pathlib import Path
import re
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "poverty-bank-a2-optimized"
FILES = [
    "Dockerfile", "compose.yaml", "compose.quality.yaml", "requirements-runtime.txt",
    ".env.example", ".gitignore", ".dockerignore", "README.md", "TEST_RESULTS.md",
    "check.py", "run_sql.py", "run_validation.py",
    "scripts/initialize_reporting.py", "scripts/verify_pipeline.py", "scripts/verify_showcase.py", "scripts/quality_demo.py",
    "scripts/run_pipeline.sh", "scripts/alice_trace.sql", "scripts/package_release.py", "scripts/smoke_reports.py",
    "tests/test_consent_sql.py", "tests/test_report_api.py", "tests/test_detail_api.py", "tests/test_quality_demo.py",
    "web/server.py", "web/static/index.html",
    "web/static/styles.css", "web/static/app.js", "web/static/favicon.svg",
    "web/static/icons.svg", "web/static/ICONS-LICENSE.txt",
    "sql/scenarios/identity.sql", "sql/scenarios/consent.sql", "sql/scenarios/types.sql",
]
FILES += ["sql/" + name for name in (
    "01_create_schemas_and_tables.sql", "02_load_synthetic_source_data.sql", "02b_load_showcase_data.sql",
    "03_stage_and_resolve_customers.sql", "04_load_data_vault.sql",
    "05_load_warehouse.sql", "06_reporting_views.sql", "07_validation_queries.sql",
)]
PRIVATE_PATH = re.compile(
    r"/(?:Users|home)/[^\s/<>]+|[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]"
    r"|file:/{2,3}|%2f(?:Users|home)%2f",
    re.IGNORECASE,
)


def main():
    entries = {}
    for relative in sorted(FILES):
        path = ROOT / relative
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f"Required regular release file missing or unsafe: {relative}")
        content = path.read_bytes()
        if PRIVATE_PATH.search(content.decode("utf-8")):
            # Do not reproduce the detected private text in the error.
            raise ValueError(f"Machine-specific path detected in: {relative}")
        entries[relative] = content
    manifest = {
        "format": 1,
        "description": "Portable runtime source distribution; operational guides are maintained separately.",
        "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                  for name, data in entries.items()},
    }
    entries["RELEASE_MANIFEST.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    destination = ROOT / "dist" / "PovertyBank-A2-Optimized.zip"
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_suffix(".zip.tmp")
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for relative, content in entries.items():
                info = ZipInfo(f"{ARCHIVE_ROOT}/{relative}", date_time=(2026, 10, 13, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.create_system = 3
                mode = 0o100755 if relative.endswith(".sh") else 0o100644
                info.external_attr = mode << 16
                archive.writestr(info, content)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    print(f"Created dist/{destination.name}: {len(entries)} files, {destination.stat().st_size:,} bytes")
    print("SHA-256: " + digest)
    print("Privacy check passed for every allowlisted file; runtime data and local settings excluded.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, UnicodeError) as exc:
        # OSError text may contain an absolute path; only print our relative errors.
        print(str(exc) if isinstance(exc, ValueError) else "Packaging failed; check required files and output access.")
        raise SystemExit(1) from None
