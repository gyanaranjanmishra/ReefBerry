"""Elements in an ICP test, their default targets, and the Elementals/Trace dosing factors.

Targets follow the Fauna Marin Reef ICP report (min, optimum, max). Elements the report gives
only a maximum for use the optimum from Fauna Marin's single-dose calculator.

Dosing uses the same formula as Fauna Marin's Elementals single-dose calculator
(https://lab.faunamarin.de/en/calc-elementals-once):

    dose_ml = a / 100 / b * litres * (target - measured)

and spreads it over enough days that no day adds more than `cap` ml per 100 litres.
ReefBerry isn't affiliated with Fauna Marin; check the product label before dosing.
Edit this file to change a default. Your own targets (ICP → Targets) override it.
"""

from __future__ import annotations

from dataclasses import dataclass

MG = "mg/l"
UG = "µg/l"

GROUPS = {
    "macro": "Macro elements and halogens",
    "nutrients": "Nutrients",
    "dynamic": "Dynamic trace elements",
    "trace": "Physiologically relevant trace elements",
    "other": "Other trace elements and pollutants",
}


@dataclass(frozen=True)
class Dose:
    product: str
    a: float
    b: float
    cap: float        # most ml per 100 litres in one day
    priority: int     # 1 = most urgent, as on the Fauna Marin report


@dataclass(frozen=True)
class Element:
    sym: str
    name: str
    unit: str
    group: str
    csv: str                      # column in the Fauna Marin CSV export
    min: float | None = None
    opt: float | None = None
    max: float | None = None
    dose: Dose | None = None
    decimals: int = 2


def _e(sym, name, unit, group, csv, lo=None, opt=None, hi=None, dose=None, dec=2):
    return Element(sym, name, unit, group, csv, lo, opt, hi, dose, dec)


ELEMENTS: list[Element] = [
    _e("Na", "Sodium", MG, "macro", "na", 9500, 10700, 11500, dec=0),
    _e("S", "Sulfur", MG, "macro", "s", 850, 900, 950, Dose("Elementals S", 10, 7, 28.5, 1), dec=0),
    _e("K", "Potassium", MG, "macro", "k", 380, 395, 420, Dose("Elementals K", 10, 10, 50, 1), dec=0),
    _e("B", "Boron", MG, "macro", "b", 3.8, 4.5, 5.5, Dose("Elementals B", 15, 1, 30, 1)),
    _e("Mg", "Magnesium", MG, "macro", "mg", 1200, 1350, 1450, Dose("Elementals Mg", 100, 100, 100, 1), dec=0),
    _e("Ca", "Calcium", MG, "macro", "ca", 400, 425, 440, dec=0),
    _e("Sr", "Strontium", MG, "macro", "sr", 6.5, 8.0, 9.0, Dose("Elementals Sr", 10, 2, 10, 1)),
    _e("Br", "Bromine", MG, "macro", "br", 55, 67, 75, Dose("Elementals Br", 10, 10, 10, 1), dec=1),
    _e("I", "Iodine", MG, "macro", "i", 0.055, 0.065, 0.080, Dose("Trace I", 1, 0.01, 3, 2), dec=3),
    _e("F", "Fluoride", MG, "macro", "fluoride", 0.9, 1.25, 1.6, Dose("Elementals F", 10, 0.2, 25, 2)),
    _e("P", "Phosphorus", MG, "nutrients", "p", 0.006, 0.033, 0.060, Dose("Elementals P", 1, 0.01, 2, 2), dec=3),
    _e("N", "Nitrogen (TNb)", MG, "nutrients", "tnb", 1, 5.5, 10, Dose("Elementals N", 1, 1, 2, 2)),
    _e("Si", "Silicon", MG, "nutrients", "si", 0.1, None, 0.2),
    _e("Zn", "Zinc", UG, "dynamic", "zn", 3, 5.5, 8, Dose("Trace Zn", 1, 10, 0.3, 3)),
    _e("V", "Vanadium", UG, "dynamic", "v", 2, 6, 10, Dose("Trace V", 1, 5, 0.4, 3)),
    _e("Cu", "Copper", UG, "dynamic", "cu", 2, 4, 6, Dose("Trace Cu", 1, 1, 2, 3)),
    _e("Ni", "Nickel", UG, "dynamic", "ni", 3, 4.5, 6, Dose("Trace Ni", 1, 4, 1, 3)),
    _e("Mo", "Molybdenum", UG, "dynamic", "mo", 10, 15, 20, Dose("Trace Mo", 1, 6, 1, 3), dec=1),
    _e("Ba", "Barium", UG, "trace", "ba", 5, 10, 50, Dose("Trace Ba", 10, 5, 10, 4), dec=1),
    _e("Co", "Cobalt", UG, "trace", "co", 0.02, 0.96, 1.9, Dose("Trace Co", 1, 4, 0.25, 4)),
    _e("Cr", "Chromium", UG, "trace", "cr", 0.05, 1.175, 2.3, Dose("Trace Cr", 1, 0.5, 1, 4)),
    _e("Fe", "Iron", UG, "trace", "fe", 0.05, 1.275, 2.5, Dose("Trace Fe", 1, 4, 0.25, 4)),
    _e("Li", "Lithium", UG, "trace", "li", 180, 265, 350, Dose("Trace Li", 1, 15, 2, 4), dec=0),
    _e("Mn", "Manganese", UG, "trace", "mn", 0.1, 0.175, 0.25, Dose("Trace Mn", 1, 4, 0.25, 4)),
    _e("Se", "Selenium", UG, "trace", "se", 0.9, 3.2, 5.5, Dose("Trace Se", 1, 0.5, 2, 4)),
    _e("Rb", "Rubidium", UG, "trace", "rb", 70, 100, 130, Dose("Trace Rb", 1, 5, 5, 4), dec=1),
    _e("Al", "Aluminium", UG, "other", "al", None, None, 30, dec=1),
    _e("Sb", "Antimony", UG, "other", "sb", None, None, 10),
    _e("As", "Arsenic", UG, "other", "as", None, None, 0),
    _e("Be", "Beryllium", UG, "other", "be", None, None, 0),
    _e("Pb", "Lead", UG, "other", "pb", None, None, 0),
    _e("Cd", "Cadmium", UG, "other", "cd", None, None, 0),
    _e("La", "Lanthanum", UG, "other", "la", None, None, 10),
    _e("Hg", "Mercury", UG, "other", "hg", None, None, 0),
    _e("Ag", "Silver", UG, "other", "ag", None, None, 10),
    _e("Ti", "Titanium", UG, "other", "ti", None, None, 3.5),
    _e("W", "Tungsten", UG, "other", "w", None, None, 30),
    _e("Sn", "Tin", UG, "other", "sn", None, None, 10),
    _e("Zr", "Zirconium", UG, "other", "zr", None, None, 2.2),
    _e("Cs", "Caesium", UG, "other", "cs"),
    _e("Ga", "Gallium", UG, "other", "ga"),
    _e("Hf", "Hafnium", UG, "other", "hf"),
    _e("Nd", "Neodymium", UG, "other", "nd"),
    _e("Te", "Tellurium", UG, "other", "te"),
    _e("Th", "Thorium", UG, "other", "th"),
    _e("Tl", "Thallium", UG, "other", "tl"),
    _e("U", "Uranium", UG, "other", "u"),
]

BY_SYM = {e.sym: e for e in ELEMENTS}
