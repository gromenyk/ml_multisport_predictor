import unittest

from metrics.route_evaluator import evaluate_route, parse_gpx


def _gpx(slope=0.07):
    points = []
    meters_per_step = 111.195
    for index in range(12):
        elevation = 100 + index * meters_per_step * slope
        points.append(f'<trkpt lat="0" lon="{index * 0.001}"><ele>{elevation}</ele></trkpt>')
    return (
        '<?xml version="1.0"?><gpx version="1.1"><trk><trkseg>'
        + "".join(points) + '</trkseg></trk></gpx>'
    ).encode("utf-8")


class RouteEvaluatorTests(unittest.TestCase):
    def test_finds_five_minute_window_in_five_to_ten_percent_slope(self):
        parsed = parse_gpx(_gpx())
        result = evaluate_route(parsed, {
            "duration_seconds": 300,
            "slope_bands": ["5-10%"],
        }, speed_kmh=12)
        self.assertTrue(result["compatible"])
        self.assertGreater(result["candidate_count"], 0)
        self.assertAlmostEqual(result["candidates"][0]["distance_km"], 1.0, places=2)
        self.assertAlmostEqual(result["candidates"][0]["mean_slope_percent"], 7.0, places=1)

    def test_rejects_route_outside_protocol_band(self):
        parsed = parse_gpx(_gpx())
        result = evaluate_route(parsed, {
            "duration_seconds": 300,
            "slope_bands": ["10-15%"],
        }, speed_kmh=12)
        self.assertFalse(result["compatible"])

    def test_requires_elevation(self):
        with self.assertRaisesRegex(ValueError, "elevación"):
            parse_gpx(b'<gpx><rte><rtept lat="0" lon="0"/><rtept lat="0" lon="1"/></rte></gpx>')


if __name__ == "__main__":
    unittest.main()
