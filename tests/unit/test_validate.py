from pathlib import Path

import pytest

from grippo.errors import ValidationError
from grippo.git import validate as v


@pytest.mark.parametrize("name", ["feature/login", "fix-1", "release/1.2.3", "user/abc_def"])
def test_valid_ref_names(name):
    assert v.validate_ref_name(name) == name


@pytest.mark.parametrize("name", [
    "", "-D", "--force", "a..b", "a b", "a~1", "a^", "a:b", "a?", "a*", "a[", "a\\b", ".hidden",
    "a/.b", "a.lock", "a/", "/a", "@", "a@{1}", "a//b", "x;rm -rf ~", "$(touch x)", "a\nb",
])
def test_invalid_ref_names(name):
    with pytest.raises(ValidationError):
        v.validate_ref_name(name)


@pytest.mark.parametrize("rev", ["HEAD", "HEAD~1", "HEAD^2", "origin/main", "abc1234", "v1.0.0",
                                 "stash@{0}", "main@{upstream}"])
def test_valid_revisions(rev):
    assert v.validate_revision(rev) == rev


@pytest.mark.parametrize("rev", ["-x", "--all", "a b", "a;b", "$(x)", "`x`", "a|b", "a..b"])
def test_invalid_revisions(rev):
    with pytest.raises(ValidationError):
        v.validate_revision(rev)


def test_range_only_when_allowed():
    assert v.validate_revision("main..HEAD", allow_range=True) == "main..HEAD"


def test_paths_stay_inside_repo(tmp_path: Path):
    root = tmp_path / "r"
    root.mkdir()
    assert v.validate_path(root, "src/a.py") == "src/a.py"
    assert v.validate_path(root, str(root / "b.txt")) == "b.txt"
    assert v.validate_path(root, "a/../c.txt") == "c.txt"
    assert v.validate_path(root, "-rf") == "./-rf"
    assert v.validate_path(root, "file with space.txt") == "file with space.txt"
    for bad in ("../outside", "/etc/passwd", ".git/config", "sub/../../x", "a\x00b", ""):
        with pytest.raises(ValidationError):
            v.validate_path(root, bad)


@pytest.mark.parametrize("url", ["https://example.invalid/r.git", "git@example.invalid:o/r.git",
                                 "ssh://git@example.invalid/r", "/srv/git/r.git",
                                 "file:///srv/r.git"])
def test_valid_urls(url):
    assert v.validate_url(url) == url


@pytest.mark.parametrize("url", ["ext::sh -c touch% /tmp/pwned", "fd::17", "-uhttps://x",
                                 "https://x y", "relative/path", "ftp://x"])
def test_invalid_urls(url):
    with pytest.raises(ValidationError):
        v.validate_url(url)


@pytest.mark.parametrize("path,sensitive", [(".env", True), ("config/.env.local", True),
                                            (".env.example", False), ("certs/server.pem", True),
                                            ("id_rsa", True), ("src/env.py", False)])
def test_sensitive_paths(path, sensitive):
    assert v.is_sensitive_path(path) is sensitive


def test_message_and_int():
    assert v.validate_message("  feat: x  ") == "feat: x"
    with pytest.raises(ValidationError):
        v.validate_message("   ")
    with pytest.raises(ValidationError):
        v.validate_int(True, low=0, high=5, what="n")
    with pytest.raises(ValidationError):
        v.validate_int(99, low=0, high=5, what="n")
