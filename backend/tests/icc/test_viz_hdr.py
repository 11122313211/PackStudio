"""viz.hdr 传输特性（ROADMAP v0.5.11）：PQ(ST 2084)/HLG(BT.2100) 数学锁定。

公式经权威规范核查（ST 2084 常数 m1=2610/16384、m2=2523/32、c1=3424/4096、
c2=2413/128、c3=2392/128；BT.2100 HLG OETF 分段 √(3E₀) / a·ln(12E₀−b)+c）。
参考值以数值测试锁定：PQ 50% 信号 ↔ ≈92.25 nits；HLG E'(1/12)=0.5、
E'(1)=1（规范常数 a/b/c 为 8 位十进制，E'(1) 存在 ~5e-9 截断偏差，用
相应容差断言）；OETF/EOTF 往返恒等 <1e-9。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy
import pytest

from packstudio.engines.color_engine.errors import VizError  # noqa: E402
from packstudio.engines.color_engine.viz.hdr import (  # noqa: E402
    CICP_TO_TRANSFER, HLG_A, HLG_B, HLG_C, MAX_POINTS, MIN_POINTS,
    PQ_C1, PQ_C2, PQ_C3, PQ_M1, PQ_M2, PQ_MAX_NITS, SUPPORTED_TRANSFERS,
    gamma22_oetf, hlg_inverse_oetf, hlg_oetf, pq_eotf, pq_oetf,
    reference_points, srgb_oetf, transfer_curve_points,
)

_ONE_THIRD = 1.0 / 12.0


class TestPqMath:
    def test_constants_exact(self) -> None:
        """ST 2084 常数精确分数形式。"""
        assert PQ_M1 == 2610 / 16384
        assert PQ_M2 == 2523 / 32
        assert PQ_C1 == 3424 / 4096
        assert PQ_C2 == 2413 / 128
        assert PQ_C3 == 2392 / 128
        assert PQ_C1 == PQ_C3 - PQ_C2 + 1.0  # 规范恒等式
        assert PQ_MAX_NITS == 10000.0

    def test_eotf_reference_values(self) -> None:
        """EOTF 参考值：0→0、50%→≈92.25 nits、100%→10000 nits。"""
        assert pq_eotf(0.0) == pytest.approx(0.0, abs=1e-12)
        assert pq_eotf(0.5) == pytest.approx(92.25, abs=0.05)
        assert pq_eotf(1.0) == pytest.approx(10000.0, abs=1e-6)
        # 数组入 → 数组出，形状保持
        arr = pq_eotf(numpy.array([0.0, 0.5, 1.0]))
        assert isinstance(arr, numpy.ndarray) and arr.shape == (3,)
        assert arr[1] == pytest.approx(92.25, abs=0.05)

    def test_eotf_monotonic(self) -> None:
        """EOTF 在全信号域单调递增。"""
        grid = numpy.linspace(0.0, 1.0, 513)
        luma = numpy.asarray(pq_eotf(grid))
        assert numpy.all(numpy.diff(luma) >= 0.0)

    def test_oetf_roundtrip_identity(self) -> None:
        """OETF/EOTF 互为精确逆：往返误差 < 1e-9。"""
        grid = numpy.linspace(1e-4, 1.0, 513)
        assert float(numpy.max(numpy.abs(pq_oetf(pq_eotf(grid)) - grid))) < 1e-9
        luma = numpy.linspace(0.0, 10000.0, 513)
        assert float(numpy.max(numpy.abs(pq_eotf(pq_oetf(luma)) - luma))) < 1e-6


class TestHlgMath:
    def test_constants(self) -> None:
        """BT.2100 常数：b = 1−4a、c = 0.5 − a·ln(4a)。"""
        assert HLG_A == pytest.approx(0.17883277)
        assert HLG_B == pytest.approx(1.0 - 4.0 * HLG_A)  # 0.28466892
        assert HLG_C == pytest.approx(0.5 - HLG_A * numpy.log(4.0 * HLG_A))

    def test_oetf_reference_values(self) -> None:
        """OETF 参考值：0→0、1/12→0.5、1→1（截断容差）、1/24→√(3/24)。"""
        assert hlg_oetf(0.0) == pytest.approx(0.0, abs=1e-12)
        assert hlg_oetf(_ONE_THIRD) == pytest.approx(0.5, abs=1e-12)
        # 规范常数 8 位十进制下的截断：E'(1) = 1 − ~5e-9
        assert hlg_oetf(1.0) == pytest.approx(1.0, abs=1e-8)
        assert hlg_oetf(1.0 / 24.0) == pytest.approx(numpy.sqrt(3.0 / 24.0),
                                                     abs=1e-12)

    def test_oetf_piecewise_continuity(self) -> None:
        """1/12 处两分支连续：左极限 = 右值。"""
        left = float(hlg_oetf(_ONE_THIRD - 1e-12))
        right = float(hlg_oetf(_ONE_THIRD))
        assert left == pytest.approx(right, abs=1e-9)

    def test_inverse_oetf_roundtrip(self) -> None:
        """逆 OETF 往返恒等 < 1e-12。"""
        grid = numpy.linspace(0.0, 1.0, 513)
        err = numpy.max(numpy.abs(hlg_inverse_oetf(hlg_oetf(grid)) - grid))
        assert float(err) < 1e-12

    def test_oetf_monotonic_and_bounds(self) -> None:
        """OETF 单调递增且值域 [0,1]。"""
        grid = numpy.linspace(0.0, 1.0, 513)
        sig = numpy.asarray(hlg_oetf(grid))
        assert numpy.all(numpy.diff(sig) >= 0.0)
        assert sig.min() >= 0.0 and sig.max() <= 1.0


class TestSdrBaselines:
    def test_srgb_oetf_reference(self) -> None:
        """sRGB OETF：线性 0.0031308 以下线性段、1→1、50% 线性→≈0.735。"""
        assert srgb_oetf(0.0) == pytest.approx(0.0, abs=1e-12)
        assert srgb_oetf(0.0031308) == pytest.approx(0.0031308 * 12.92, abs=1e-12)
        assert srgb_oetf(1.0) == pytest.approx(1.0, abs=1e-12)
        assert srgb_oetf(0.5) == pytest.approx(1.055 * 0.5 ** (1 / 2.4) - 0.055,
                                               abs=1e-12)

    def test_gamma22_oetf(self) -> None:
        """Gamma 2.2 OETF：纯幂、端点锁定。"""
        assert gamma22_oetf(0.0) == pytest.approx(0.0, abs=1e-12)
        assert gamma22_oetf(0.5) == pytest.approx(0.5 ** (1 / 2.2), abs=1e-12)
        assert gamma22_oetf(1.0) == pytest.approx(1.0, abs=1e-12)


class TestCurvePointsAndValidation:
    def test_curve_points_shape_and_bounds(self) -> None:
        """n 点、x∈[1e-4,1] 严格递增、y∈[0,1] 单调不减、末端 (1,1)。"""
        n = 65
        for name in SUPPORTED_TRANSFERS:
            pts = transfer_curve_points(name, n)
            assert len(pts) == n
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            assert xs[0] == pytest.approx(1e-4, abs=1e-9)
            assert xs[-1] == pytest.approx(1.0, abs=1e-12)
            assert all(b > a for a, b in zip(xs, xs[1:]))
            assert all(0.0 <= y <= 1.0 for y in ys)
            assert all(b >= a for a, b in zip(ys, ys[1:]))
            assert ys[-1] == pytest.approx(1.0, abs=1e-6)

    def test_curve_points_validation(self) -> None:
        """非法 transfer / n 越界 → VizError。"""
        with pytest.raises(VizError):
            transfer_curve_points("hdtv")
        with pytest.raises(VizError):
            transfer_curve_points("pq", MIN_POINTS - 1)
        with pytest.raises(VizError):
            transfer_curve_points("pq", MAX_POINTS + 1)
        with pytest.raises(VizError):
            transfer_curve_points("pq", True)  # bool 拒绝

    def test_reference_points_consistent(self) -> None:
        """参考点自洽：PQ input = pq_eotf(0.5)/10000；HLG input = 1/12。"""
        refs = {r["transfer"]: r for r in reference_points()}
        assert refs["pq"]["signal"] == 0.5
        assert refs["pq"]["input"] == pytest.approx(
            float(pq_eotf(0.5)) / PQ_MAX_NITS, abs=1e-8)
        assert refs["pq"]["nits"] == pytest.approx(92.25, abs=0.05)
        assert refs["hlg"]["input"] == pytest.approx(_ONE_THIRD, abs=1e-8)
        assert refs["hlg"]["signal"] == 0.5

    def test_cicp_mapping_matches_validate(self) -> None:
        """CICP 映射与 icc.validate 的 transfer_label 同口径（16=PQ/18=HLG）。"""
        assert CICP_TO_TRANSFER == {16: "pq", 18: "hlg"}

    def test_transfer_validation_rejects_nonfinite(self) -> None:
        """非有限输入 → VizError（全部传输函数）。"""
        for fn in (pq_eotf, pq_oetf, hlg_oetf, hlg_inverse_oetf,
                   srgb_oetf, gamma22_oetf):
            with pytest.raises(VizError):
                fn(float("nan"))
            with pytest.raises(VizError):
                fn(float("inf"))
        with pytest.raises(VizError):
            pq_oetf(-1.0)  # 负亮度
        with pytest.raises(VizError):
            hlg_oetf(-0.1)  # 负场景线性
