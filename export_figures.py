"""
Export publication-quality figures for the IEEE report.
Run from the OpenCharacterize root directory.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import AutoMinorLocator

from virtual_dut.adc_model import esp32_adc
from analysis.characterization_engine import CharacterizationEngine

Path("docs").mkdir(exist_ok=True)
dut = esp32_adc()
engine = CharacterizationEngine(resolution_bits=12, vref=3.3)

plt.rcParams.update({
    'font.family': 'serif',
    'font.size': 9,
    'axes.linewidth': 0.8,
    'lines.linewidth': 0.6,
    'grid.linewidth': 0.4,
    'grid.alpha': 0.3,
    'xtick.direction': 'in',
    'ytick.direction': 'in',
    'xtick.major.size': 3,
    'ytick.major.size': 3,
    'figure.dpi': 300,
})

# ============================================================
# Fig 1: INL and DNL
# ============================================================
ramp = np.linspace(0, 3.3, 200_000)
static = engine.static_characterization(ramp, dut.convert_array(ramp))

inl = static.inl
dnl = static.dnl
nz = np.where((inl != 0) | (dnl != 0))[0]
lo, hi = nz[0], nz[-1]
codes = np.arange(lo, hi + 1)
inl_v = inl[lo:hi + 1]
dnl_v = dnl[lo:hi + 1]

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(3.5, 3.8), gridspec_kw={'hspace': 0.45})

ax1.fill_between(codes, inl_v, 0, color='#0077b6', alpha=0.25, linewidth=0)
ax1.plot(codes, inl_v, color='#0077b6', linewidth=0.4)
ax1.axhline(0, color='gray', linestyle='--', linewidth=0.5)
ax1.set_title('Integral Nonlinearity (INL)', fontsize=9, fontweight='bold')
ax1.set_ylabel('INL (LSB)')
ax1.set_xlim(lo, hi)
ax1.xaxis.set_minor_locator(AutoMinorLocator())
ax1.grid(True, which='major', alpha=0.25)

ax2.fill_between(codes, dnl_v, 0, color='#d62828', alpha=0.25, linewidth=0)
ax2.plot(codes, dnl_v, color='#d62828', linewidth=0.4)
ax2.axhline(0, color='gray', linestyle='--', linewidth=0.5)
ax2.axhline(-1, color='#d62828', linestyle=':', linewidth=0.7, alpha=0.6)
ax2.text(hi - 200, -1.15, 'Missing code', fontsize=6, color='#d62828',
         ha='right', va='top', style='italic')
ax2.set_title('Differential Nonlinearity (DNL)', fontsize=9, fontweight='bold')
ax2.set_xlabel('Code')
ax2.set_ylabel('DNL (LSB)')
ax2.set_xlim(lo, hi)
ax2.xaxis.set_minor_locator(AutoMinorLocator())
ax2.grid(True, which='major', alpha=0.25)

fig.savefig('docs/fig_inl_dnl.png', dpi=300, bbox_inches='tight', pad_inches=0.05)
plt.close(fig)
print("Saved docs/fig_inl_dnl.png")

# ============================================================
# Fig 2: FFT Spectrum
# ============================================================
dut.reset()
fs = 100_000
n_fft = 8192
f_sig = 83 * fs / n_fft
t = np.arange(n_fft) / fs
sine = 1.65 + 1.4 * np.sin(2 * np.pi * f_sig * t)
dynamic = engine.dynamic_characterization(dut.convert_array(sine, sample_rate_hz=fs), fs, f_sig)

freqs_khz = dynamic.fft_freqs / 1000
mag = dynamic.fft_magnitude
peak_db = float(np.max(mag[1:]))
fund_bin = int(round(f_sig * n_fft / fs))

fig, ax = plt.subplots(figsize=(3.5, 2.4))
ax.plot(freqs_khz, mag, color='#0077b6', linewidth=0.3)
ax.fill_between(freqs_khz, mag, peak_db - 120, color='#0077b6', alpha=0.08, linewidth=0)
ax.set_ylim(peak_db - 120, peak_db + 12)
ax.set_xlim(0, fs / 2000)

# Label fundamental only with text, no arrow clutter
if fund_bin < len(mag):
    ax.annotate(r'$f_0$', xy=(freqs_khz[fund_bin], mag[fund_bin]),
                xytext=(freqs_khz[fund_bin] + 4, mag[fund_bin] - 2),
                fontsize=8, color='#006400',
                arrowprops=dict(arrowstyle='->', color='#006400', lw=0.6))

# Label only H2 and H3 to avoid clutter
harmonic_labels = {2: 'H2', 3: 'H3'}
offsets = {2: (1.5, 8), 3: (1.5, 8)}
for h_idx, label in harmonic_labels.items():
    h_bin = fund_bin * h_idx
    if h_bin < len(mag):
        dx, dy = offsets[h_idx]
        ax.annotate(label, xy=(freqs_khz[h_bin], mag[h_bin]),
                    xytext=(freqs_khz[h_bin] + dx, mag[h_bin] + dy),
                    fontsize=7, color='#8b0000',
                    arrowprops=dict(arrowstyle='->', color='#8b0000', lw=0.5))

ax.set_title(f'FFT Spectrum  |  SNR = {dynamic.snr_db:.1f} dB   '
             f'THD = {dynamic.thd_db:.1f} dB   ENOB = {dynamic.enob:.2f}',
             fontsize=8, fontweight='bold')
ax.set_xlabel('Frequency (kHz)')
ax.set_ylabel('Magnitude (dBFS)')
ax.xaxis.set_minor_locator(AutoMinorLocator())
ax.grid(True, which='major', alpha=0.25)

fig.savefig('docs/fig_fft.png', dpi=300, bbox_inches='tight', pad_inches=0.05)
plt.close(fig)
print("Saved docs/fig_fft.png")

# ============================================================
# Fig 3: Noise Distribution
# ============================================================
dut.reset()
dc_codes = dut.convert_array(np.full(10_000, 1.65), sample_rate_hz=fs)
noise = engine.noise_characterization(dc_codes, fs)

fig, ax = plt.subplots(figsize=(3.5, 2.0))
ax.bar(noise.histogram_bins[:-1], noise.histogram,
       width=0.8, color='#0077b6', alpha=0.65, edgecolor='#005f8a', linewidth=0.3)
ax.set_title(f'Noise Distribution  |  RMS = {noise.rms_noise_lsb:.2f} LSB   '
             f'Gaussian = {"Yes" if noise.is_gaussian else "No"}',
             fontsize=8, fontweight='bold')
ax.set_xlabel('ADC Code')
ax.set_ylabel('Count')
ax.xaxis.set_minor_locator(AutoMinorLocator())
ax.grid(True, which='major', alpha=0.25)

fig.savefig('docs/fig_noise.png', dpi=300, bbox_inches='tight', pad_inches=0.05)
plt.close(fig)
print("Saved docs/fig_noise.png")

print("\nDone. Replace TikZ placeholders in LaTeX with:")
print("  \\includegraphics[width=\\columnwidth]{fig_inl_dnl.png}")
print("  \\includegraphics[width=\\columnwidth]{fig_fft.png}")
print("  \\includegraphics[width=\\columnwidth]{fig_noise.png}")
