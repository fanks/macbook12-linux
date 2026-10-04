"""Response requirements for the smooth curve; no device or desktop access."""
from dataclasses import replace
import itertools
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trackpad"))
from backend import Backend, Curve, _factor, _spline


def curves():
    yield from (Curve(*values) for values in itertools.product((.3, .9), (1., 1.8), (1., 4.)))
    yield Curve(.5, 1.25, 1.92)
    yield Curve(.569047619047619, 1.5, 2.)
    randomizer = random.Random(20261004)
    for _ in range(16):
        yield Curve(randomizer.uniform(.3, .9), randomizer.uniform(1., 1.8), randomizer.uniform(1., 4.))


def sampled_factor(points, speed):
    """libinput interpolates output, not gain; include its linear tail."""
    index = min(int(speed // 10), len(points) - 2)
    (x0, y0), (x1, y1) = points[index:index + 2]
    output = y0 + (y1 - y0) * (speed - x0) / (x1 - x0)
    return output / (speed * .2968 / 25.4)


class CurveShapeTests(unittest.TestCase):
    def test_dense_gain_is_monotone_and_stays_between_anchors(self):
        for curve in curves():
            with self.subTest(curve=curve):
                spline = _spline(curve)
                points = Backend.propose(curve)
                expected = (curve.slow, .9 * curve.medium, (387 / 130) * curve.fast, 4.8 * curve.fast)
                previous_ideal = previous_sampled = curve.slow
                for tick in range(1, 6301):
                    speed = tick / 10.
                    ideal = _factor(speed, *spline)
                    sampled = sampled_factor(points, speed)
                    self.assertTrue(math.isfinite(ideal) and math.isfinite(sampled))
                    self.assertGreaterEqual(ideal + 1e-12, previous_ideal)
                    self.assertGreaterEqual(sampled + 1e-12, previous_sampled)
                    lower, upper = ((expected[0], expected[0]) if speed <= 10 else
                                    (expected[0], expected[1]) if speed <= 100 else
                                    (expected[1], expected[2]) if speed <= 400 else
                                    (expected[2], expected[3]) if speed <= 520 else
                                    (expected[3], expected[3]))
                    self.assertGreaterEqual(ideal + 1e-12, lower)
                    self.assertLessEqual(ideal, upper + 1e-12)
                    self.assertGreaterEqual(sampled + 1e-12, lower)
                    self.assertLessEqual(sampled, upper + 1e-12)
                    previous_ideal, previous_sampled = ideal, sampled

    def test_precision_and_bounded_tail_remain_constant(self):
        for curve in curves():
            with self.subTest(curve=curve):
                spline = _spline(curve)
                points = Backend.propose(curve)
                for speed in (0., .01, 1., 5., 9.99, 10.):
                    self.assertEqual(_factor(speed, *spline), curve.slow)
                    if speed:
                        self.assertAlmostEqual(sampled_factor(points, speed), curve.slow, places=13)
                for speed in (520., 525., 630., 1000., 1_000_000.):
                    self.assertEqual(_factor(speed, *spline), 4.8 * curve.fast)
                    self.assertAlmostEqual(sampled_factor(points, speed), 4.8 * curve.fast, places=12)

    def test_ideal_curve_has_continuous_slopes_at_all_joins(self):
        # Test the intended smooth curve. libinput's sampled approximation has
        # small derivative corners and must not be advertised as exactly C1.
        epsilon = .0001
        for curve in curves():
            with self.subTest(curve=curve):
                spline = _spline(curve)
                for speed in (10., 100., 400., 520.):
                    value = _factor(speed, *spline)
                    left = (value - _factor(speed - epsilon, *spline)) / epsilon
                    right = (_factor(speed + epsilon, *spline) - value) / epsilon
                    self.assertAlmostEqual(left, right, delta=2e-6)
                    if speed in (10., 520.):
                        self.assertAlmostEqual(left, 0., delta=2e-6)
                        self.assertAlmostEqual(right, 0., delta=2e-6)

    def test_each_control_keeps_the_other_two_anchors(self):
        original = Curve(.5, 1.25, 1.92)
        anchors = {"slow": (10., 1.), "medium": (100., .9), "fast": (400., 387 / 130)}
        for changed, limits in (("slow", (.3, .9)), ("medium", (1., 1.8)), ("fast", (1., 4.))):
            for value in limits:
                curve = replace(original, **{changed: value})
                with self.subTest(control=changed, value=value):
                    spline = _spline(curve)
                    points = Backend.propose(curve)
                    for control, (speed, scale) in anchors.items():
                        expected = scale * (value if control == changed else getattr(original, control))
                        self.assertEqual(_factor(speed, *spline), expected)
                        self.assertAlmostEqual(sampled_factor(points, speed), expected, places=13)


if __name__ == "__main__":
    unittest.main()
