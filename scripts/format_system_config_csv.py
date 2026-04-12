"""Format data/system_config.csv with padded columns for plain-text readability.

Run from repo root: python scripts/format_system_config_csv.py

core/config.py strips key, value, and type — trailing pad spaces on those are safe.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path


def _pad(text: str, width: int) -> str:
    t = text if text is not None else ""
    if len(t) >= width:
        return t
    return t + (" " * (width - len(t)))


def main() -> None:
    repo = Path(__file__).resolve().parent.parent
    path = repo / "data" / "system_config.csv"
    raw_text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(raw_text))
    fieldnames = list(reader.fieldnames or [])
    if not fieldnames:
        raise SystemExit("missing CSV header")

    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({k: (row.get(k) or "") for k in fieldnames})

    data_rows = [
        r
        for r in rows
        if (r.get("key") or "").strip() and not (r.get("key") or "").strip().startswith("#")
    ]

    w_key = max(48, max(len((r["key"] or "").strip()) for r in data_rows) + 1)
    w_type = max(7, max(len((r["type"] or "").strip()) for r in data_rows) + 1)
    w_min = max(8, max(len((r["min_value"] or "").strip()) for r in data_rows) + 1)
    w_max = max(8, max(len((r["max_value"] or "").strip()) for r in data_rows) + 1)
    w_opt = max(6, max(len((r["options"] or "").strip()) for r in data_rows) + 1)

    def _measure_val(v: str) -> str:
        return (v or "").rstrip()

    max_val_len = max(len(_measure_val(r["value"])) for r in data_rows)
    w_val = min(max_val_len + 1, 76)

    max_desc_len = max(len(_measure_val(r["description"])) for r in data_rows)
    w_desc = min(max_desc_len + 2, 82)

    out_buf = io.StringIO()
    writer = csv.writer(out_buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)

    writer.writerow(fieldnames)
    writer.writerow([])

    prev_was_data = False
    for r in rows:
        key = (r.get("key") or "").strip()
        if not key:
            writer.writerow([])
            prev_was_data = False
            continue
        if key.startswith("#"):
            if prev_was_data:
                writer.writerow([])
            padded_key = _pad(key, w_key)
            writer.writerow([padded_key] + [""] * (len(fieldnames) - 1))
            prev_was_data = False
            continue

        val = _measure_val(r.get("value") or "")
        typ = (r.get("type") or "").strip()
        desc = _measure_val(r.get("description") or "")
        mn = (r.get("min_value") or "").strip()
        mx = (r.get("max_value") or "").strip()
        opt = (r.get("options") or "").strip()

        val_out = _pad(val, w_val) if len(val) <= w_val else val
        writer.writerow(
            [
                _pad(key, w_key),
                val_out,
                _pad(typ, w_type),
                _pad(desc, w_desc),
                _pad(mn, w_min),
                _pad(mx, w_max),
                _pad(opt, w_opt),
            ]
        )
        prev_was_data = True

    path.write_text(out_buf.getvalue(), encoding="utf-8")
    print(
        "Wrote",
        path,
        f"widths key={w_key} val={w_val} type={w_type} desc={w_desc} min={w_min} max={w_max} opt={w_opt}",
    )


if __name__ == "__main__":
    main()
