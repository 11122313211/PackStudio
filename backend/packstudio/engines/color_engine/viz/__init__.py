"""色域可视化子模块（TECH-DESIGN §2.5 导出面）。"""

from __future__ import annotations

from packstudio.engines.color_engine.viz.gamut import (GamutOverlap, GamutSample, TransformFn,
                                   VolumeSeries, WavelengthMark,
                                   dominant_wavelength, estimate_gamut_volume,
                                   gamut_overlap, plot_ab_slice,
                                   plot_ab_slice_animation, plot_chromaticity,
                                   plot_gamut_overlap, plot_volume_trend,
                                   sample_cmyk_gamut, sample_rgb_gamut)
from packstudio.engines.color_engine.viz.hdr import (CICP_TO_TRANSFER, SUPPORTED_TRANSFERS,
                                 TRANSFER_LABELS, gamma22_oetf, hlg_inverse_oetf,
                                 hlg_oetf, pq_eotf, pq_oetf, reference_points,
                                 srgb_oetf, transfer_curve_points)
from packstudio.engines.color_engine.viz.theme import THEMES, get_theme, set_theme

__all__ = [
    "GamutSample",
    "GamutOverlap",
    "VolumeSeries",
    "WavelengthMark",
    "TransformFn",
    "THEMES",
    "sample_rgb_gamut",
    "sample_cmyk_gamut",
    "estimate_gamut_volume",
    "gamut_overlap",
    "dominant_wavelength",
    "plot_chromaticity",
    "plot_ab_slice",
    "plot_ab_slice_animation",
    "plot_gamut_overlap",
    "plot_volume_trend",
    "SUPPORTED_TRANSFERS",
    "TRANSFER_LABELS",
    "CICP_TO_TRANSFER",
    "pq_eotf",
    "pq_oetf",
    "hlg_oetf",
    "hlg_inverse_oetf",
    "srgb_oetf",
    "gamma22_oetf",
    "transfer_curve_points",
    "reference_points",
    "get_theme",
    "set_theme",
]
