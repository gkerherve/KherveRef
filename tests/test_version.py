from kherveref import __version__, version_string


def test_version_is_major_minor_only():
    assert __version__.count(".") == 1


def test_version_string_starts_with_version():
    assert version_string().startswith(f"v{__version__}")


def test_stamped_build_file_is_used_without_git(tmp_path, monkeypatch):
    import kherveref
    monkeypatch.setattr(kherveref, "BUILD_FILE", tmp_path / "BUILD")
    (tmp_path / "BUILD").write_text("57+abc1234\n", encoding="ascii")
    assert kherveref._stamped_build_info() == (57, "abc1234")
    (tmp_path / "BUILD").write_text("garbage", encoding="ascii")
    assert kherveref._stamped_build_info() is None


def test_release_version_is_major_minor_count():
    from kherveref import release_version
    parts = release_version().split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)
