"""Terrain slope limiting — max gradient between orthogonal neighbors."""

from world.roads import smooth_elevation_max_gradient


def test_smooth_reduces_cliff_between_two_tiles():
    raw = {(0, 0): 0.0, (1, 0): 10.0}
    out = smooth_elevation_max_gradient(raw, max_step=1.0, iterations=64)
    assert abs(out[(0, 0)] - out[(1, 0)]) <= 1.01


def test_smooth_preserves_flat():
    raw = {(0, 0): 2.0, (1, 0): 2.0, (0, 1): 2.0}
    out = smooth_elevation_max_gradient(raw, max_step=0.5, iterations=8)
    assert abs(out[(0, 0)] - 2.0) < 0.01
    assert abs(out[(1, 0)] - 2.0) < 0.01


def test_smooth_chain_spreads_over_path():
    """Total drop ~10 over 10 steps — max_step 1.0 should be satisfiable."""
    raw = {(i, 0): 10.0 - i for i in range(11)}
    out = smooth_elevation_max_gradient(raw, max_step=1.0, iterations=128)
    for i in range(10):
        assert abs(out[(i, 0)] - out[(i + 1, 0)]) <= 1.01
