from kherveref.latex import (latex_to_unicode, protect_case, strip_braces,
                             unicode_to_latex)


def test_decode_accents_and_symbols():
    assert latex_to_unicode(r'M{\"u}ller') == "Müller"
    assert strip_braces(latex_to_unicode(r'{\"{U}}ber {DNA}')) == "Über DNA"
    assert latex_to_unicode(r"\'{e}t\'e") == "été"
    assert latex_to_unicode(r"Fran\c{c}ois") == "François"
    assert latex_to_unicode(r"\v{S}ar\'{\i}") == "Šarí"
    assert latex_to_unicode(r"{\ss}") == "ß"
    assert latex_to_unicode(r"pp. 10--20 --- A \& B") == "pp. 10–20 — A & B"


def test_math_is_untouched():
    assert latex_to_unicode(r"$\alpha$--Fe") == r"$\alpha$–Fe"


def test_encode_round_trip():
    for s in ("Müller", "François", "Šarí", "Straße", "Ørsted", "A & B 10%",
              "10–20"):
        assert strip_braces(latex_to_unicode(unicode_to_latex(s))) == s


def test_utf8_mode_keeps_letters():
    assert unicode_to_latex("Müller & Co", ascii_only=False) == r"Müller \& Co"


def test_protect_case():
    assert protect_case("XPS of TiO2 at pH 7 by McDonald") == \
        "{XPS} of {TiO2} at {pH} 7 by {McDonald}"
    assert protect_case("Deep learning") == "Deep learning"
    assert protect_case("Already {DNA} and $X$") == "Already {DNA} and $X$"
