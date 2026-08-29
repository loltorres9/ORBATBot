"""Merge-Logik für den PDF-Merger — bewusst ohne GUI-Abhängigkeiten.

Damit lässt sich alles, was tatsächlich Seiten anfasst, ohne Tkinter und ohne
Bildschirm testen; ``pdfmerge.py`` ist nur die Oberfläche darüber.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


class PdfMergeError(Exception):
    """Ein Fehler, der dem Benutzer wörtlich angezeigt werden kann."""


class PasswordRequired(PdfMergeError):
    """Die Datei ist verschlüsselt und das Passwort fehlt oder ist falsch."""


@dataclass
class PdfItem:
    """Eine Datei in der Merge-Liste."""

    path: str
    pages: int = 0
    encrypted: bool = False
    password: str = ""
    #: Seitenauswahl wie "1-3,7,10-"; leer heißt „alle Seiten“.
    page_spec: str = ""
    error: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def title(self) -> str:
        """Dateiname ohne Endung — wird zum Lesezeichen im Ergebnis."""
        return os.path.splitext(self.name)[0]

    def selected_pages(self) -> list[int]:
        """Die ausgewählten Seiten als 0-basierte Indizes."""
        return parse_page_spec(self.page_spec, self.pages)

    @property
    def selected_count(self) -> int:
        return len(self.selected_pages())


def parse_page_spec(spec: str, page_count: int) -> list[int]:
    """``"1-3,7,10-"`` → ``[0,1,2,6,9,...]`` (0-basiert, Reihenfolge wie getippt).

    Leer, ``*`` oder ``all``/``alle`` bedeutet alle Seiten. Doppelt genannte
    Seiten werden bewusst nicht entfernt: wer ``1,1`` schreibt, will die Seite
    zweimal.
    """
    text = (spec or "").strip().lower()
    if text in ("", "*", "all", "alle"):
        return list(range(page_count))
    if page_count <= 0:
        raise PdfMergeError("Die Datei hat keine Seiten.")

    pages: list[int] = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start_text, _, end_text = part.partition("-")
            start = _page_number(start_text or "1", page_count)
            end = _page_number(end_text or str(page_count), page_count)
            if start > end:
                raise PdfMergeError(
                    f"Ungültiger Bereich '{part}': {start} liegt hinter {end}."
                )
            pages.extend(range(start - 1, end))
        else:
            pages.append(_page_number(part, page_count) - 1)

    if not pages:
        raise PdfMergeError(f"Keine Seiten in '{spec}'.")
    return pages


def _page_number(text: str, page_count: int) -> int:
    if not text.isdigit():
        raise PdfMergeError(f"'{text}' ist keine Seitenzahl.")
    number = int(text)
    if number < 1 or number > page_count:
        raise PdfMergeError(
            f"Seite {number} gibt es nicht — die Datei hat {page_count} Seiten."
        )
    return number


def open_pdf(path: str, password: str = "") -> PdfReader:
    """Öffnet eine PDF und entsperrt sie, falls nötig.

    Viele verschlüsselte PDFs haben ein leeres Benutzerpasswort und lassen sich
    ohne Zutun öffnen — deshalb wird das immer zuerst versucht.
    """
    try:
        reader = PdfReader(path)
    except FileNotFoundError:
        raise PdfMergeError(f"Datei nicht gefunden: {path}") from None
    except PdfReadError as exc:
        raise PdfMergeError(f"Keine lesbare PDF-Datei: {os.path.basename(path)} ({exc})") from exc
    except OSError as exc:
        raise PdfMergeError(f"Datei nicht lesbar: {os.path.basename(path)} ({exc})") from exc

    if reader.is_encrypted:
        for candidate in ("", password):
            try:
                if reader.decrypt(candidate):
                    break
            except Exception:  # pypdf wirft je nach Verfahren Unterschiedliches
                continue
        else:
            raise PasswordRequired(
                f"{os.path.basename(path)} ist passwortgeschützt."
            )
    return reader


def inspect(path: str, password: str = "") -> PdfItem:
    """Liest Seitenzahl und Verschlüsselung — für die Anzeige in der Liste."""
    reader = open_pdf(path, password)
    return PdfItem(
        path=os.path.abspath(path),
        pages=len(reader.pages),
        encrypted=reader.is_encrypted,
        password=password,
    )


def merge(
    items: Sequence[PdfItem],
    output_path: str,
    *,
    bookmarks: bool = True,
    keep_outline: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Fügt ``items`` in genau dieser Reihenfolge zu ``output_path`` zusammen.

    Gibt die Seitenzahl der Ergebnisdatei zurück. Geschrieben wird erst am Ende
    und über eine temporäre Datei, damit ein Fehler in der letzten Datei nicht
    eine halbe PDF hinterlässt — und damit man in eine der Quelldateien hinein
    speichern kann, ohne sie unter den eigenen Füßen wegzuziehen.
    """
    if not items:
        raise PdfMergeError("Es ist keine Datei ausgewählt.")

    writer = PdfWriter()
    total = len(items)
    try:
        for index, item in enumerate(items, start=1):
            if progress:
                progress(index, total, item.name)
            reader = open_pdf(item.path, item.password)
            page_count = len(reader.pages)
            if page_count != item.pages:
                # Die Datei hat sich geändert, seit sie in die Liste kam.
                item.pages = page_count
            pages = parse_page_spec(item.page_spec, page_count)
            writer.append(
                reader,
                outline_item=item.title if bookmarks else None,
                pages=pages,
                import_outline=keep_outline,
            )

        if not writer.pages:
            raise PdfMergeError("Das Ergebnis hätte keine einzige Seite.")

        temp_path = output_path + ".part"
        with open(temp_path, "wb") as handle:
            writer.write(handle)
        os.replace(temp_path, output_path)
        return len(writer.pages)
    except PdfMergeError:
        raise
    except OSError as exc:
        raise PdfMergeError(f"Speichern fehlgeschlagen: {exc}") from exc
    except Exception as exc:  # pypdf ist bei kaputten Dateien nicht wählerisch
        raise PdfMergeError(f"Zusammenfügen fehlgeschlagen: {exc}") from exc
    finally:
        writer.close()


def total_pages(items: Iterable[PdfItem]) -> int:
    """Seitenzahl des künftigen Ergebnisses, soweit sie sich ausrechnen lässt."""
    count = 0
    for item in items:
        try:
            count += item.selected_count
        except PdfMergeError:
            continue
    return count
