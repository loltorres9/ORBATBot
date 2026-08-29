"""Tests der Merge-Logik — laufen ohne Tkinter und ohne Bildschirm.

    python -m pytest tools/pdfmerge/test_pdfmerge_core.py
    python tools/pdfmerge/test_pdfmerge_core.py      (ohne pytest)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pypdf import PdfReader, PdfWriter  # noqa: E402

from pdfmerge_core import (  # noqa: E402
    PasswordRequired,
    PdfItem,
    PdfMergeError,
    inspect,
    merge,
    parse_page_spec,
    total_pages,
)


def _make_pdf(path: str, pages: int, password: str = "") -> str:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if password:
        writer.encrypt(password)
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def test_page_spec_basics():
    assert parse_page_spec("", 3) == [0, 1, 2]
    assert parse_page_spec("alle", 3) == [0, 1, 2]
    assert parse_page_spec("*", 2) == [0, 1]
    assert parse_page_spec("1-3,5", 5) == [0, 1, 2, 4]
    assert parse_page_spec("3-", 5) == [2, 3, 4]
    assert parse_page_spec("-2", 5) == [0, 1]
    assert parse_page_spec(" 2 , 4 ", 5) == [1, 3]
    # Doppelte Seiten sind Absicht, nicht Versehen.
    assert parse_page_spec("1,1", 2) == [0, 0]


def test_page_spec_rejects_nonsense():
    for spec in ("0", "6", "x", "3-1", "1-99"):
        try:
            parse_page_spec(spec, 5)
        except PdfMergeError:
            continue
        raise AssertionError(f"'{spec}' hätte abgelehnt werden müssen")


def test_inspect_and_password(tmpdir):
    plain = _make_pdf(os.path.join(tmpdir, "plain.pdf"), 3)
    locked = _make_pdf(os.path.join(tmpdir, "locked.pdf"), 2, password="geheim")

    assert inspect(plain).pages == 3
    try:
        inspect(locked)
        raise AssertionError("Passwort hätte verlangt werden müssen")
    except PasswordRequired:
        pass
    unlocked = inspect(locked, "geheim")
    assert unlocked.pages == 2 and unlocked.encrypted


def test_merge_order_selection_and_bookmarks(tmpdir):
    a = inspect(_make_pdf(os.path.join(tmpdir, "a.pdf"), 3))
    b = inspect(_make_pdf(os.path.join(tmpdir, "b.pdf"), 5))
    b.page_spec = "2-3"
    out = os.path.join(tmpdir, "out.pdf")

    assert total_pages([a, b]) == 5
    assert merge([b, a], out) == 5
    assert len(PdfReader(out).pages) == 5
    titles = [entry.title for entry in PdfReader(out).outline if hasattr(entry, "title")]
    assert titles == ["b", "a"], titles


def test_failure_leaves_target_untouched(tmpdir):
    a = inspect(_make_pdf(os.path.join(tmpdir, "a.pdf"), 2))
    out = os.path.join(tmpdir, "out.pdf")
    merge([a], out)
    before = open(out, "rb").read()

    try:
        merge([a, PdfItem(path=os.path.join(tmpdir, "fehlt.pdf"))], out)
        raise AssertionError("fehlende Datei hätte auffallen müssen")
    except PdfMergeError:
        pass

    assert open(out, "rb").read() == before, "Zieldatei wurde beschädigt"
    assert not os.path.exists(out + ".part"), "Temporärdatei blieb liegen"


def test_merge_into_a_source_file(tmpdir):
    """In eine der Quelldateien speichern muss gehen — das macht man versehentlich."""
    path = _make_pdf(os.path.join(tmpdir, "a.pdf"), 2)
    item = inspect(path)
    assert merge([item, item], path) == 4
    assert len(PdfReader(path).pages) == 4


def test_empty_list_is_refused(tmpdir):
    try:
        merge([], os.path.join(tmpdir, "out.pdf"))
    except PdfMergeError:
        return
    raise AssertionError("leere Liste hätte abgelehnt werden müssen")


def _run_without_pytest() -> int:
    import tempfile
    import traceback

    failures = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        with tempfile.TemporaryDirectory() as directory:
            arguments = (directory,) if function.__code__.co_argcount else ()
            try:
                function(*arguments)
                print(f"ok    {name}")
            except Exception:
                failures += 1
                print(f"FEHL  {name}")
                traceback.print_exc()
    print("\n" + ("Alle Tests bestanden." if not failures else f"{failures} Test(s) fehlgeschlagen."))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_without_pytest())
