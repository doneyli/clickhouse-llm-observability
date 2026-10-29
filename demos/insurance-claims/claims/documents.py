"""
Synthetic claim documents rendered as images, with known field values.

Every document carries a SYNTHETIC watermark and no government seal or branding:
these are test fixtures for a fictional insurer, not lookalike official records.

    render_death_certificate(fields, path)   → PNG
    render_claimant_statement(fields, path)  → PNG
    render_beneficiary_form(fields, path)    → PNG

The renderers draw exactly the values they are given, so a dataset item can hold
the ground truth (what was drawn) next to the image (what the agent reads), and
deliberately inconsistent packets are just different values on two documents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 1360
INK = (25, 25, 35)
RULE = (120, 120, 140)
MARK = (205, 70, 70)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _page(title: str, subtitle: str):
    img = Image.new("RGB", (W, H), (252, 251, 247))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, W - 40, H - 40], outline=RULE, width=3)
    d.text((W // 2, 110), title, font=_font(46), fill=INK, anchor="mm")
    d.text((W // 2, 165), subtitle, font=_font(24), fill=RULE, anchor="mm")
    d.line([80, 200, W - 80, 200], fill=RULE, width=2)
    return img, d


def _watermark(img: Image.Image) -> Image.Image:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((W // 2, H // 2), "SYNTHETIC SAMPLE — DEMO DATA — NOT AN OFFICIAL RECORD",
                               font=_font(30), fill=MARK + (70,), anchor="mm")
    layer = layer.rotate(28, center=(W // 2, H // 2))
    out = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    ImageDraw.Draw(out).text((W // 2, H - 70), "SYNTHETIC SAMPLE · Northwind Life & Annuity claims demo",
                             font=_font(22), fill=MARK, anchor="mm")
    return out


def _rows(d: ImageDraw.ImageDraw, rows: list[tuple[str, Optional[str]]], top: int = 240, step: int = 78):
    y = top
    for label, value in rows:
        d.text((90, y), label.upper(), font=_font(20), fill=RULE)
        d.text((90, y + 26), value if value not in (None, "") else "—", font=_font(32), fill=INK)
        d.line([90, y + 66, W - 90, y + 66], fill=(215, 215, 222), width=1)
        y += step
    return y


def _paragraph(d: ImageDraw.ImageDraw, text: str, top: int, size: int = 26, width_chars: int = 74):
    import textwrap
    y = top
    for para in text.split("\n"):
        for line in textwrap.wrap(para, width_chars) or [""]:
            d.text((90, y), line, font=_font(size), fill=INK)
            y += size + 12
        y += 10
    return y


def render_death_certificate(f: dict, path: Path) -> Path:
    img, d = _page("CERTIFICATE OF DEATH", "Synthetic sample for the claims demo")
    _rows(d, [
        ("Certificate number", f.get("certificate_number")),
        ("Name of decedent", f.get("decedent_name")),
        ("Date of birth", f.get("date_of_birth")),
        ("Date of death", f.get("date_of_death")),
        ("State where death occurred", f.get("place_of_death_state")),
        ("Immediate cause of death", f.get("cause_of_death")),
        ("Manner of death", f.get("manner_of_death")),
        ("Certifier", f.get("certifier")),
        ("Date certified", f.get("date_certified")),
        ("Copy type", "CERTIFIED COPY — issued by the vital records office" if f.get("certified", True)
         else "UNCERTIFIED PHOTOCOPY — not issued by vital records"),
    ])
    if f.get("note"):
        _paragraph(d, f"Remarks: {f['note']}", top=1040, size=22, width_chars=70)
    path.parent.mkdir(parents=True, exist_ok=True)
    _watermark(img).save(path, optimize=True)
    return path


def render_claimant_statement(f: dict, path: Path) -> Path:
    img, d = _page("CLAIMANT'S STATEMENT — DEATH CLAIM", "Northwind Life & Annuity · synthetic sample")
    y = _rows(d, [
        ("Policy number", f.get("policy_number")),
        ("Name of insured", f.get("insured_name")),
        ("Date of death", f.get("date_of_death")),
        ("Claimant name", f.get("claimant_name")),
        ("Relationship to insured", f.get("relationship_to_insured")),
        ("Payout method", f.get("payout_method")),
    ])
    if f.get("statement"):
        d.text((90, y + 10), "CLAIMANT'S STATEMENT", font=_font(20), fill=RULE)
        _paragraph(d, f["statement"], top=y + 44, size=24, width_chars=66)
    path.parent.mkdir(parents=True, exist_ok=True)
    _watermark(img).save(path, optimize=True)
    return path


def render_beneficiary_form(f: dict, path: Path) -> Path:
    img, d = _page("BENEFICIARY DESIGNATION ON FILE", "Northwind Life & Annuity · synthetic sample")
    y = _rows(d, [("Policy number", f.get("policy_number")), ("Owner", f.get("owner_name")),
                  ("Designation date", f.get("designation_date"))])
    d.text((90, y + 10), "BENEFICIARIES", font=_font(20), fill=RULE)
    y += 50
    for b in f.get("beneficiaries", []):
        d.text((90, y), f"{b['name']} — {b['relationship']} — {b['share_pct']}% — born {b.get('date_of_birth') or '—'}",
               font=_font(28), fill=INK)
        y += 52
    path.parent.mkdir(parents=True, exist_ok=True)
    _watermark(img).save(path, optimize=True)
    return path
