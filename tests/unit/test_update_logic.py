from grippo.manage.update import changelog_between, parse_version, python_compatible


def test_semver_ordering_with_prereleases():
    texts = ["v0.2.0", "v0.10.0", "v0.2.0-rc.1", "v0.2.0-rc.2", "v1.0.0", "v0.2.1"]
    ordered = sorted((parse_version(t) for t in texts), key=lambda v: v.sort_key())
    assert [v.text for v in ordered] == ["0.2.0-rc.1", "0.2.0-rc.2", "0.2.0", "0.2.1", "0.10.0",
                                         "1.0.0"]
    assert parse_version("v0.2.0-rc.1").stable is False
    assert parse_version("latest") is None


def test_changelog_between():
    text = "# Changelog\n\n## [0.3.0] - x\n- c\n\n## [0.2.1] - y\n- b\n\n## [0.2.0]\n- a\n"
    out = changelog_between(text, parse_version("0.2.0"), parse_version("0.3.0"))
    assert "0.3.0" in out and "0.2.1" in out and "- a" not in out


def test_python_compatible():
    assert python_compatible(">=3.11", (3, 14, 0))
    assert not python_compatible(">=3.11", (3, 10, 9))
    assert python_compatible(">=3.11,<4", (3, 12, 1))
    assert not python_compatible("garbage", (3, 12, 1))
