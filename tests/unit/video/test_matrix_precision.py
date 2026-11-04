from __future__ import annotations

import pytest

from manimagent.video.notation import matrix_text


@pytest.mark.parametrize("value", [1.0, 1.0000001, 1.0000002, -123456.789, 1e-12, 1000000.0])
def test_matrix_labels_roundtrip_without_changing_math(value):
    assert float(matrix_text(value)) == value
