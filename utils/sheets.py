import gspread
from google.oauth2.service_account import Credentials
import json
import os
import re
from typing import Optional

SCOPES = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]

# Matches "1. Squad Leader" or "1- Squad Leader" but NOT "1-1 Rangers" (digit after hyphen)
_SLOT_PREFIX = re.compile(r'^\d+[.\-](?!\d)\s*')

# Radio frequency cells like "152 CHN : 1" or "343 CHN:9"
_RADIO_FREQ = re.compile(r'\d{3}\s*CHN', re.IGNORECASE)


def get_client() -> gspread.Client:
    creds_json = os.getenv('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS environment variable not set.")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def extract_sheet_id(url: str) -> str:
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)
    raise ValueError("Could not extract sheet ID. Make sure it's a valid Google Sheets link.")


def _is_slot_entry(cell: str) -> bool:
    """Cell starts with a number like '1.' or '1-'."""
    return bool(_SLOT_PREFIX.match(cell.strip()))


def _is_available(cell: str) -> bool:
    """Slot is available if it contains <Insert Name>."""
    return '<insert name>' in cell.lower()


def _extract_role(cell: str) -> str:
    """
    From "3. Team Leader Alpha - [] <Insert Name>"
    returns "3. Team Leader Alpha".
    The number prefix is kept so duplicate role names within the same squad
    (e.g. two Rifleman slots) remain distinguishable.
    """
    # Remove " - [tag] anything" suffix only
    role = re.sub(r'\s*[-–—]\s*[\[<].*', '', cell.strip())
    return role.strip()


def _is_squad_header(cell: str) -> bool:
    """
    A squad header is a non-empty cell that is NOT a slot entry,
    NOT a radio frequency, and contains at least one letter.
    """
    cell = cell.strip()
    if not cell:
        return False
    if _is_slot_entry(cell):
        return False
    if _RADIO_FREQ.search(cell):
        return False
    # Skip short labels like column headers ("Net", etc.)
    if len(cell) < 3:
        return False
    # Skip column headings that end with a colon ("Slots:", "Radio frequencies:")
    if cell.endswith(':'):
        return False
    # Skip announcement sentences — squad headers don't end with punctuation
    if cell.endswith('.') or cell.endswith('!') or cell.endswith('?'):
        return False
    # Assignment marker cells (e.g. "[] <Insert Name>") are not squad headers
    if _is_available(cell):
        return False
    # Allow squad identifier patterns like "1-0 ()", "2-1" even without letters
    if re.match(r'^\d+-\d+', cell):
        return True
    # Require at least one letter for everything else
    if not re.search(r'[a-zA-Z]', cell):
        return False
    return True


def load_slots(sheet_url: str) -> dict:
    """
    Parse an Arma 3 ORBAT Google Sheet.

    Supports two layouts:
    - Single-cell: "3. Team Leader Alpha - [] <Insert Name>" (all in one cell)
    - Multi-cell:  "3. Team Leader Alpha" | ... | "[] <Insert Name>" (split across columns)

    For multi-cell layouts, when a slot entry is found the code searches up to 5
    columns to the right in the same row for the <Insert Name> marker.  The
    assignment is written to whichever cell contains <Insert Name>.

    Squad headers are inferred from the nearest non-slot cell above in the same column.
    """
    client = get_client()
    sheet_id = extract_sheet_id(sheet_url)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1
    operation_name = spreadsheet.title

    all_values = worksheet.get_all_values()
    if not all_values:
        raise ValueError("The sheet appears to be empty.")

    num_cols = max(len(row) for row in all_values)

    # Track the most recent squad header seen in each column
    squad_per_col: dict[int, str] = {}
    seen_values: set = set()
    slots = []

    for row_idx, row in enumerate(all_values):
        for col_idx in range(num_cols):
            cell = row[col_idx].strip() if col_idx < len(row) else ''
            if not cell:
                continue

            if _is_slot_entry(cell):
                # Find the cell containing <Insert Name> — may be this cell or
                # up to 4 columns to the right (multi-cell ORBAT layouts).
                # Stop early if another slot entry is encountered: that cell belongs
                # to a different squad's column and we must not steal its assignment.
                assign_col = None
                for search_col in range(col_idx, min(col_idx + 5, num_cols)):
                    search_cell = row[search_col].strip() if search_col < len(row) else ''
                    # Must check slot-entry BEFORE available: a single-cell slot like
                    # "3. Medic - [] <Insert Name>" passes both tests. If we're scanning
                    # past the original column we must stop rather than steal that cell.
                    if search_col > col_idx and _is_slot_entry(search_cell):
                        break  # crossed into another slot — stop
                    if _is_available(search_cell):
                        assign_col = search_col
                        break

                if assign_col is not None:
                    role = _extract_role(cell)
                    squad = squad_per_col.get(col_idx, 'Unknown')
                    label = f"{squad} \u2013 {role}"
                    if len(label) > 100:
                        label = label[:97] + '...'

                    sheet_row = row_idx + 1         # 1-indexed
                    assign_sheet_col = assign_col + 1  # 1-indexed
                    value = f"r{sheet_row}c{assign_sheet_col}"

                    if value in seen_values:
                        continue  # same cell already claimed — skip duplicate
                    seen_values.add(value)

                    slots.append({
                        'label': label,
                        'row': sheet_row,
                        'col': assign_sheet_col,
                        'squad': squad,
                        'role': role,
                        'value': value,
                    })
            elif _is_squad_header(cell):
                # Strip empty unit tag e.g. "1-0 ()" → "1-0"
                squad_per_col[col_idx] = re.sub(r'\s*\(\s*\)\s*$', '', cell).strip() or cell

    if not slots:
        raise ValueError(
            "No available slots found.\n\n"
            "The bot looks for cells containing **`<Insert Name>`** to identify open slots.\n"
            "Make sure your sheet uses that exact text for unfilled positions."
        )

    return {
        'operation_name': operation_name,
        'slots': slots,
        'sheet_id': sheet_id,
        # These are not used for ORBAT-format sheets (per-cell updates instead)
        'squad_col': None,
        'role_col': None,
        'status_col': None,
        'assigned_col': None,
    }


def load_all_slots(sheet_url: str) -> dict:
    """
    Load ALL slots from an ORBAT sheet — including already-assigned ones.
    Each slot has an 'assigned_to' field (str or None).
    Used to build the live ORBAT display.

    A slot is considered filled when its assignment cell contains '[]' followed
    by a non-empty name that is not '<Insert Name>'.
    """
    client = get_client()
    sheet_id = extract_sheet_id(sheet_url)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1
    operation_name = spreadsheet.title
    all_values = worksheet.get_all_values()
    if not all_values:
        raise ValueError("The sheet appears to be empty.")

    num_cols = max(len(row) for row in all_values)
    squad_per_col: dict[int, str] = {}
    seen_values: set = set()
    slots = []

    for row_idx, row in enumerate(all_values):
        for col_idx in range(num_cols):
            cell = row[col_idx].strip() if col_idx < len(row) else ''
            if not cell:
                continue

            if _is_slot_entry(cell):
                role = _extract_role(cell)
                squad = squad_per_col.get(col_idx, 'Unknown')
                sheet_row = row_idx + 1
                assigned_to = None
                assign_col = None

                for search_col in range(col_idx, min(col_idx + 5, num_cols)):
                    search_cell = row[search_col].strip() if search_col < len(row) else ''
                    if search_col > col_idx and _is_slot_entry(search_cell):
                        break  # crossed into another slot's column
                    if _is_available(search_cell):
                        assign_col = search_col
                        break
                    # Filled with [] prefix (bot-assigned or manually in same format)
                    filled = re.search(r'\[\]\s*(.+)', search_cell)
                    if filled:
                        name = filled.group(1).strip()
                        if name and '<insert name>' not in name.lower():
                            assigned_to = name
                            assign_col = search_col
                            break
                    # Single-cell filled: "1. Role - [TAG] Name" where name is not <Insert Name>
                    tagged = re.search(r'[-–—]\s*\[.*?\]\s*(.+)', search_cell)
                    if tagged:
                        name = tagged.group(1).strip()
                        if name and '<insert name>' not in name.lower():
                            assigned_to = name
                            assign_col = search_col
                            break
                    # Single-cell filled without brackets: "1. Role - Name" or "1. Role — Name"
                    untagged = re.search(r'[-–—]\s*([^\[<].*)', search_cell)
                    if untagged:
                        name = untagged.group(1).strip()
                        if name and '<insert name>' not in name.lower():
                            assigned_to = name
                            assign_col = search_col
                            break
                    # Manually filled: plain name in a cell to the right (no [] prefix)
                    if search_col > col_idx and search_cell and not _RADIO_FREQ.search(search_cell):
                        assigned_to = search_cell
                        assign_col = search_col
                        break

                assign_sheet_col = (assign_col + 1) if assign_col is not None else None
                value = (
                    f"r{sheet_row}c{assign_sheet_col}"
                    if assign_sheet_col else f"r{sheet_row}"
                )
                if value in seen_values:
                    continue
                seen_values.add(value)

                slots.append({
                    'squad': squad,
                    'role': role,
                    'row': sheet_row,
                    'col': assign_sheet_col,
                    'assigned_to': assigned_to,
                    'col_idx': col_idx,
                })
            elif _is_squad_header(cell):
                # Strip empty unit tag e.g. "1-0 ()" → "1-0"
                squad_per_col[col_idx] = re.sub(r'\s*\(\s*\)\s*$', '', cell).strip() or cell

    return {
        'operation_name': operation_name,
        'sheet_id': sheet_id,
        'slots': slots,
    }


def clear_slot(sheet_id: str, row: int, col: int, member_name: str):
    """
    Reverse an assignment: restore the assignment portion of the cell to
    '[] <Insert Name>' while preserving the role prefix.

    For single-cell format "7. Rifleman - [2nd USC] Panz" this restores
    "7. Rifleman - [] <Insert Name>". For a standalone assignment cell
    like "[2nd USC] Panz" it restores "[] <Insert Name>".
    """
    client = get_client()
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1

    current = worksheet.cell(row, col).value or ''

    # Replace "[UnitTag] MemberName" or "[] MemberName" → "[] <Insert Name>"
    new_value = re.sub(
        r'\[.*?\]\s*' + re.escape(member_name),
        '[] <Insert Name>',
        current,
        flags=re.IGNORECASE,
    )
    if new_value == current:
        # Fallback: replace the name anywhere in the cell, then fix any leftover tag
        new_value = re.sub(re.escape(member_name), '<Insert Name>', current, flags=re.IGNORECASE)
        new_value = re.sub(r'\[.*?\](\s*<Insert Name>)', r'[]\1', new_value, flags=re.IGNORECASE)
    if new_value == current:
        # Last resort: preserve role prefix, reset only the assignment portion
        role_part = _extract_role(current)
        if role_part and role_part != current:
            new_value = role_part + ' - [] <Insert Name>'
        else:
            new_value = '[] <Insert Name>'
    worksheet.update_cell(row, col, new_value)

    # Remove bold formatting — non-fatal
    try:
        cell_a1 = gspread.utils.rowcol_to_a1(row, col)
        worksheet.format(cell_a1, {'textFormat': {'bold': False}})
    except Exception:
        pass


def assign_slot(sheet_id: str, row: int, col: int, member_name: str, unit_role: str = None):
    """
    Replace '<Insert Name>' with the member's name and, if a unit_role is
    provided, fill the [] tag with the group name.

    e.g. "[] <Insert Name>"  -> "[2nd USC] MemberName"
    or   "3. Role - [] <Insert Name>" -> "3. Role - [2nd USC] MemberName"

    The member's name is formatted as bold.
    """
    client = get_client()
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1

    current = worksheet.cell(row, col).value or ''
    new_value = re.sub(r'<Insert Name>', member_name, current, flags=re.IGNORECASE)
    if unit_role:
        new_value = re.sub(r'\[\]', f'[{unit_role}]', new_value, count=1)
    worksheet.update_cell(row, col, new_value)

    # Apply bold formatting — non-fatal, the assignment itself already succeeded
    try:
        cell_a1 = gspread.utils.rowcol_to_a1(row, col)
        worksheet.format(cell_a1, {'textFormat': {'bold': True}})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# One-way export
# ---------------------------------------------------------------------------
#
# Writing an ORBAT out to a spreadsheet, for briefings, Zeus, or anyone who
# would rather look at a sheet. Nothing reads it back: the export always creates
# a **new tab** and never touches an existing one, so it cannot overwrite the
# sheet a live operation is running on. (`load_slots()` only ever reads the
# first tab, which an exported one is not.)

# Sheets caps a tab title at 100 characters.
MAX_TAB_TITLE = 100

# Where the two columns of squads land. Column C is left empty as a gutter,
# mirroring how these sheets are laid out by hand.
_LEFT, _RIGHT = 1, 4


def _squad_block(squad: dict) -> list:
    """One squad as a list of (role_cell, assignment_cell) pairs, header first."""
    unit = squad.get('reserved_unit')
    rows = [(f"{squad['name']} [{unit}]" if unit else squad['name'], '')]
    if squad.get('radio'):
        rows.append((squad['radio'], ''))
    for number, slot in enumerate(squad['slots'], start=1):
        booking = slot.get('booking')
        if booking:
            tag = booking.get('unit_role') or ''
            assignment = f"[{tag}] {booking['member_name']}"
        else:
            assignment = '[] <Insert Name>'
        rows.append((f"{number}. {slot['role_name']}", assignment))
    rows.append(('', ''))
    return rows


def export_orbat(sheet_url: str, tab_title: str, squads: list, nets: list) -> str:
    """Write the ORBAT into a new tab and return the tab's title.

    The layout is the one `load_slots()` understands — a squad header, then
    `N. Role` beside `[] <Insert Name>` — so the tab reads like the sheets these
    units already keep by hand. It is still one-way: the bot reads the first tab
    only, so an export is never picked up on its own.
    """
    client = get_client()
    spreadsheet = client.open_by_key(extract_sheet_id(sheet_url))

    title = tab_title[:MAX_TAB_TITLE]
    taken = {ws.title for ws in spreadsheet.worksheets()}
    if title in taken:
        # Never overwrite. A second export in the same minute gets a suffix.
        for suffix in range(2, 100):
            candidate = f"{title[:MAX_TAB_TITLE - 4]} ({suffix})"
            if candidate not in taken:
                title = candidate
                break

    left = [_squad_block(s) for s in squads if not s['column_side']]
    right = [_squad_block(s) for s in squads if s['column_side']]

    cells = []

    def place(blocks, column):
        row = 1
        for block in blocks:
            for role, assignment in block:
                if role:
                    cells.append(gspread.Cell(row, column, role))
                if assignment:
                    cells.append(gspread.Cell(row, column + 1, assignment))
                row += 1
        return row

    bottom = max(place(left, _LEFT), place(right, _RIGHT))

    if nets:
        row = bottom + 1
        cells.append(gspread.Cell(row, _LEFT, 'RADIO NETS'))
        for net in nets:
            row += 1
            channel = net['channel'] or ''
            name = f"({net['name']})" if net['inactive'] else net['name']
            cells.append(gspread.Cell(row, _LEFT, name))
            if channel:
                cells.append(gspread.Cell(row, _LEFT + 1, channel))
        bottom = row

    worksheet = spreadsheet.add_worksheet(
        title=title, rows=max(bottom + 2, 20), cols=_RIGHT + 2
    )
    if cells:
        worksheet.update_cells(cells)
    return title
