"""
ADC characterization engine implementing IEEE Std 1241 methodology.

Static:  INL, DNL, offset error, gain error, missing codes
Dynamic: SNR, THD, SFDR, SINAD, ENOB via windowed FFT
Noise:   RMS noise, distribution analysis, spectral decomposition
Histogram: Code density method per Analog Devices AN-835
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List
from scipy import signal as sig
from scipy.fft import fft, fftfreq


@dataclass
class StaticResults:
    inl: np.ndarray
    dnl: np.ndarray
    inl_max: float
    inl_min: float
    dnl_max: float
    dnl_min: float
    offset_error_lsb: float
    offset_error_mv: float
    gain_error_pct: float
    gain_error_lsb: float
    missing_codes: List[int]
    num_codes_tested: int
    monotonic: bool
    transfer_function: Optional[np.ndarray] = None


@dataclass
class DynamicResults:
    snr_db: float
    thd_db: float
    sinad_db: float
    sfdr_db: float
    enob: float
    signal_freq_hz: float
    harmonics_db: List[float]
    noise_floor_db: float
    fft_magnitude: np.ndarray
    fft_freqs: np.ndarray
    num_samples: int
    sample_rate_hz: float


@dataclass
class NoiseResults:
    rms_noise_lsb: float
    rms_noise_uv: float
    pp_noise_lsb: float
    mean_code: float
    std_code: float
    histogram: np.ndarray
    histogram_bins: np.ndarray
    is_gaussian: bool
    noise_spectrum: Optional[np.ndarray] = None
    noise_freqs: Optional[np.ndarray] = None


@dataclass
class HistogramResults:
    code_counts: np.ndarray
    dnl_histogram: np.ndarray
    inl_histogram: np.ndarray
    missing_codes: List[int]
    total_samples: int
    input_type: str


@dataclass
class CharacterizationReport:
    dut_id: dict
    static: Optional[StaticResults] = None
    dynamic: Optional[DynamicResults] = None
    noise: Optional[NoiseResults] = None
    histogram: Optional[HistogramResults] = None
    pass_fail: dict = field(default_factory=dict)


class CharacterizationEngine:
    """
    Works with both virtual ADCs and real hardware (via SCPI).
    Accepts arrays of stimulus voltages and measured codes.
    """

    def __init__(self, resolution_bits: int = 12, vref: float = 3.3):
        self.bits = resolution_bits
        self.vref = vref
        self.num_codes = 2 ** resolution_bits
        self.lsb = vref / self.num_codes

    def static_characterization(
        self,
        stimulus_voltages: np.ndarray,
        measured_codes: np.ndarray,
    ) -> StaticResults:
        """
        Ramp test: sweep a linear ramp, capture output codes,
        extract transfer function and compute INL/DNL.
        Trims analysis to the first and last populated code
        (standard practice per IEEE 1241 to handle dead zones).
        """
        n_codes = self.num_codes
        code_thresholds = np.full(n_codes, np.nan)
        code_counts = np.zeros(n_codes, dtype=int)

        for code in range(n_codes):
            mask = measured_codes == code
            if np.any(mask):
                code_thresholds[code] = np.mean(stimulus_voltages[mask])
                code_counts[code] = np.sum(mask)

        # Find valid code range. Exclude rail codes (0 and max) which
        # accumulate dead-zone and saturation hits in any real ADC.
        interior = code_counts[1:-1]
        interior_pop = np.where(interior > 0)[0] + 1  # offset back to full index
        if len(interior_pop) < 2:
            return StaticResults(
                inl=np.zeros(n_codes), dnl=np.zeros(n_codes),
                inl_max=0, inl_min=0, dnl_max=0, dnl_min=0,
                offset_error_lsb=0, offset_error_mv=0,
                gain_error_pct=0, gain_error_lsb=0,
                missing_codes=[], num_codes_tested=0,
                monotonic=True, transfer_function=code_thresholds,
            )

        first_code = int(interior_pop[0])
        last_code = int(interior_pop[-1])
        valid_range = last_code - first_code + 1

        # Offset error: deviation of first transition from ideal
        if not np.isnan(code_thresholds[first_code]):
            offset_error_v = code_thresholds[first_code] - (first_code + 0.5) * self.lsb
        else:
            offset_error_v = 0.0

        offset_error_lsb = offset_error_v / self.lsb
        offset_error_mv = offset_error_v * 1000

        # Gain error: endpoint fit across valid range
        actual_span = code_thresholds[last_code] - code_thresholds[first_code]
        ideal_span = (last_code - first_code) * self.lsb
        if ideal_span > 0:
            gain_error_pct = ((actual_span - ideal_span) / ideal_span) * 100
            gain_error_lsb = gain_error_pct * n_codes / 100
        else:
            gain_error_pct, gain_error_lsb = 0.0, 0.0

        # DNL: only for codes in the valid range
        # ideal_count based on samples that fall in the valid range
        valid_samples = np.sum(code_counts[first_code:last_code + 1])
        ideal_count = valid_samples / valid_range if valid_range > 0 else 1

        dnl = np.zeros(n_codes)
        for k in range(first_code, last_code + 1):
            dnl[k] = (code_counts[k] / ideal_count) - 1.0 if ideal_count > 0 else 0.0

        # INL: cumulative DNL with endpoint correction (valid range only)
        inl = np.zeros(n_codes)
        inl[first_code:last_code + 1] = np.cumsum(dnl[first_code:last_code + 1])
        valid_inl = inl[first_code:last_code + 1]
        valid_inl -= np.linspace(valid_inl[0], valid_inl[-1], len(valid_inl))
        inl[first_code:last_code + 1] = valid_inl

        # Missing codes: only within valid range
        missing = [int(k) for k in range(first_code, last_code + 1) if code_counts[k] == 0]
        monotonic = bool(np.all(dnl[first_code:last_code + 1] > -1.0))

        return StaticResults(
            inl=inl, dnl=dnl,
            inl_max=float(np.max(valid_inl)), inl_min=float(np.min(valid_inl)),
            dnl_max=float(np.max(dnl[first_code:last_code + 1])),
            dnl_min=float(np.min(dnl[first_code:last_code + 1])),
            offset_error_lsb=float(offset_error_lsb),
            offset_error_mv=float(offset_error_mv),
            gain_error_pct=float(gain_error_pct),
            gain_error_lsb=float(gain_error_lsb),
            missing_codes=missing,
            num_codes_tested=int(np.sum(code_counts[first_code:last_code + 1] > 0)),
            monotonic=monotonic,
            transfer_function=code_thresholds,
        )

    def dynamic_characterization(
        self,
        codes: np.ndarray,
        sample_rate_hz: float,
        signal_freq_hz: Optional[float] = None,
        num_harmonics: int = 7,
        window: str = "blackmanharris",
    ) -> DynamicResults:
        """
        FFT-based dynamic test. Feed a coherent-sampled sine wave,
        extract signal/harmonic/noise power bins.
        """
        n = len(codes)
        voltages = codes.astype(np.float64) * self.lsb
        voltages -= np.mean(voltages)

        win_fn = {
            "blackmanharris": sig.windows.blackmanharris,
            "hann": sig.windows.hann,
            "flattop": sig.windows.flattop,
        }
        w = win_fn.get(window, lambda n: np.ones(n))(n)
        coherent_gain = np.sum(w) / n

        spectrum = fft(voltages * w)
        freqs = fftfreq(n, 1.0 / sample_rate_hz)
        half_n = n // 2

        mag = np.abs(spectrum[:half_n]) / (n * coherent_gain)
        mag[1:] *= 2
        freqs_pos = freqs[:half_n]
        mag_db = 20 * np.log10(mag + 1e-20)

        # Detect fundamental
        if signal_freq_hz is None:
            search_start = max(1, int(10 * n / sample_rate_hz))
            fund_bin = search_start + np.argmax(mag[search_start:half_n // 2])
            signal_freq_hz = float(freqs_pos[fund_bin])
        else:
            fund_bin = int(round(signal_freq_hz * n / sample_rate_hz))

        # Signal power: capture full main lobe (+/-7 bins covers BH main lobe)
        lobe_width = 7
        sig_bins = range(max(1, fund_bin - lobe_width), min(half_n, fund_bin + lobe_width + 1))
        signal_power = np.sum(mag[list(sig_bins)] ** 2)

        # Harmonic powers (same lobe width for consistency)
        harmonic_power = 0.0
        harmonics_db = []
        for h in range(2, num_harmonics + 1):
            h_bin = fund_bin * h
            if h_bin >= half_n:
                harmonics_db.append(-999.0)
                continue
            h_bins = range(max(1, h_bin - lobe_width), min(half_n, h_bin + lobe_width + 1))
            h_pwr = np.sum(mag[list(h_bins)] ** 2)
            harmonic_power += h_pwr
            harmonics_db.append(float(10 * np.log10(h_pwr + 1e-30)))

        total_power = np.sum(mag[1:] ** 2)
        noise_power = max(total_power - signal_power - harmonic_power, 1e-30)

        snr_db = 10 * np.log10(signal_power / noise_power)
        thd_db = 10 * np.log10(harmonic_power / signal_power) if harmonic_power > 0 else -120.0
        sinad_db = 10 * np.log10(signal_power / (noise_power + harmonic_power))
        sfdr_db = float(mag_db[fund_bin] - np.max([
            mag_db[fund_bin * h] if fund_bin * h < half_n else -999
            for h in range(2, num_harmonics + 1)
        ]))
        enob = (sinad_db - 1.76) / 6.02
        n_noise_bins = half_n - len(sig_bins) - lobe_width * 2 * min(num_harmonics, half_n // fund_bin - 1)
        noise_floor_db = 10 * np.log10(noise_power / max(n_noise_bins, 1))

        return DynamicResults(
            snr_db=float(snr_db), thd_db=float(thd_db),
            sinad_db=float(sinad_db), sfdr_db=float(sfdr_db),
            enob=float(enob), signal_freq_hz=float(signal_freq_hz),
            harmonics_db=harmonics_db, noise_floor_db=float(noise_floor_db),
            fft_magnitude=mag_db, fft_freqs=freqs_pos,
            num_samples=n, sample_rate_hz=float(sample_rate_hz),
        )

    def noise_characterization(
        self,
        codes: np.ndarray,
        sample_rate_hz: float = 100_000,
    ) -> NoiseResults:
        """DC input noise analysis: distribution, RMS, spectral content."""
        mean_code = float(np.mean(codes))
        std_code = float(np.std(codes))
        pp_noise = float(np.max(codes) - np.min(codes))

        bins = np.arange(
            max(0, int(mean_code) - 20),
            min(self.num_codes, int(mean_code) + 21),
        )
        hist, bin_edges = np.histogram(codes, bins=bins)

        from scipy.stats import kurtosis as kurt_fn
        k = kurt_fn(codes.astype(float), fisher=True)
        is_gaussian = abs(k) < 1.0

        noise_mag, noise_freqs = None, None
        n = len(codes)
        if n > 64:
            noise_seq = codes.astype(float) - mean_code
            w = sig.windows.blackmanharris(n)
            spectrum = fft(noise_seq * w)
            half_n = n // 2
            noise_mag = 20 * np.log10(
                np.abs(spectrum[:half_n]) / (n * np.sum(w) / n) + 1e-20)
            noise_freqs = fftfreq(n, 1.0 / sample_rate_hz)[:half_n]

        return NoiseResults(
            rms_noise_lsb=std_code,
            rms_noise_uv=std_code * self.lsb * 1e6,
            pp_noise_lsb=pp_noise,
            mean_code=mean_code, std_code=std_code,
            histogram=hist, histogram_bins=bin_edges,
            is_gaussian=bool(is_gaussian),
            noise_spectrum=noise_mag, noise_freqs=noise_freqs,
        )

    def histogram_characterization(
        self,
        codes: np.ndarray,
        input_type: str = "sine",
        signal_amplitude_codes: Optional[float] = None,
    ) -> HistogramResults:
        """
        Code density method. For sine input, expected density follows
        p(code) = 1 / (pi * sqrt(A^2 - V^2)).
        """
        n_codes = self.num_codes
        total = len(codes)
        counts = np.bincount(codes, minlength=n_codes).astype(float)

        if input_type == "sine":
            if signal_amplitude_codes is None:
                signal_amplitude_codes = (np.max(codes) - np.min(codes)) / 2.0

            center = (np.max(codes) + np.min(codes)) / 2.0
            A = signal_amplitude_codes
            expected = np.zeros(n_codes)

            for k in range(n_codes):
                v_low = np.clip((k - center) / A, -1, 1) if A > 0 else 0
                v_high = np.clip((k + 1 - center) / A, -1, 1) if A > 0 else 0
                expected[k] = (total / np.pi) * (np.arcsin(v_high) - np.arcsin(v_low))
        else:
            expected = np.full(n_codes, total / n_codes)

        dnl = np.zeros(n_codes)
        for k in range(n_codes):
            dnl[k] = (counts[k] / expected[k]) - 1.0 if expected[k] > 0 else 0.0

        inl = np.cumsum(dnl)
        inl -= np.linspace(inl[0], inl[-1], n_codes)
        missing = [int(k) for k in range(n_codes) if counts[k] == 0 and expected[k] > 5]

        return HistogramResults(
            code_counts=counts.astype(int),
            dnl_histogram=dnl, inl_histogram=inl,
            missing_codes=missing, total_samples=total,
            input_type=input_type,
        )

    def evaluate_pass_fail(self, static, dynamic, noise, spec) -> dict:
        """Compare measured results against datasheet specification limits."""
        results = {}

        if static:
            inl_peak = max(abs(static.inl_max), abs(static.inl_min))
            dnl_peak = max(abs(static.dnl_max), abs(static.dnl_min))
            results["INL_max"] = {"measured": inl_peak, "limit": spec.inl_max_lsb,
                                  "unit": "LSB", "pass": inl_peak <= spec.inl_max_lsb}
            results["DNL_max"] = {"measured": dnl_peak, "limit": spec.dnl_max_lsb,
                                  "unit": "LSB", "pass": dnl_peak <= spec.dnl_max_lsb}
            results["Offset_Error"] = {"measured": abs(static.offset_error_mv),
                                       "limit": spec.offset_error_max_mv, "unit": "mV",
                                       "pass": abs(static.offset_error_mv) <= spec.offset_error_max_mv}
            results["Gain_Error"] = {"measured": abs(static.gain_error_pct),
                                     "limit": spec.gain_error_max_pct, "unit": "%",
                                     "pass": abs(static.gain_error_pct) <= spec.gain_error_max_pct}
            results["Missing_Codes"] = {"measured": len(static.missing_codes), "limit": 0,
                                        "unit": "codes", "pass": len(static.missing_codes) == 0}

        if dynamic:
            results["SNR"] = {"measured": dynamic.snr_db, "limit": spec.snr_min_db,
                              "unit": "dB", "pass": dynamic.snr_db >= spec.snr_min_db}
            results["THD"] = {"measured": dynamic.thd_db, "limit": spec.thd_max_db,
                              "unit": "dB", "pass": dynamic.thd_db <= spec.thd_max_db}
            results["ENOB"] = {"measured": dynamic.enob, "limit": spec.enob_min,
                               "unit": "bits", "pass": dynamic.enob >= spec.enob_min}

        results["OVERALL"] = {"pass": all(r["pass"] for r in results.values())}
        return results
