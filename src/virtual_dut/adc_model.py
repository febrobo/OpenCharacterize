"""
Virtual ADC model with configurable non-idealities for characterization
without physical hardware. Models real-world SAR ADC behavior including
capacitor mismatch, thermal noise, 1/f noise, and supply coupling.

Reference: IEEE Std 1241-2010, Analog Devices AN-835
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class ADCArchitecture(Enum):
    SAR = "successive_approximation"
    SIGMA_DELTA = "sigma_delta"
    PIPELINE = "pipeline"
    FLASH = "flash"


@dataclass
class ADCSpec:
    """Datasheet specification limits for pass/fail evaluation."""
    resolution_bits: int = 12
    vref: float = 3.3
    architecture: ADCArchitecture = ADCArchitecture.SAR
    sample_rate_hz: int = 100_000
    input_range_min: float = 0.0
    input_range_max: float = 3.3
    inl_max_lsb: float = 3.0
    dnl_max_lsb: float = 1.5
    enob_min: float = 9.5
    snr_min_db: float = 58.0
    thd_max_db: float = -60.0
    offset_error_max_mv: float = 30.0
    gain_error_max_pct: float = 2.0

    @property
    def num_codes(self) -> int:
        return 2 ** self.resolution_bits

    @property
    def lsb_voltage(self) -> float:
        return self.vref / self.num_codes


@dataclass
class ADCImperfections:
    """
    Configurable non-idealities mapped to physical causes:

    offset_error      -> comparator offset in SAR logic
    gain_error        -> reference voltage inaccuracy
    inl_bow_amplitude -> systematic capacitor mismatch
    dnl_variation     -> manufacturing process variation
    thermal_noise     -> kT/C noise floor
    flicker_noise     -> 1/f noise from MOSFET channel traps
    ps_noise          -> VDD ripple coupling into VREF
    dead_zone_*       -> input buffer clipping
    missing_codes     -> severe DNL causing code width = 0
    """
    offset_error_lsb: float = 15.0
    gain_error_pct: float = 1.5
    thermal_noise_lsb_rms: float = 3.0
    flicker_noise_amplitude: float = 1.5
    power_supply_noise_lsb: float = 2.0
    ps_noise_freq_hz: float = 60.0
    dead_zone_low_v: float = 0.13
    dead_zone_high_v: float = 3.1
    inl_bow_amplitude_lsb: float = 8.0
    inl_wiggle_amplitude_lsb: float = 2.0
    dnl_variation_lsb: float = 0.4
    missing_code_indices: list = field(default_factory=lambda: [1847, 2048, 3291])
    seed: int = 42


class VirtualADC:
    """
    Physics-based virtual SAR ADC. Applies imperfections in the same
    order as real hardware: input clamping -> gain -> offset -> INL
    distortion -> additive noise -> quantization -> missing code remap.
    """

    def __init__(
        self,
        spec: Optional[ADCSpec] = None,
        imperfections: Optional[ADCImperfections] = None,
    ):
        self.spec = spec or ADCSpec()
        self.imp = imperfections or ADCImperfections()
        self.rng = np.random.default_rng(self.imp.seed)
        self._inl_profile = self._generate_inl_profile()
        self._dnl_deviations = self._generate_dnl_deviations()
        self._transfer_fn = self._build_transfer_function()
        self._sample_count = 0

    def _generate_inl_profile(self) -> np.ndarray:
        """
        Realistic INL from three physical sources:
        1. Parabolic bow from systematic capacitor mismatch
        2. Periodic bumps at binary-weighted transitions (MSB = largest)
        3. Locally-correlated random variation
        """
        n = self.spec.num_codes
        codes = np.arange(n, dtype=np.float64)
        norm = codes / (n - 1)

        bow = self.imp.inl_bow_amplitude_lsb * 4 * norm * (1 - norm)

        # Binary-weighted transitions: MSB (bit 0 here) = 1 cycle = one
        # big bump at midscale with full amplitude. Higher indices model
        # successively lower-significance bits with more cycles and less
        # amplitude. This matches SAR capacitor array mismatch physics.
        periodic = np.zeros(n)
        for bit in range(self.spec.resolution_bits):
            n_cycles = 2 ** bit
            amp = self.imp.inl_wiggle_amplitude_lsb / (bit + 1)
            periodic += amp * np.sin(2 * np.pi * n_cycles * norm)

        local = self.rng.normal(0, self.imp.inl_wiggle_amplitude_lsb * 0.3, n)
        kernel = np.ones(16) / 16
        local = np.convolve(local, kernel, mode='same')

        inl = bow + periodic + local
        inl -= np.linspace(inl[0], inl[-1], n)
        return inl

    def _generate_dnl_deviations(self) -> np.ndarray:
        n = self.spec.num_codes
        dnl = self.rng.normal(0, self.imp.dnl_variation_lsb, n)

        for idx in self.imp.missing_code_indices:
            if 0 <= idx < n:
                dnl[idx] = -1.0

        # Larger DNL at major binary transitions (capacitor switching)
        for bit in range(2, self.spec.resolution_bits):
            tc = 2 ** bit
            if tc < n:
                dnl[tc] += self.rng.normal(0, self.imp.dnl_variation_lsb * 2)

        return dnl

    def _build_transfer_function(self) -> np.ndarray:
        """
        Build voltage-to-code thresholds with all static non-idealities.
        Enforces monotonicity after applying INL -- in real SAR ADCs,
        the transfer function is monotonic but codes with reversed
        thresholds collapse to zero width (becoming missing codes).
        """
        n = self.spec.num_codes
        thresholds = np.arange(n + 1, dtype=np.float64) * self.spec.lsb_voltage

        gain_factor = 1.0 + (self.imp.gain_error_pct / 100.0)
        thresholds /= gain_factor

        offset_v = self.imp.offset_error_lsb * self.spec.lsb_voltage
        thresholds += offset_v

        for i in range(min(n, len(thresholds) - 1)):
            thresholds[i] += self._inl_profile[i] * self.spec.lsb_voltage

        # Enforce monotonicity -- codes where thresholds reversed become
        # zero-width (missing codes), matching real SAR ADC behavior
        thresholds = np.maximum.accumulate(thresholds)

        return thresholds

    def convert(self, voltage: float, timestamp_s: float = 0.0) -> int:
        self._sample_count += 1

        if voltage < self.imp.dead_zone_low_v:
            return 0
        if voltage > self.imp.dead_zone_high_v:
            return self.spec.num_codes - 1

        noise = (
            self.rng.normal(0, self.imp.thermal_noise_lsb_rms)
            + self.imp.flicker_noise_amplitude * np.sin(
                2 * np.pi * 7.3 * timestamp_s + self.rng.uniform(0, 2 * np.pi))
            + self.imp.power_supply_noise_lsb * np.sin(
                2 * np.pi * self.imp.ps_noise_freq_hz * timestamp_s)
        )

        effective_v = voltage + noise * self.spec.lsb_voltage
        code = int(np.searchsorted(self._transfer_fn, effective_v) - 1)
        code = max(0, min(self.spec.num_codes - 1, code))

        if code in self.imp.missing_code_indices:
            code += 1 if self.rng.random() > 0.5 else -1
            code = max(0, min(self.spec.num_codes - 1, code))

        return code

    def convert_array(
        self,
        voltages: np.ndarray,
        sample_rate_hz: Optional[int] = None,
        start_time_s: float = 0.0,
    ) -> np.ndarray:
        """Vectorized bulk conversion for performance."""
        rate = sample_rate_hz or self.spec.sample_rate_hz
        n_samples = len(voltages)
        timestamps = start_time_s + np.arange(n_samples) / rate

        v = voltages.copy().astype(np.float64)

        thermal = self.rng.normal(0, self.imp.thermal_noise_lsb_rms, n_samples)
        flicker = self.imp.flicker_noise_amplitude * np.sin(
            2 * np.pi * 7.3 * timestamps + self.rng.uniform(0, 2 * np.pi))
        supply = self.imp.power_supply_noise_lsb * np.sin(
            2 * np.pi * self.imp.ps_noise_freq_hz * timestamps)

        v += (thermal + flicker + supply) * self.spec.lsb_voltage

        codes = np.searchsorted(self._transfer_fn, v) - 1
        codes = np.where(voltages < self.imp.dead_zone_low_v, 0, codes)
        codes = np.where(voltages > self.imp.dead_zone_high_v, self.spec.num_codes - 1, codes)
        codes = np.clip(codes, 0, self.spec.num_codes - 1)

        for mc in self.imp.missing_code_indices:
            mask = codes == mc
            shift = np.where(self.rng.random(n_samples) > 0.5, 1, -1)
            codes = np.where(mask, codes + shift, codes)

        codes = np.clip(codes, 0, self.spec.num_codes - 1)
        self._sample_count += n_samples
        return codes.astype(np.int32)

    def get_identity(self) -> dict:
        return {
            "manufacturer": "OpenCharacterize",
            "model": f"VADC-{self.spec.resolution_bits}B-{self.spec.architecture.value}",
            "serial": f"SIM-{self.imp.seed:04d}",
            "firmware": "1.0.0",
        }

    def get_spec(self) -> ADCSpec:
        return self.spec

    def get_inl_profile(self) -> np.ndarray:
        return self._inl_profile.copy()

    def reset(self):
        self.rng = np.random.default_rng(self.imp.seed)
        self._sample_count = 0


# --- Pre-configured DUT profiles ---

def esp32_adc() -> VirtualADC:
    """ESP32 12-bit SAR — known for poor linearity and high noise."""
    return VirtualADC(
        spec=ADCSpec(
            resolution_bits=12, vref=3.3,
            architecture=ADCArchitecture.SAR,
            sample_rate_hz=100_000,
            inl_max_lsb=12.0, dnl_max_lsb=2.0,
            enob_min=9.0, snr_min_db=55.0,
            thd_max_db=-55.0,
            offset_error_max_mv=40, gain_error_max_pct=3.0,
        ),
        imperfections=ADCImperfections(
            offset_error_lsb=15.0, gain_error_pct=1.5,
            thermal_noise_lsb_rms=3.5, flicker_noise_amplitude=2.0,
            power_supply_noise_lsb=2.5, dead_zone_low_v=0.13,
            dead_zone_high_v=3.1, inl_bow_amplitude_lsb=10.0,
            inl_wiggle_amplitude_lsb=3.0, dnl_variation_lsb=0.5,
            missing_code_indices=[1847, 2048, 3291],
        ),
    )


def stm32_adc() -> VirtualADC:
    """STM32F4-class 12-bit SAR — significantly better than ESP32."""
    return VirtualADC(
        spec=ADCSpec(
            resolution_bits=12, vref=3.3,
            architecture=ADCArchitecture.SAR,
            sample_rate_hz=2_400_000,
            inl_max_lsb=2.5, dnl_max_lsb=1.0,
            enob_min=10.5, snr_min_db=62.0,
            thd_max_db=-70.0,
            offset_error_max_mv=5, gain_error_max_pct=0.5,
        ),
        imperfections=ADCImperfections(
            offset_error_lsb=3.0, gain_error_pct=0.3,
            thermal_noise_lsb_rms=1.2, flicker_noise_amplitude=0.5,
            power_supply_noise_lsb=0.8, dead_zone_low_v=0.0,
            dead_zone_high_v=3.3, inl_bow_amplitude_lsb=2.0,
            inl_wiggle_amplitude_lsb=0.8, dnl_variation_lsb=0.2,
            missing_code_indices=[],
        ),
    )


def ideal_adc(bits: int = 12) -> VirtualADC:
    """Perfect ADC — quantization noise only. Used as reference baseline."""
    return VirtualADC(
        spec=ADCSpec(resolution_bits=bits, vref=3.3),
        imperfections=ADCImperfections(
            offset_error_lsb=0, gain_error_pct=0,
            thermal_noise_lsb_rms=0.3,
            flicker_noise_amplitude=0, power_supply_noise_lsb=0,
            dead_zone_low_v=0, dead_zone_high_v=3.3,
            inl_bow_amplitude_lsb=0, inl_wiggle_amplitude_lsb=0,
            dnl_variation_lsb=0.01, missing_code_indices=[],
        ),
    )


if __name__ == "__main__":
    dut = esp32_adc()
    print(f"DUT: {dut.get_identity()}")
    print(f"LSB: {dut.spec.lsb_voltage * 1000:.3f} mV")

    voltages = np.linspace(0, 3.3, 10000)
    codes = dut.convert_array(voltages)
    print(f"Ramp: {len(voltages)} pts, codes {codes.min()}-{codes.max()}, "
          f"{len(np.unique(codes))} unique, {4096 - len(np.unique(codes))} missing")
