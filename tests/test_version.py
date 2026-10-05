from kherveref import __version__, version_string


def test_version_is_major_minor_only():
    assert __version__.count(".") == 1


def test_version_string_starts_with_version():
    assert version_string().startswith(f"v{__version__}")
