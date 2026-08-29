"""PDF-Merger — kleines Windows-Werkzeug zum Zusammenfügen von PDF-Dateien.

Start:  python pdfmerge.py [datei1.pdf datei2.pdf ...]

Die Reihenfolge in der Liste ist die Reihenfolge im Ergebnis: per Ziehen mit
der Maus, mit den Pfeil-Schaltflächen oder mit Alt+Hoch/Runter ändern.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from pdfmerge_core import (
    PasswordRequired,
    PdfItem,
    PdfMergeError,
    inspect,
    merge,
    parse_page_spec,
    total_pages,
)

APP_NAME = "PDF-Merger"
PDF_TYPES = [("PDF-Dateien", "*.pdf"), ("Alle Dateien", "*.*")]


def _enable_dpi_awareness() -> None:
    """Ohne das ist die Oberfläche auf skalierten Windows-Displays unscharf."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class MergerApp(ttk.Frame):
    def __init__(self, master: tk.Tk, initial_files: list[str] | None = None) -> None:
        super().__init__(master, padding=10)
        self.master: tk.Tk = master
        #: iid der Tabellenzeile → Datei. Die *Reihenfolge* steht ausschließlich
        #: in der Tabelle, damit Ziehen mit der Maus und Liste nicht auseinanderlaufen.
        self.items: dict[str, PdfItem] = {}
        self._counter = 0
        self._drag_iids: list[str] = []
        self._busy = False
        self._queue: queue.Queue = queue.Queue()

        self._build()
        self.pack(fill="both", expand=True)
        if initial_files:
            self.after(100, lambda: self._add_paths(initial_files))

    # ------------------------------------------------------------------ Aufbau

    def _build(self) -> None:
        self.master.title(APP_NAME)
        self.master.minsize(700, 420)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(toolbar, text="Dateien hinzufügen…", command=self.add_files).pack(side="left")
        ttk.Button(toolbar, text="Entfernen", command=self.remove_selected).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Liste leeren", command=self.clear).pack(side="left", padx=(6, 0))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(toolbar, text="▲", width=3, command=lambda: self.move_selected(-1)).pack(side="left")
        ttk.Button(toolbar, text="▼", width=3, command=lambda: self.move_selected(1)).pack(side="left", padx=(4, 0))
        ttk.Button(toolbar, text="A–Z", width=5, command=self.sort_by_name).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Umkehren", command=self.reverse).pack(side="left", padx=(4, 0))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(toolbar, text="Seiten wählen…", command=self.edit_pages).pack(side="left")

        columns = ("nr", "file", "pages", "sel")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="extended")
        for key, text, width, anchor in (
            ("nr", "Nr.", 44, "e"),
            ("file", "Datei", 420, "w"),
            ("pages", "Seiten", 70, "e"),
            ("sel", "Auswahl", 130, "w"),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "file"))
        self.tree.grid(row=1, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure("error", foreground="#b00020")

        self.tree.bind("<ButtonPress-1>", self._drag_start)
        self.tree.bind("<B1-Motion>", self._drag_motion)
        self.tree.bind("<ButtonRelease-1>", self._drag_end)
        self.tree.bind("<Double-1>", lambda _e: self.edit_pages())
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())
        self.tree.bind("<<TreeviewSelect>>", self._show_path)
        self.master.bind("<Alt-Up>", lambda _e: self.move_selected(-1))
        self.master.bind("<Alt-Down>", lambda _e: self.move_selected(1))
        self.master.bind("<Control-o>", lambda _e: self.add_files())

        self.status = ttk.Label(self, text="Noch keine Dateien ausgewählt.", anchor="w")
        self.status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        bottom = ttk.Frame(self)
        bottom.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.bookmarks = tk.BooleanVar(value=True)
        self.keep_outline = tk.BooleanVar(value=True)
        self.open_after = tk.BooleanVar(value=True)
        ttk.Checkbutton(bottom, text="Lesezeichen je Datei", variable=self.bookmarks).pack(side="left")
        ttk.Checkbutton(bottom, text="Vorhandene Lesezeichen übernehmen", variable=self.keep_outline).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(bottom, text="Danach öffnen", variable=self.open_after).pack(side="left", padx=(10, 0))
        self.merge_button = ttk.Button(bottom, text="Zusammenfügen…", command=self.do_merge)
        self.merge_button.pack(side="right")

        self._enable_explorer_drop()
        self._update_status()

    def _enable_explorer_drop(self) -> None:
        """Dateien aus dem Explorer ins Fenster ziehen — falls tkinterdnd2 da ist.

        Rein optional: ohne das Paket funktioniert alles andere unverändert.
        """
        try:
            from tkinterdnd2 import DND_FILES  # type: ignore

            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind(
                "<<Drop>>",
                lambda event: self._add_paths(list(self.tk.splitlist(event.data))),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ Liste

    def ordered(self) -> list[PdfItem]:
        return [self.items[iid] for iid in self.tree.get_children()]

    def _add_paths(self, paths) -> None:
        known = {item.path for item in self.items.values()}
        added = skipped = 0
        problems: list[str] = []
        for raw in paths:
            path = os.path.abspath(str(raw).strip())
            if os.path.isdir(path):
                continue
            if path in known:
                skipped += 1
                continue
            item = self._inspect_with_password(path)
            if item is None:
                continue
            if item.error:
                problems.append(item.error)
            known.add(path)
            self._insert(item)
            added += 1

        self._renumber()
        parts = [f"{added} Datei(en) hinzugefügt"]
        if skipped:
            parts.append(f"{skipped} bereits in der Liste")
        self._update_status(" · ".join(parts))
        if problems:
            messagebox.showwarning(APP_NAME, "\n".join(problems), parent=self.master)

    def _inspect_with_password(self, path: str) -> PdfItem | None:
        """Liest die Datei ein und fragt bei Bedarf einmal nach dem Passwort."""
        password = ""
        attempts = 3
        for attempt in range(attempts):
            try:
                return inspect(path, password)
            except PasswordRequired as exc:
                if attempt == attempts - 1:
                    break
                answer = simpledialog.askstring(
                    APP_NAME,
                    f"{exc}\nPasswort:" if attempt == 0 else "Falsches Passwort. Noch einmal:",
                    show="*",
                    parent=self.master,
                )
                if not answer:
                    return None
                password = answer
            except PdfMergeError as exc:
                return PdfItem(path=path, error=str(exc))
        return PdfItem(path=path, error=f"{os.path.basename(path)}: Passwort nicht akzeptiert.")

    def _insert(self, item: PdfItem) -> None:
        self._counter += 1
        iid = f"item{self._counter}"
        self.items[iid] = item
        self.tree.insert("", "end", iid=iid, values=self._row(item), tags=("error",) if item.error else ())

    def _row(self, item: PdfItem) -> tuple:
        if item.error:
            return ("", item.name, "—", "Fehler")
        try:
            selection = "alle" if not item.page_spec else f"{item.page_spec} ({item.selected_count})"
        except PdfMergeError as exc:
            selection = f"⚠ {exc}"
        return ("", item.name, item.pages, selection)

    def _renumber(self) -> None:
        for number, iid in enumerate(self.tree.get_children(), start=1):
            self.tree.set(iid, "nr", number)
        self._update_status()

    def _update_status(self, message: str | None = None) -> None:
        items = self.ordered()
        broken = sum(1 for item in items if item.error)
        summary = f"{len(items)} Datei(en) · {total_pages(items)} Seiten im Ergebnis"
        if broken:
            summary += f" · {broken} fehlerhaft"
        self.status.configure(text=f"{message} — {summary}" if message else summary)
        self.merge_button.state(["!disabled"] if items and not self._busy else ["disabled"])

    def _show_path(self, _event=None) -> None:
        selection = self.tree.selection()
        if len(selection) == 1:
            self.status.configure(text=self.items[selection[0]].path)

    # ------------------------------------------------------------- Bedienung

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="PDF-Dateien auswählen", filetypes=PDF_TYPES, parent=self.master
        )
        if paths:
            self._add_paths(paths)

    def remove_selected(self) -> None:
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.items.pop(iid, None)
        self._renumber()

    def clear(self) -> None:
        if self.items and not messagebox.askyesno(APP_NAME, "Ganze Liste leeren?", parent=self.master):
            return
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self._renumber()

    def move_selected(self, delta: int) -> None:
        """Verschiebt die Auswahl um eine Position — als Block, ohne Lücken."""
        order = list(self.tree.get_children())
        selected = [iid for iid in order if iid in set(self.tree.selection())]
        if not selected:
            return
        if delta < 0 and order.index(selected[0]) == 0:
            return
        if delta > 0 and order.index(selected[-1]) == len(order) - 1:
            return
        for iid in selected if delta < 0 else reversed(selected):
            self.tree.move(iid, "", self.tree.index(iid) + delta)
        self._renumber()
        self.tree.see(selected[0] if delta < 0 else selected[-1])

    def sort_by_name(self) -> None:
        order = sorted(
            self.tree.get_children(),
            key=lambda iid: _natural_key(self.items[iid].name),
        )
        for index, iid in enumerate(order):
            self.tree.move(iid, "", index)
        self._renumber()

    def reverse(self) -> None:
        for index, iid in enumerate(reversed(self.tree.get_children())):
            self.tree.move(iid, "", index)
        self._renumber()

    def edit_pages(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "Bitte zuerst eine Datei auswählen.", parent=self.master)
            return
        first = self.items[selection[0]]
        answer = simpledialog.askstring(
            APP_NAME,
            "Welche Seiten sollen übernommen werden?\n"
            "Beispiele: 1-3,7,10-  ·  leer oder 'alle' für die ganze Datei",
            initialvalue=first.page_spec,
            parent=self.master,
        )
        if answer is None:
            return
        for iid in selection:
            item = self.items[iid]
            if item.error:
                continue
            try:
                parse_page_spec(answer, item.pages)
            except PdfMergeError as exc:
                messagebox.showerror(APP_NAME, f"{item.name}: {exc}", parent=self.master)
                continue
            item.page_spec = answer.strip()
            self.tree.item(iid, values=self._row(item))
        self._renumber()

    # ------------------------------------------------------------- Maus-Drag

    def _drag_start(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            self._drag_iids = []
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            self._drag_iids = []
            return
        selection = set(self.tree.selection())
        # Auf einer bereits ausgewählten Zeile zieht man den ganzen Block.
        self._drag_iids = (
            [i for i in self.tree.get_children() if i in selection] if iid in selection else [iid]
        )

    def _drag_motion(self, event) -> None:
        if not self._drag_iids:
            return
        target = self.tree.identify_row(event.y)
        if not target or target in self._drag_iids:
            return
        index = self.tree.index(target)
        for offset, iid in enumerate(self._drag_iids):
            self.tree.move(iid, "", index + offset)
        self.tree.selection_set(self._drag_iids)

    def _drag_end(self, _event) -> None:
        if self._drag_iids:
            self._drag_iids = []
            self._renumber()

    # --------------------------------------------------------------- Merge

    def do_merge(self) -> None:
        items = self.ordered()
        broken = [item for item in items if item.error]
        if broken:
            messagebox.showerror(
                APP_NAME,
                "Diese Dateien lassen sich nicht lesen und müssen aus der Liste:\n\n"
                + "\n".join(item.error for item in broken),
                parent=self.master,
            )
            return
        if len(items) < 2 and not messagebox.askyesno(
            APP_NAME, "Es ist nur eine Datei in der Liste. Trotzdem speichern?", parent=self.master
        ):
            return
        try:
            for item in items:
                item.selected_pages()
        except PdfMergeError as exc:
            messagebox.showerror(APP_NAME, f"Ungültige Seitenauswahl: {exc}", parent=self.master)
            return

        output = filedialog.asksaveasfilename(
            title="Ergebnis speichern unter",
            defaultextension=".pdf",
            filetypes=PDF_TYPES,
            initialfile="zusammengefuegt.pdf",
            initialdir=os.path.dirname(items[0].path),
            parent=self.master,
        )
        if not output:
            return

        self._set_busy(True)
        self.progress.configure(maximum=len(items), value=0)
        # Die Optionen werden *hier* gelesen: Tk-Variablen dürfen nur aus dem
        # Hauptthread angefasst werden, der Worker sieht nur einfache Werte.
        options = (self.bookmarks.get(), self.keep_outline.get())
        thread = threading.Thread(
            target=self._merge_worker, args=(items, output, options), daemon=True
        )
        thread.start()
        self.after(80, self._poll)

    def _merge_worker(
        self, items: list[PdfItem], output: str, options: tuple[bool, bool]
    ) -> None:
        bookmarks, keep_outline = options

        def progress(index: int, total: int, name: str) -> None:
            self._queue.put(("progress", index, name))

        try:
            pages = merge(
                items,
                output,
                bookmarks=bookmarks,
                keep_outline=keep_outline,
                progress=progress,
            )
            self._queue.put(("done", output, pages))
        except PdfMergeError as exc:
            self._queue.put(("error", str(exc)))
        except Exception as exc:  # niemals still im Hintergrundthread sterben
            self._queue.put(("error", f"Unerwarteter Fehler: {exc}"))

    def _poll(self) -> None:
        """Holt Meldungen des Arbeitsthreads ab — Tk darf nur hier angefasst werden."""
        try:
            while True:
                message = self._queue.get_nowait()
                kind = message[0]
                if kind == "progress":
                    _, index, name = message
                    self.progress.configure(value=index)
                    self.status.configure(text=f"Verarbeite {name} …")
                elif kind == "done":
                    _, output, pages = message
                    self._set_busy(False)
                    self.progress.configure(value=self.progress["maximum"])
                    self._update_status(f"Fertig: {pages} Seiten → {os.path.basename(output)}")
                    if self.open_after.get():
                        _open_file(output)
                    else:
                        messagebox.showinfo(
                            APP_NAME, f"Gespeichert:\n{output}\n\n{pages} Seiten.", parent=self.master
                        )
                    return
                elif kind == "error":
                    self._set_busy(False)
                    self.progress.configure(value=0)
                    self._update_status("Abgebrochen")
                    messagebox.showerror(APP_NAME, message[1], parent=self.master)
                    return
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = ["disabled"] if busy else ["!disabled"]
        self.merge_button.state(state)
        self.master.configure(cursor="watch" if busy else "")


def _natural_key(name: str):
    """Sortiert ``Anhang2`` vor ``Anhang10`` — die Reihenfolge, die man erwartet."""
    parts: list = []
    digits = ""
    for char in name.lower():
        if char.isdigit():
            digits += char
        else:
            if digits:
                parts.append((1, int(digits), ""))
                digits = ""
            parts.append((0, 0, char))
    if digits:
        parts.append((1, int(digits), ""))
    return parts


def _open_file(path: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
    except Exception:
        pass


def main() -> int:
    _enable_dpi_awareness()
    try:
        from tkinterdnd2 import TkinterDnD  # type: ignore

        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()

    try:
        root.call("ttk::style", "theme", "use", "vista")
    except tk.TclError:
        pass

    MergerApp(root, [arg for arg in sys.argv[1:] if not arg.startswith("-")])
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
