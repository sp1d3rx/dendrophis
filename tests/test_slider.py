"""Tests for custom settings Slider widget."""

from __future__ import annotations

from dendrophis.ui.screens.settings import Slider


def test_slider_detents_generation() -> None:
    """Test that the slider correctly generates step-based detents."""
    slider = Slider(value=65536, min_value=4096, max_value=131072)

    # Detents: 4k, 32k, 64k, 96k, 128k
    expected_detents = [4096, 32768, 65536, 98304, 131072]
    assert slider.detents == expected_detents


def test_slider_value_snapping() -> None:
    """Test that the slider snaps initial values to the nearest detent."""
    # 50k should snap to 64k (65536) since it is closer than 32k (32768)
    slider = Slider(value=51200, min_value=4096, max_value=131072)
    assert slider.value == 65536


def test_slider_navigation() -> None:
    """Test that left and right key navigation works correctly."""
    slider = Slider(value=65536, min_value=4096, max_value=131072)

    assert slider.value == 65536

    # Move left
    slider.key_left()
    assert slider.value == 32768

    # Move left again
    slider.key_left()
    assert slider.value == 4096

    # Move left beyond bounds
    slider.key_left()
    assert slider.value == 4096

    # Move right
    slider.key_right()
    assert slider.value == 32768
