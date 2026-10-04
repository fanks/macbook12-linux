"""Unprivileged C/Python agreement test; all config paths are private temporary files."""
import itertools
import json
import math
import os
from pathlib import Path
import random
import subprocess
import tempfile
import sys
import shlex

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trackpad"))
from backend import Backend, BackendError, Curve

HERE = Path(__file__).resolve().parent


def main():
    counts = {"accepted": 0, "rejected": 0, "compared_motion_values": 0}
    min_increment = math.inf
    max_output = 0.0
    cases = []
    for i, values in enumerate(itertools.product((.3, .9), (1., 1.8), (1., 4.))):
        cases.append((f"corner-{i}", Curve(*values)))
    cases += [("baseline", Curve(.5, 1.25, 1.92)),
              ("fractional", Curve(.57, 1.5, 2.))]
    randomizer = random.Random(20261004)
    for i in range(256):
        cases.append((f"interior-{i}", Curve(randomizer.uniform(.3, .9), randomizer.uniform(1., 1.8), randomizer.uniform(1., 4.))))
    for index, (low, high) in enumerate(((.3, .9), (1., 1.8), (1., 4.))):
        for name, value in (("just-inside-low", math.nextafter(low, high)), ("just-inside-high", math.nextafter(high, low))):
            values = [.5, 1.25, 1.92]
            values[index] = value
            cases.append((f"axis-{index}-{name}", Curve(*values)))

    with tempfile.TemporaryDirectory(prefix="private-config-") as directory:
        base = Path(directory)
        harness = base / "curve-harness"
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", *shlex.split(os.environ.get("CPPFLAGS", "")), str(HERE / "curve-harness.c"), "-o", str(harness), "-ldl", "-pthread"], check=True)
        first, second = base / "curve.conf", base / "medium.conf"

        def check(name, curve_data, medium_data, expected):
            nonlocal min_increment, max_output
            for path, data in ((first, curve_data), (second, medium_data)):
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(data, encoding="ascii")
                    path.chmod(0o600)
            result = subprocess.run([str(harness), str(first), str(second)],
                                    check=True, capture_output=True, text=True, timeout=5,
                                    env={**os.environ, "LC_ALL": "C"})
            c = json.loads(result.stdout)
            try:
                py_curve = Backend(base).read().curve
            except BackendError:
                py_curve = None
            assert c["accepted"] == expected, (name, c)
            assert (py_curve is not None) == expected, (name, py_curve)
            if not expected:
                counts["rejected"] += 1
                return
            py = tuple(y for _, y in Backend.propose(py_curve))
            assert len(c["motion"]) == len(py) == 64, name
            # Float.hex equality checks the exact binary value after round-trip.
            assert all(a.hex() == b.hex() for a, b in zip(map(float, c["motion"]), py)), name
            assert (c["slow"], c["medium"], c["fast"]) == (py_curve.slow, py_curve.medium, py_curve.fast), name
            assert c["api_status"] == [0, 0, 0], (name, c["api_status"])
            assert list(map(float.hex, map(float, c["constant"]))) == [0.0.hex(), (.9 * (.2968 * 1000. / 2413.)).hex()], name
            assert all(math.isfinite(y) and 0 <= y <= 10000 for y in py), name
            increments = [b-a for a, b in zip(py, py[1:])]
            assert min(increments) > 0, name
            assert math.isclose(py[-1] / 630., py[-2] / 620., rel_tol=1e-15), name
            min_increment = min(min_increment, *increments)
            max_output = max(max_output, *py)
            counts["accepted"] += 1
            counts["compared_motion_values"] += 64

        for name, curve in cases:
            check(name, f"{curve.slow!r} {curve.fast!r}\n", f"{curve.medium!r}\n", True)
        check("optional-medium-absent", "0.5 4\n", None, True)
        check("whitespace", " \n0.5\t4 \n", " \n1.8 \n", True)

        for index, (low, high) in enumerate(((.3, .9), (1., 1.8), (1., 4.))):
            for name, value in (("below", math.nextafter(low, -math.inf)), ("above", math.nextafter(high, math.inf)),
                                ("nan", math.nan), ("inf", math.inf), ("negative-inf", -math.inf)):
                values = [.5, 1.25, 1.92]
                values[index] = value
                slow, medium, fast = values
                check(f"axis-{index}-{name}", f"{slow!r} {fast!r}\n", f"{medium!r}\n", False)
        for name, first_data, second_data in (
            ("curve-absent", None, "1.25\n"),
            ("both-absent", None, None),
            ("curve-empty", "", "1.25\n"),
            ("curve-one-token", "0.5\n", "1.25\n"),
            ("curve-extra-token", "0.5 2 extra\n", "1.25\n"),
            ("curve-text", "text 2\n", "1.25\n"),
            ("curve-trailing-junk", "0.5 2x\n", "1.25\n"),
            ("medium-empty", "0.5 2\n", ""),
            ("medium-extra-token", "0.5 2\n", "1.25 extra\n"),
            ("medium-text", "0.5 2\n", "text\n"),
            ("medium-trailing-junk", "0.5 2\n", "1.25x\n"),
            ("medium-unapproved-2", "0.5 4\n", "2\n"),
        ):
            check(name, first_data, second_data, False)

    report = {
        "result": "PASS", "counts": counts,
        "motion_agreement": "exact IEEE-754 binary equality for every sample",
        "libinput_api": "MOTION, SCROLL and FALLBACK set_points accepted every valid curve; no apply/device/context calls",
        "minimum_sample_increment": min_increment, "maximum_output_speed": max_output,
        "isolation": "private temporary config files only; no input device, desktop settings, real curve files, sudo or service commands",
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
