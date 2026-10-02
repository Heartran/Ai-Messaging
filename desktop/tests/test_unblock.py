"""Mark-of-the-Web removal. On NTFS the stream is `file:Zone.Identifier`;
on this (POSIX) runner the same string is an ordinary file name, which is
enough to prove the walk, the filter and the counting."""

from aim_desktop.unblock import ZONE_STREAM, bundle_root, unblock_tree


def test_removes_streams_of_bundle_files_only(tmp_path):
    (tmp_path / "pythonnet" / "runtime").mkdir(parents=True)
    marked = tmp_path / "pythonnet" / "runtime" / "Python.Runtime.dll"
    marked.write_bytes(b"MZ")
    (tmp_path / "pythonnet" / "runtime" / f"Python.Runtime.dll{ZONE_STREAM}").write_text("[ZoneTransfer]\nZoneId=3\n")
    clean = tmp_path / "launcher.exe"
    clean.write_bytes(b"MZ")
    other = tmp_path / "notes.txt"
    other.write_text("x")
    (tmp_path / f"notes.txt{ZONE_STREAM}").write_text("[ZoneTransfer]\nZoneId=3\n")

    assert unblock_tree(tmp_path) == 1
    assert marked.exists() and clean.exists()
    assert not (tmp_path / "pythonnet" / "runtime" / f"Python.Runtime.dll{ZONE_STREAM}").exists()
    assert (tmp_path / f"notes.txt{ZONE_STREAM}").exists()     # not a bundle file type: untouched
    assert unblock_tree(tmp_path) == 0                          # idempotent


def test_missing_root_is_a_no_op(tmp_path):
    assert unblock_tree(tmp_path / "nope") == 0
    assert unblock_tree(None) == 0


def test_bundle_root_is_none_from_source():
    assert bundle_root() is None
