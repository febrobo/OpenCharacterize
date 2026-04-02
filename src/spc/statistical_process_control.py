"""
Statistical process control for characterization data.

Cpk/Cp: Process capability indices
Gage R&R: Measurement system analysis (ANOVA method)
Control charts: X-bar and R with standard constants
Trend detection: Western Electric rules
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
import json


@dataclass
class CpkResult:
    parameter: str
    measured_values: np.ndarray
    mean: float
    std: float
    usl: float
    lsl: float
    cp: float
    cpu: float
    cpl: float
    cpk: float
    ppm_out_of_spec: float
    verdict: str

    def to_dict(self) -> dict:
        return {
            "parameter": self.parameter,
            "n_samples": len(self.measured_values),
            "mean": round(self.mean, 4), "std": round(self.std, 4),
            "USL": self.usl, "LSL": self.lsl,
            "Cp": round(self.cp, 3), "Cpk": round(self.cpk, 3),
            "PPM_out_of_spec": round(self.ppm_out_of_spec, 1),
            "verdict": self.verdict,
        }


@dataclass
class GageRRResult:
    total_variation: float
    repeatability: float
    reproducibility: float
    gage_rr: float
    part_variation: float
    gage_rr_pct: float
    ndc: float
    verdict: str

    def to_dict(self) -> dict:
        return {
            "total_variation": round(self.total_variation, 4),
            "repeatability_EV": round(self.repeatability, 4),
            "reproducibility_AV": round(self.reproducibility, 4),
            "gage_rr": round(self.gage_rr, 4),
            "part_variation": round(self.part_variation, 4),
            "pct_GRR": round(self.gage_rr_pct, 1),
            "ndc": round(self.ndc, 1),
            "verdict": self.verdict,
        }


@dataclass
class ControlChartData:
    parameter: str
    subgroup_means: np.ndarray
    subgroup_ranges: np.ndarray
    x_bar_cl: float
    x_bar_ucl: float
    x_bar_lcl: float
    r_cl: float
    r_ucl: float
    r_lcl: float
    out_of_control: List[int]
    timestamps: List[str]


class SPCEngine:

    # Standard control chart constants (subgroup sizes 2-10)
    _A2 = {2: 1.880, 3: 1.023, 4: 0.729, 5: 0.577, 6: 0.483, 7: 0.419, 8: 0.373, 9: 0.337, 10: 0.308}
    _D3 = {2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0.076, 8: 0.136, 9: 0.184, 10: 0.223}
    _D4 = {2: 3.267, 3: 2.575, 4: 2.282, 5: 2.115, 6: 2.004, 7: 1.924, 8: 1.864, 9: 1.816, 10: 1.777}

    def compute_cpk(
        self, values: np.ndarray, usl: float, lsl: float, parameter: str = "",
    ) -> CpkResult:
        values = np.asarray(values, dtype=float)
        mu = float(np.mean(values))
        sigma = max(float(np.std(values, ddof=1)), 1e-15)

        cp = (usl - lsl) / (6 * sigma)
        cpu = (usl - mu) / (3 * sigma)
        cpl = (mu - lsl) / (3 * sigma)
        cpk = min(cpu, cpl)

        from scipy.stats import norm
        ppm = (1 - norm.cdf(usl, mu, sigma) + norm.cdf(lsl, mu, sigma)) * 1e6

        if cpk >= 1.33:
            verdict = "Capable (Cpk >= 1.33)"
        elif cpk >= 1.0:
            verdict = "Marginal (1.00 <= Cpk < 1.33)"
        else:
            verdict = "Not Capable (Cpk < 1.00)"

        return CpkResult(
            parameter=parameter, measured_values=values,
            mean=mu, std=sigma, usl=usl, lsl=lsl,
            cp=cp, cpu=cpu, cpl=cpl, cpk=cpk,
            ppm_out_of_spec=ppm, verdict=verdict,
        )

    def gage_rr(
        self, measurements: np.ndarray,
        n_parts: int, n_operators: int, n_replicates: int,
    ) -> GageRRResult:
        """
        ANOVA-based Gage R&R.
        measurements shape: (n_parts, n_operators, n_replicates)
        """
        data = measurements.reshape(n_parts, n_operators, n_replicates)
        part_means = np.mean(data, axis=(1, 2))
        operator_means = np.mean(data, axis=(0, 2))

        within_var = np.mean(np.var(data, axis=2, ddof=1))
        repeatability = np.sqrt(within_var)

        operator_var = np.var(operator_means, ddof=1)
        reproducibility = np.sqrt(max(0, operator_var - within_var / (n_parts * n_replicates)))

        part_var = np.var(part_means, ddof=1)
        part_variation = np.sqrt(max(0, part_var - within_var / (n_operators * n_replicates)))

        grr_var = repeatability**2 + reproducibility**2
        grr = np.sqrt(grr_var)
        total = np.sqrt(grr_var + part_variation**2)
        pct_grr = (grr / total * 100) if total > 0 else 0
        ndc = 1.41 * (part_variation / grr) if grr > 0 else 999

        if pct_grr <= 10:
            verdict = "Acceptable (%GRR <= 10%)"
        elif pct_grr <= 30:
            verdict = "Marginal (10% < %GRR <= 30%)"
        else:
            verdict = "Unacceptable (%GRR > 30%)"

        return GageRRResult(
            total_variation=total, repeatability=repeatability,
            reproducibility=reproducibility, gage_rr=grr,
            part_variation=part_variation, gage_rr_pct=pct_grr,
            ndc=ndc, verdict=verdict,
        )

    def control_chart(
        self, values: np.ndarray, subgroup_size: int = 5,
        parameter: str = "", timestamps: Optional[List[str]] = None,
    ) -> ControlChartData:
        n = len(values)
        n_groups = n // subgroup_size
        groups = values[: n_groups * subgroup_size].reshape(n_groups, subgroup_size)

        means = np.mean(groups, axis=1)
        ranges = np.ptp(groups, axis=1)
        x_bar = float(np.mean(means))
        r_bar = float(np.mean(ranges))

        A2 = self._A2.get(subgroup_size, 0.577)
        D3 = self._D3.get(subgroup_size, 0)
        D4 = self._D4.get(subgroup_size, 2.115)

        x_ucl = x_bar + A2 * r_bar
        x_lcl = x_bar - A2 * r_bar
        r_ucl = D4 * r_bar
        r_lcl = D3 * r_bar

        ooc = [i for i, (m, r) in enumerate(zip(means, ranges))
               if m > x_ucl or m < x_lcl or r > r_ucl]

        if timestamps is None:
            timestamps = [f"Run {i+1}" for i in range(n_groups)]

        return ControlChartData(
            parameter=parameter, subgroup_means=means, subgroup_ranges=ranges,
            x_bar_cl=x_bar, x_bar_ucl=x_ucl, x_bar_lcl=x_lcl,
            r_cl=r_bar, r_ucl=r_ucl, r_lcl=r_lcl,
            out_of_control=ooc, timestamps=timestamps[:n_groups],
        )

    def detect_trends(self, values: np.ndarray, window: int = 7) -> Dict[str, bool]:
        """Western Electric rules for out-of-control detection."""
        mu = np.mean(values)
        sigma = np.std(values, ddof=1)

        flags = {
            "beyond_3sigma": bool(np.any(np.abs(values - mu) > 3 * sigma)),
            "7_consecutive_one_side": False,
            "7_consecutive_trend": False,
            "2_of_3_beyond_2sigma": False,
        }

        above = values > mu
        for i in range(len(values) - window + 1):
            if all(above[i:i + window]) or all(~above[i:i + window]):
                flags["7_consecutive_one_side"] = True
                break

        diffs = np.diff(values)
        for i in range(len(diffs) - window + 2):
            if all(diffs[i:i + window - 1] > 0) or all(diffs[i:i + window - 1] < 0):
                flags["7_consecutive_trend"] = True
                break

        beyond_2s = np.abs(values - mu) > 2 * sigma
        for i in range(len(values) - 2):
            if sum(beyond_2s[i:i + 3]) >= 2:
                flags["2_of_3_beyond_2sigma"] = True
                break

        return flags


def run_spc_demo():
    """Run characterization 30 times, compute Cpk and control charts."""
    from virtual_dut.adc_model import esp32_adc
    from analysis.characterization_engine import CharacterizationEngine

    dut = esp32_adc()
    engine = CharacterizationEngine(resolution_bits=12, vref=3.3)
    spc = SPCEngine()

    print("Running 30 characterization iterations...")
    print("-" * 50)

    enob_vals, snr_vals, inl_vals = [], [], []
    fs = 100_000
    n_fft = 8192
    f_sig = 83 * fs / n_fft  # Coherent sampling

    for run in range(30):
        dut.rng = np.random.default_rng(42 + run)

        t = np.arange(n_fft) / fs
        sine = 1.65 + 1.4 * np.sin(2 * np.pi * f_sig * t)
        dyn = engine.dynamic_characterization(dut.convert_array(sine, sample_rate_hz=fs), fs, f_sig)

        ramp = np.linspace(0, 3.3, 100_000)
        static = engine.static_characterization(ramp, dut.convert_array(ramp))

        enob_vals.append(dyn.enob)
        snr_vals.append(dyn.snr_db)
        inl_vals.append(max(abs(static.inl_max), abs(static.inl_min)))

        if (run + 1) % 10 == 0:
            print(f"  {run + 1}/30 complete")

    enob_arr = np.array(enob_vals)

    print(f"\n{'PROCESS CAPABILITY':^50}")
    print("-" * 50)
    for name, arr, usl, lsl in [
        ("ENOB", enob_arr, 12.0, 9.0),
        ("SNR", np.array(snr_vals), 80.0, 55.0),
        ("INL_max", np.array(inl_vals), 12.0, 0.0),
    ]:
        cpk = spc.compute_cpk(arr, usl=usl, lsl=lsl, parameter=name)
        print(f"  {name:8s}  Cpk={cpk.cpk:.3f}  mean={cpk.mean:.2f}  std={cpk.std:.3f}  {cpk.verdict}")

    print(f"\n{'CONTROL CHART (ENOB)':^50}")
    print("-" * 50)
    chart = spc.control_chart(enob_arr, subgroup_size=5, parameter="ENOB")
    print(f"  CL={chart.x_bar_cl:.3f}  UCL={chart.x_bar_ucl:.3f}  LCL={chart.x_bar_lcl:.3f}")
    print(f"  Out-of-control: {chart.out_of_control}")
    print(f"  Trends: {spc.detect_trends(enob_arr)}")


if __name__ == "__main__":
    run_spc_demo()
