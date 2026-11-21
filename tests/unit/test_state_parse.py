from grippo.git.state import parse_porcelain_v2


def test_parse_branch_headers_and_entries():
    raw = "\0".join([
        "# branch.oid 1234567890abcdef1234567890abcdef12345678",
        "# branch.head feature/x",
        "# branch.upstream origin/feature/x",
        "# branch.ab +2 -3",
        "1 .M N... 100644 100644 100644 aaa bbb src/app.py",
        "1 A. N... 000000 100644 100644 000 bbb file with space.txt",
        "2 R. N... 100644 100644 100644 aaa bbb R100 docs/new name.md",
        "docs/old name.md",
        "u UU N... 100644 100644 100644 100644 a b c conflicted.txt",
        "? untracked dir/",
        "? ação.txt",
        "",
    ])
    info = parse_porcelain_v2(raw)
    assert info["branch"] == "feature/x"
    assert info["upstream"] == "origin/feature/x"
    assert (info["ahead"], info["behind"]) == (2, 3)
    paths = {e.path: e for e in info["entries"]}
    assert paths["src/app.py"].unstaged and not paths["src/app.py"].staged
    assert paths["file with space.txt"].staged
    assert paths["docs/new name.md"].orig_path == "docs/old name.md"
    assert paths["conflicted.txt"].conflicted
    assert info["untracked"] == ["untracked dir/", "ação.txt"]


def test_parse_unborn_and_detached():
    info = parse_porcelain_v2("# branch.oid (initial)\0# branch.head main\0")
    assert info["oid"] is None and info["branch"] == "main"
    info = parse_porcelain_v2("# branch.oid abc\0# branch.head (detached)\0")
    assert info["branch"] is None
