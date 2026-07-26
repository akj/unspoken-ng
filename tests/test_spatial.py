import math

import pytest

import spatial


DESKTOP_RECT = (0, 0, 1920, 1080)


@pytest.mark.parametrize(
    "obj_rect",
    [
        (0, 0, 0, 0),
        (1920, 0, 0, 0),
        (0, 1080, 0, 0),
        (1920, 1080, 0, 0),
        (910, 515, 100, 50),
        (0, 490, 100, 100),
        (1820, 490, 100, 100),
        (910, 0, 100, 100),
        (910, 980, 100, 100),
        (-500, -300, 80, 60),
        (-200, -100, 2500, 1400),
    ],
)
def test_position_is_a_unit_vector_for_object_sweep(obj_rect):
    x, y, z = spatial.position_for(obj_rect, DESKTOP_RECT)

    assert math.sqrt(x * x + y * y + z * z) == pytest.approx(
        1.0, abs=1e-9
    )


@pytest.mark.parametrize(
    ("first_rect", "second_rect", "clamped_angle_x"),
    [
        ((5000, 540, 0, 0), (10000, 540, 0, 0), 90.0),
        ((-5000, 540, 0, 0), (-10000, 540, 0, 0), -90.0),
    ],
)
def test_azimuth_is_clamped(first_rect, second_rect, clamped_angle_x):
    angle_y = -15.0
    rad_x = math.radians(clamped_angle_x)
    rad_y = math.radians(angle_y)
    expected = (
        math.sin(rad_x) * math.cos(rad_y),
        math.sin(rad_y),
        -math.cos(rad_x) * math.cos(rad_y),
    )

    assert spatial.position_for(first_rect, DESKTOP_RECT) == pytest.approx(
        expected, abs=1e-9
    )
    assert spatial.position_for(second_rect, DESKTOP_RECT) == pytest.approx(
        expected, abs=1e-9
    )


def test_zero_size_object_rect_is_well_defined():
    position = spatial.position_for((960, 540, 0, 0), DESKTOP_RECT)

    assert position == pytest.approx(
        (0.0, math.sin(math.radians(-15.0)), -math.cos(math.radians(-15.0))),
        abs=1e-9,
    )


def test_zero_size_desktop_rect_treats_object_as_centered():
    position = spatial.position_for((1000, -500, 100, 100), (100, 200, 0, 0))

    assert position == pytest.approx((0.0, 0.0, -1.0), abs=1e-9)
    assert all(math.isfinite(component) for component in position)


def test_object_fully_outside_desktop_bounds_is_well_defined():
    position = spatial.position_for((-3000, 2000, 100, 100), DESKTOP_RECT)

    assert all(math.isfinite(component) for component in position)
    assert math.sqrt(sum(component * component for component in position)) == (
        pytest.approx(1.0, abs=1e-9)
    )


def test_desktop_offset_is_accounted_for():
    centered = spatial.position_for((1060, 690, 0, 0), (100, 150, 1920, 1080))

    assert centered == pytest.approx(
        (0.0, math.sin(math.radians(-15.0)), -math.cos(math.radians(-15.0))),
        abs=1e-9,
    )
