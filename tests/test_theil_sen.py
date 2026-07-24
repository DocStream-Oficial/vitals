"""
test_theil_sen.py — Roadmap edad-corporal-credibilidad, Paso 3:
app/trends.py::theil_sen_slope (pendiente ROBUSTA para el pace of aging).

Cubre:
- serie limpia -> pendiente exacta (mediana de pendientes por pares == la
  pendiente real cuando no hay ruido).
- con un outlier -> la mediana lo ignora, pendiente idéntica a la serie limpia
  (a diferencia de linreg_slope/OLS, que el outlier SÍ distorsiona).
- n<4 -> None (contrato del roadmap: con 3 puntos o menos, un solo outlier ya
  contamina la mayoría de los pares).
- None-safe con valores None intercalados (se filtran antes de calcular).
"""
from __future__ import annotations

from app.trends import theil_sen_slope, linreg_slope


def test_clean_series_exact_slope():
    """Serie perfectamente lineal (pendiente 2.0) -> theil_sen_slope reproduce
    la pendiente exacta."""
    ys = [10 + 2 * i for i in range(10)]
    assert theil_sen_slope(ys) == 2.0


def test_robust_to_single_outlier():
    """Un solo outlier brutal en medio de la serie NO debe mover la
    pendiente — la mediana de pendientes por pares lo diluye entre los ~9
    pares "limpios" que siguen presentes. Contraste explícito: linreg_slope
    (OLS) SÍ se deja arrastrar por el mismo outlier."""
    ys = [10 + 2 * i for i in range(10)]
    ys_with_outlier = list(ys)
    ys_with_outlier[5] = 1000.0

    robust = theil_sen_slope(ys_with_outlier)
    assert robust == theil_sen_slope(ys)  # idéntico a la serie limpia

    ols = linreg_slope(ys_with_outlier)
    assert ols is not None
    assert ols != robust  # el OLS SÍ se distorsiona (referencia del contraste)


def test_less_than_4_points_returns_none():
    assert theil_sen_slope([1, 2, 3]) is None
    assert theil_sen_slope([1, 2]) is None
    assert theil_sen_slope([]) is None


def test_exactly_4_points_returns_value():
    assert theil_sen_slope([1, 2, 3, 4]) == 1.0


def test_none_values_filtered_before_computing():
    """Valores None intercalados se descartan antes de formar pares — no
    cuentan para el umbral n<4 ni distorsionan la mediana."""
    assert theil_sen_slope([1, None, 3, 5, 7]) == 2.0  # los no-None son 1,3,5,7 (pendiente 2)
    assert theil_sen_slope([1, None, None, 3]) is None  # solo 2 valores no-None -> <4


def test_negative_slope():
    ys = [50 - 3 * i for i in range(8)]
    assert theil_sen_slope(ys) == -3.0
