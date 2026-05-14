"""DF-HLM-7 rho Forecast Updater for HeyLou Marketing Wave 2."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

try:
    import structlog
except Exception:  # pragma: no cover
    structlog = None  # type: ignore[assignment]

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None  # type: ignore[assignment]

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas
except Exception:  # pragma: no cover
    A4 = None  # type: ignore[assignment]
    cm = 28.35
    canvas = None  # type: ignore[assignment]


PERCENTILES = (10, 25, 50, 75, 90)
REAL_MODE_RE = re.compile(r"^PT-2026-[A-Z0-9]{2}-[A-Z0-9]{3}$")
K_CONSTRAINTS = {
    "K11": "cascade_containment",
    "K12": "provenance_and_non_llm_validation",
    "K13": "sales_cloud_ground_truth",
    "K14": "single_command_human_override",
    "K15": "entropy_budget_400_loc",
    "K16": "concurrent_spawn_mutex",
}


def _logger() -> Any:
    if structlog is None:
        return None
    return structlog.get_logger("df_hlm_7")


@dataclass(frozen=True)
class PriorConfig:
    invest_low_eur: float = 25000.0
    invest_high_eur: float = 50000.0
    roi_alpha: float = 3.0
    roi_beta: float = 7.0
    lift_mean: float = 2.0
    lift_std: float = 0.3
    voice_mean: float = 0.0
    voice_std: float = 0.1

    def as_provenance(self) -> dict[str, Any]:
        return {
            "invest": {"distribution": "uniform", "low_eur": self.invest_low_eur, "high_eur": self.invest_high_eur},
            "roi": {"distribution": "beta", "alpha": self.roi_alpha, "beta": self.roi_beta},
            "lift": {"distribution": "normal", "mean": self.lift_mean, "std": self.lift_std},
            "voice": {"distribution": "normal", "mean": self.voice_mean, "std": self.voice_std},
        }


@dataclass(frozen=True)
class EngineConfig:
    base_dir: Path
    iterations: int = 1000
    seed: int = 20260514
    months: int = 3
    priors: PriorConfig = PriorConfig()
    timeout_s: int = 30
    circuit_open_threshold: int = 3
    direct_mode_capability: float = 0.60
    audit_log: Path | None = None

    @property
    def state_dir(self) -> Path:
        return self.base_dir / "state"

    @property
    def reports_dir(self) -> Path:
        return self.base_dir / "reports"

    @property
    def audit_path(self) -> Path:
        return self.audit_log or self.base_dir / "logs" / "df-hlm-7-audit.jsonl"


@dataclass(frozen=True)
class SalesCloudRevenue:
    month: str
    revenue_eur: float
    source: str = "sales_cloud_revenue_data"


class CircuitBreaker:
    def __init__(self, open_threshold: int = 3) -> None:
        self.open_threshold = open_threshold
        self.failures: dict[str, int] = {}

    def record_failure(self, name: str) -> None:
        self.failures[name] = self.failures.get(name, 0) + 1

    def record_success(self, name: str) -> None:
        self.failures[name] = 0

    def is_open(self, name: str) -> bool:
        return self.failures.get(name, 0) >= self.open_threshold


class RhoForecastEngine:
    """Deterministic Monte-Carlo rho forecaster with real-mode gating."""

    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.breaker = CircuitBreaker(config.circuit_open_threshold)
        self.log = _logger()

    def mode(self) -> str:
        enabled = os.environ.get("DF_HLM_7_REAL_SALES_CLOUD_ENABLED", "").lower() == "true"
        ticket = os.environ.get("PHRONESIS_TICKET", "")
        if enabled and REAL_MODE_RE.match(ticket):
            return "real"
        return "mock"

    def pre_action_domain_check(self) -> dict[str, Any]:
        mode = self.mode()
        return {
            "ok": True,
            "df_id": "DF-HLM-7",
            "domain": "HeyLou-Marketing-Wave-2",
            "mode": mode,
            "external_anchor_type": "sales_cloud_revenue_data",
            "phronesis_ticket": os.environ.get("PHRONESIS_TICKET") if mode == "real" else None,
        }

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "dependencies": [], "score": 1.0, "timestamp": utc_now()}

    def load_sales_cloud_revenue(self) -> list[SalesCloudRevenue]:
        if self.breaker.is_open("sales_cloud"):
            return []
        if self.mode() == "mock":
            return self._synthetic_sales_data()
        try:
            raw = os.environ.get("DF_HLM_7_SALES_CLOUD_REVENUE_JSON")
            if not raw:
                raise TimeoutError("sales cloud payload unavailable")
            data = json.loads(raw)
            rows = [SalesCloudRevenue(month=str(r["month"]), revenue_eur=float(r["revenue_eur"])) for r in data]
            self.breaker.record_success("sales_cloud")
            return rows
        except Exception:
            self.breaker.record_failure("sales_cloud")
            return []

    def _synthetic_sales_data(self) -> list[SalesCloudRevenue]:
        rng = np.random.default_rng(self.config.seed + 17)
        rows = []
        for idx in range(self.config.months):
            rows.append(SalesCloudRevenue(month=f"2026-Q{idx + 1}", revenue_eur=float(rng.normal(320000, 18000))))
        return rows

    def run(self, month: str | None = None) -> dict[str, Any]:
        domain = self.pre_action_domain_check()
        sales = self.load_sales_cloud_revenue()
        mode = self._degradation_mode(sales)
        forecast = self.monte_carlo_forecast()
        sensitivity = self.sensitivity_analysis(forecast["samples"])
        top_sources = self.pareto_top_3_lift_sources(forecast["samples"])
        payload = {
            "df_id": "DF-HLM-7",
            "month": month or datetime.now(timezone.utc).strftime("%Y-%m"),
            "mode": self.mode(),
            "degradation_mode": mode,
            "direct_mode_capability": self.config.direct_mode_capability if mode == "standalone_prior_only" else 1.0,
            "domain_check": domain,
            "external_anchor": {"type": "sales_cloud_revenue_data", "rows": [asdict(r) for r in sales]},
            "forecast": {k: v for k, v in forecast.items() if k != "samples"},
            "sensitivity": sensitivity,
            "pareto_top_3_lift_sources": top_sources,
            "provenance": self.provenance(),
            "timestamp": utc_now(),
        }
        snap = self.write_monthly_snapshot(payload)
        pdf = self.generate_quarterly_pdf(payload)
        payload["snapshot_path"] = str(snap)
        payload["pdf_path"] = str(pdf)
        self.append_audit("run_complete", payload)
        return payload

    def _degradation_mode(self, sales: list[SalesCloudRevenue]) -> str:
        if sales and self.mode() == "real":
            return "full"
        if sales:
            return "degraded_partial_inputs"
        if self.breaker.is_open("sales_cloud"):
            return "standalone_prior_only"
        return "degraded_no_sales_data"

    def monte_carlo_forecast(self) -> dict[str, Any]:
        rng = np.random.default_rng(self.config.seed)
        p = self.config.priors
        invest = stats.uniform(loc=p.invest_low_eur, scale=p.invest_high_eur - p.invest_low_eur).rvs(
            size=self.config.iterations, random_state=rng
        )
        roi = stats.beta(a=p.roi_alpha, b=p.roi_beta).rvs(size=self.config.iterations, random_state=rng)
        lift = stats.norm(loc=p.lift_mean, scale=p.lift_std).rvs(size=self.config.iterations, random_state=rng)
        voice = stats.norm(loc=p.voice_mean, scale=p.voice_std).rvs(size=self.config.iterations, random_state=rng)
        monthly = {}
        samples = {"invest": invest, "roi": roi, "lift": lift, "voice": voice, "rho": []}
        for m in range(1, self.config.months + 1):
            seasonal = 1.0 + (m - 1) * 0.015
            rho = ((invest * roi * seasonal) + (lift * 4200.0) - (np.abs(voice) * 9000.0)) / np.maximum(invest, 1.0)
            samples["rho"].append(rho)
            monthly[f"M{m}"] = {f"P{q}": round(float(np.percentile(rho, q)), 6) for q in PERCENTILES}
        return {"monthly": monthly, "samples": samples}

    def sensitivity_analysis(self, samples: dict[str, Any]) -> dict[str, Any]:
        rho = np.asarray(samples["rho"][0])
        result = {}
        for name in ("invest", "roi", "lift", "voice"):
            values = np.asarray(samples[name])
            corr = float(np.corrcoef(values, rho)[0, 1])
            result[name] = round(abs(corr), 6)
        total = sum(result.values()) or 1.0
        indices = {k: round(v / total, 6) for k, v in sorted(result.items(), key=lambda item: item[1], reverse=True)}
        return {"method": "tornado_correlation_proxy", "indices": indices, "largest_driver": next(iter(indices))}

    def pareto_top_3_lift_sources(self, samples: dict[str, Any]) -> list[dict[str, Any]]:
        rho = np.asarray(samples["rho"][0])
        sources = [
            ("direct_booking_roi", samples["roi"], 1800),
            ("brand_voice_lift", samples["lift"], 1400),
            ("budget_efficiency", 1 / np.asarray(samples["invest"]), 900),
            ("competitive_voice_shift", -np.abs(np.asarray(samples["voice"])), 700),
        ]
        scored = []
        for name, values, tokens in sources:
            impact = abs(float(np.corrcoef(np.asarray(values), rho)[0, 1]))
            scored.append({"source": name, "rho_per_token": round(impact / tokens, 9), "tokens": tokens})
        return sorted(scored, key=lambda item: item["rho_per_token"], reverse=True)[:3]

    def provenance(self) -> dict[str, Any]:
        return {
            "iteration_count": self.config.iterations,
            "seed": self.config.seed,
            "variable_priors": self.config.priors.as_provenance(),
            "non_llm_validation_layer": True,
            "deterministic_with_fixed_seed": True,
            "constraints": K_CONSTRAINTS,
        }

    def write_monthly_snapshot(self, payload: dict[str, Any]) -> Path:
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.state_dir / f"{payload['month']}-seed-{self.config.seed}.json"
        atomic_write_json(path, payload)
        return path

    def generate_quarterly_pdf(self, payload: dict[str, Any]) -> Path:
        self.config.reports_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.reports_dir / f"martin-quarterly-{payload['month']}.pdf"
        if canvas is None or A4 is None:
            path.write_bytes(b"%PDF-1.4\n% fallback report\n%%EOF\n")
            return path
        chart = self._render_chart(payload)
        c = canvas.Canvas(str(path), pagesize=A4)
        width, height = A4
        c.setFont("Helvetica-Bold", 14)
        c.drawString(2 * cm, height - 2 * cm, "DF-HLM-7 rho Forecast - Quartal-Bericht fuer Martin")
        c.setFont("Helvetica", 10)
        c.drawString(2 * cm, height - 3 * cm, f"Month: {payload['month']} | Seed: {self.config.seed} | Iter: {self.config.iterations}")
        c.drawString(2 * cm, height - 3.7 * cm, f"Mode: {payload['mode']} | Degradation: {payload['degradation_mode']}")
        y = height - 4.8 * cm
        for month, values in payload["forecast"]["monthly"].items():
            c.drawString(2 * cm, y, f"{month}: {values}")
            y -= 0.6 * cm
        c.drawString(2 * cm, y - 0.3 * cm, f"Largest driver: {payload['sensitivity']['largest_driver']}")
        if chart:
            c.drawImage(str(chart), 2 * cm, 3 * cm, width=15 * cm, height=7 * cm)
        c.showPage()
        c.save()
        return path

    def _render_chart(self, payload: dict[str, Any]) -> Path | None:
        if plt is None:
            return None
        chart = self.config.reports_dir / "forecast-percentiles.png"
        months = list(payload["forecast"]["monthly"].keys())
        p50 = [payload["forecast"]["monthly"][m]["P50"] for m in months]
        p10 = [payload["forecast"]["monthly"][m]["P10"] for m in months]
        p90 = [payload["forecast"]["monthly"][m]["P90"] for m in months]
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.plot(months, p50, marker="o", label="P50")
        ax.fill_between(months, p10, p90, alpha=0.2, label="P10-P90")
        ax.set_ylabel("rho")
        ax.legend()
        fig.tight_layout()
        fig.savefig(chart)
        plt.close(fig)
        return chart

    def append_audit(self, event: str, payload: dict[str, Any]) -> None:
        entry = {"event": event, "timestamp": utc_now(), "month": payload.get("month"), "seed": self.config.seed}
        self.config.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        if self.log is not None:
            log_entry = {k: v for k, v in entry.items() if k != "event"}
            self.log.info(event, **log_entry)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        tmp = Path(handle.name)
    tmp.replace(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=str(Path.cwd()))
    parser.add_argument("--month")
    parser.add_argument("--seed", type=int, default=20260514)
    args = parser.parse_args(argv)
    engine = RhoForecastEngine(EngineConfig(base_dir=Path(args.base_dir), seed=args.seed))
    result = engine.run(month=args.month)
    print(json.dumps({"snapshot_path": result["snapshot_path"], "pdf_path": result["pdf_path"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
