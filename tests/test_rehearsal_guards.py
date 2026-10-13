"""The rehearsal harness must refuse every target except the local rehearsal schema."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import Settings

HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "rehearsal" / "migration_interrupt.py"
spec = importlib.util.spec_from_file_location("migration_interrupt", HARNESS)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

VALID = "mysql+pymysql://rehearsal:pw@127.0.0.1:3306/ai_sales_crm_rehearsal"


def test_accepts_only_the_local_rehearsal_schema() -> None:
    url = harness.target_url({"REHEARSAL_DATABASE_URL": VALID})
    assert url.database == "ai_sales_crm_rehearsal"
    # A missing port means MySQL's default, which is allowlisted.
    no_port = harness.target_url({"REHEARSAL_DATABASE_URL": VALID.replace(":3306", "")})
    assert no_port.port is None


@pytest.mark.parametrize(
    ("environ", "reason"),
    [
        ({}, "not set"),
        ({"REHEARSAL_DATABASE_URL": "sqlite:///ai_sales_crm_rehearsal"}, "must be MySQL"),
        ({"REHEARSAL_DATABASE_URL": VALID.replace("_rehearsal", "")}, "must be exactly"),
        ({"REHEARSAL_DATABASE_URL": VALID.replace("_rehearsal", "_test")}, "must be exactly"),
        ({"REHEARSAL_DATABASE_URL": VALID + "?init_command=USE+ai_sales_crm"}, "query options"),
        ({"REHEARSAL_DATABASE_URL": VALID.replace("127.0.0.1", "db.prod.example.com")}, "allowlist"),
        # A local port other than 3306 may be a tunnel to a remote server.
        ({"REHEARSAL_DATABASE_URL": VALID.replace(":3306", ":3307")}, "allowlist"),
        ({"REHEARSAL_DATABASE_URL": VALID.replace("127.0.0.1:3306", "localhost:13306")}, "allowlist"),
        ({"REHEARSAL_DATABASE_URL": VALID, "ENVIRONMENT": "Production"}, "production"),
    ],
)
def test_refuses_unsafe_targets(environ: dict[str, str], reason: str) -> None:
    with pytest.raises(harness.GuardError, match=reason):
        harness.target_url(environ)


def test_does_not_reuse_already_loaded_app_settings() -> None:
    # The test session has loaded app settings from .env, so retargeting must refuse.
    import app.core.config  # noqa: F401

    with pytest.raises(harness.GuardError, match="loaded before"):
        harness.point_app_settings_at(harness.target_url({"REHEARSAL_DATABASE_URL": VALID}))


# Runs point_app_settings_at in a fresh interpreter whose working directory holds the
# given .env, so the effective settings come from that file. Nothing connects to MySQL.
_SETTINGS_PROBE = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("harness", sys.argv[1])
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
url = harness.target_url({"REHEARSAL_DATABASE_URL": sys.argv[2]})
harness.point_app_settings_at(url)
print("settings-accepted")
"""


def _probe_settings(tmp_path: Path, dotenv: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / ".env").write_text(dotenv, encoding="utf-8")
    # Process variables override .env, so drop every one the app settings read; the
    # probe's settings must come only from the .env written above.
    setting_names = {name.upper() for name in Settings.model_fields}
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in setting_names | {"REHEARSAL_DATABASE_URL"}
    }
    return subprocess.run(
        [sys.executable, "-c", _SETTINGS_PROBE, str(HARNESS), VALID],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_refuses_when_effective_settings_say_production(tmp_path: Path) -> None:
    production = _probe_settings(
        tmp_path,
        "ENVIRONMENT=production\n"
        f"JWT_SECRET_KEY={'p' * 40}\n"
        "ALLOW_PUBLIC_REGISTRATION=false\n"
        'CORS_ALLOWED_ORIGINS=["https://crm.example.com"]\n',
    )
    assert production.returncode != 0
    assert "ENVIRONMENT is production" in production.stderr
    assert "settings-accepted" not in production.stdout


def test_accepts_development_settings_pointed_at_rehearsal(tmp_path: Path) -> None:
    # Control for the test above: the same probe passes when .env is not production.
    development = _probe_settings(tmp_path, "ENVIRONMENT=development\n")
    assert development.returncode == 0, development.stderr
    assert "settings-accepted" in development.stdout
