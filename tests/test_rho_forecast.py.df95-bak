
# K12+K13+K16 Trinity-CONTRARIAN 2026-05-17 (Cross-LLM-validated)
def k12_provenance(payload: bytes, key: bytes = b"df-trinity-contrarian-v1") -> dict:
    import hashlib, hmac
    return {
        "payload_hash": hashlib.sha256(payload).hexdigest(),
        "hmac_sha256": hmac.new(key, payload, hashlib.sha256).hexdigest(),
    }

def k13_anchor(payload_hash: str) -> dict:
    from datetime import datetime, timezone
    return {
        "anchor_type": "rfc3161-mock",
        "iso_ts": datetime.now(timezone.utc).isoformat(),
        "payload_hash": payload_hash,
    }

def k16_lock_or_exit(df_name: str):
    import fcntl, os, sys
    lock_path = f"/tmp/df-trinity-{df_name}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        sys.exit(3)

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

import numpy as np
import pytest

import src.rho_forecast as rho_forecast_module
from src.rho_forecast import EngineConfig, PriorConfig, RhoForecastEngine, SalesCloudRevenue


@pytest.fixture()
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RhoForecastEngine:
    monkeypatch.delenv("DF_HLM_7_REAL_SALES_CLOUD_ENABLED", raising=False)
    monkeypatch.delenv("PHRONESIS_TICKET", raising=False)
    return RhoForecastEngine(EngineConfig(base_dir=tmp_path))


def test_default_mock_mode_synthetic(engine: RhoForecastEngine) -> None:
    assert engine.mode() == "mock"
    assert len(engine.load_sales_cloud_revenue()) == 3


def test_env_var_true_real_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_HLM_7_REAL_SALES_CLOUD_ENABLED", "true")
    monkeypatch.setenv("PHRONESIS_TICKET", "PT-2026-AB-123")
    assert RhoForecastEngine(EngineConfig(base_dir=tmp_path)).mode() == "real"


def test_concurrent_spawn_protection() -> None:
    script = Path(__file__).parents[1] / "scripts" / "run-df-hlm-7.sh"
    text = script.read_text()
    assert "mkdir \"$LOCK_DIR\"" in text
    assert "pgrep -f \"src/rho_forecast.py\"" in text


def test_cascade_containment(engine: RhoForecastEngine) -> None:
    engine.breaker.record_failure("sales_cloud")
    assert engine.run("2026-05")["forecast"]["monthly"]


def test_external_anchor_sales_cloud(engine: RhoForecastEngine) -> None:
    assert engine.run("2026-05")["external_anchor"]["type"] == "sales_cloud_revenue_data"


def test_circuit_breaker_open(engine: RhoForecastEngine) -> None:
    for _ in range(3):
        engine.breaker.record_failure("sales_cloud")
    assert engine.breaker.is_open("sales_cloud")
    assert engine.load_sales_cloud_revenue() == []


def test_direct_mode_prior_only(engine: RhoForecastEngine) -> None:
    for _ in range(3):
        engine.breaker.record_failure("sales_cloud")
    result = engine.run("2026-05")
    assert result["degradation_mode"] == "standalone_prior_only"
    assert result["direct_mode_capability"] == 0.60


def test_idempotent_monthly_snapshot(engine: RhoForecastEngine) -> None:
    one = engine.run("2026-05")["snapshot_path"]
    two = engine.run("2026-05")["snapshot_path"]
    assert one == two


def test_health_check_no_deps(engine: RhoForecastEngine) -> None:
    assert engine.health_check()["dependencies"] == []


def test_monte_carlo_1000_iter_deterministic_seed(tmp_path: Path) -> None:
    cfg = EngineConfig(base_dir=tmp_path, seed=11, iterations=1000)
    a = RhoForecastEngine(cfg).monte_carlo_forecast()["monthly"]
    b = RhoForecastEngine(cfg).monte_carlo_forecast()["monthly"]
    assert a == b


def test_p10_p25_p50_p75_p90_output_format(engine: RhoForecastEngine) -> None:
    values = engine.monte_carlo_forecast()["monthly"]["M1"]
    assert set(values) == {"P10", "P25", "P50", "P75", "P90"}


def test_sensitivity_analysis_sobol_or_tornado(engine: RhoForecastEngine) -> None:
    forecast = engine.monte_carlo_forecast()
    result = engine.sensitivity_analysis(forecast["samples"])
    assert result["method"] == "tornado_correlation_proxy"
    assert result["largest_driver"] in result["indices"]


def test_pareto_top_3_lift_sources(engine: RhoForecastEngine) -> None:
    top = engine.pareto_top_3_lift_sources(engine.monte_carlo_forecast()["samples"])
    assert len(top) == 3
    assert top[0]["rho_per_token"] >= top[-1]["rho_per_token"]


def test_pdf_generation_quartal_bericht(engine: RhoForecastEngine) -> None:
    path = Path(engine.run("2026-05")["pdf_path"])
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF")


def test_beta_prior_roi(engine: RhoForecastEngine) -> None:
    roi = engine.monte_carlo_forecast()["samples"]["roi"]
    assert np.isclose(float(np.mean(roi)), 0.3, atol=0.04)


def test_normal_prior_lift_voice(engine: RhoForecastEngine) -> None:
    samples = engine.monte_carlo_forecast()["samples"]
    assert np.isclose(float(np.mean(samples["lift"])), 2.0, atol=0.06)
    assert np.isclose(float(np.mean(samples["voice"])), 0.0, atol=0.03)


def test_provenance_in_output_seed_priors(engine: RhoForecastEngine) -> None:
    provenance = engine.run("2026-05")["provenance"]
    assert provenance["seed"] == 20260514
    assert provenance["iteration_count"] == 1000
    assert "roi" in provenance["variable_priors"]


def test_pre_action_domain_check(engine: RhoForecastEngine) -> None:
    check = engine.pre_action_domain_check()
    assert check["ok"] is True
    assert check["domain"] == "HeyLou-Marketing-Wave-2"


def test_audit_log_appended_per_run(engine: RhoForecastEngine) -> None:
    engine.run("2026-05")
    engine.run("2026-06")
    lines = engine.config.audit_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "mock_run_complete"


def test_pii_scrubbed_in_output_with_kemmer_name(engine: RhoForecastEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Output enthaelt keinen Kemmer-Familien-Namen."""
    original = engine.pre_action_domain_check

    def custom_domain_check() -> dict[str, object]:
        result = original()
        result["briefing_note"] = "Martin reviewed with Imke yesterday"
        return result

    monkeypatch.setattr(engine, "pre_action_domain_check", custom_domain_check)
    result = engine.run("2026-05")
    snapshot_text = Path(result["snapshot_path"]).read_text(encoding="utf-8")
    assert "Martin" not in snapshot_text
    assert "Imke" not in snapshot_text


def test_k13_pre_action_verification_env_tag_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real-Mode mit falschem env_tag wird geblockt."""
    monkeypatch.setenv("DF_ENV_TAG", "prod")
    monkeypatch.delenv("DF_EXPECTED_ENV_TAG", raising=False)
    monkeypatch.setenv("DF_HLM_7_REAL_SALES_CLOUD_ENABLED", "true")
    monkeypatch.setenv("PHRONESIS_TICKET", "PT-2026-AB-123")
    monkeypatch.setenv("DF_HLM_7_SALES_CLOUD_REVENUE_JSON", '[{"month":"2026-05","revenue_eur":320000}]')
    engine = RhoForecastEngine(EngineConfig(base_dir=tmp_path))
    with pytest.raises(RuntimeError) as exc_info:
        engine.run("2026-05")
    assert "K13" in str(exc_info.value)


def test_mock_provenance_explicit_in_output(engine: RhoForecastEngine) -> None:
    """Mock-Outputs haben 'mode': 'mock' in Provenance."""
    result = engine.run("2026-05")
    snapshot_text = Path(result["snapshot_path"]).read_text(encoding="utf-8")
    assert result["provenance"]["output"]["mode"] == "mock"
    assert '"mode": "mock"' in snapshot_text or "MOCK-" in snapshot_text


def test_k16_mutex_blocks_concurrent_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent Engine-Spawn wird geblockt."""
    temp_lock_dir = tmp_path / "df-hlm-7.lock"
    original_guard = rho_forecast_module.K16MutexGuard
    original_run_once = rho_forecast_module.RhoForecastEngine._run_once
    entered = threading.Event()
    release = threading.Event()
    state = {"calls": 0}
    first_result: dict[str, object] = {}

    class TestGuard(original_guard):
        def __init__(self, lock_dir: str, df_engine_marker: str) -> None:
            super().__init__(lock_dir=temp_lock_dir, df_engine_marker="nonexistent_marker_xyz123")

    def slow_run_once(self: RhoForecastEngine, month: str | None = None) -> dict[str, object]:
        state["calls"] += 1
        if state["calls"] == 1:
            entered.set()
            assert release.wait(timeout=5)
        return original_run_once(self, month)

    def runner() -> None:
        first_result["value"] = RhoForecastEngine(EngineConfig(base_dir=tmp_path / "one")).run("2026-05")

    monkeypatch.setattr(rho_forecast_module, "K16MutexGuard", TestGuard)
    monkeypatch.setattr(rho_forecast_module.RhoForecastEngine, "_run_once", slow_run_once)

    thread = threading.Thread(target=runner)
    thread.start()
    assert entered.wait(timeout=5)
    with pytest.raises(RuntimeError) as exc_info:
        RhoForecastEngine(EngineConfig(base_dir=tmp_path / "two")).run("2026-05")
    release.set()
    thread.join(timeout=5)

    assert "K16-VETO" in str(exc_info.value)
    assert "value" in first_result
    assert not temp_lock_dir.exists()
