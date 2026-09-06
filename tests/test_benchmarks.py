from benchmarks.measure import percentile


def test_percentile_matches_known_values():
    data = [10, 20, 30, 40, 50]
    assert percentile(data, 50) == 30
    assert percentile(data, 0) == 10
    assert percentile(data, 100) == 50


def test_percentile_interpolates_between_points():
    data = [1, 2, 3, 4]
    assert percentile(data, 100) == 4
    assert 2.5 < percentile(data, 60) < 3.0
