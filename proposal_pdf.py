#!/usr/bin/env python3
"""DEMEX proposal PDF generator (reportlab).

Compiles a polished, corporate demolition-bid proposal from a single DEMEX
project. Mirrors the layout and boilerplate of ProposalsWorkspace in
DEMEX.html (BID #, project meta, inclusions, exclusions, special notes,
pricing with alternates, payment terms, signature, footer) but renders a
print-ready PDF instead of an HTML print view.

Input JSON (written by demex_bridge.py, also usable from the terminal):

    {
      "project": { ...DEMEX project object... },
      "company": { "name", "contact", "addr", "phone", "email",
                   "bidPrefix", "profitPct", "overheadPct" },
      "client":  { "name", "contactName", ... }   // optional
    }

Usage:
    python proposal_pdf.py --input proposal_in.json --out proposal.pdf

Requires reportlab (see requirements.txt).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape as _xml_escape

log = logging.getLogger("demex.proposal")

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                    Paragraph, Spacer, Table, TableStyle,
                                    ListFlowable, ListItem)
except ImportError:                                   # pragma: no cover
    raise SystemExit(
        "reportlab is required for proposal_pdf.py.\n"
        "Install it with:  pip install -r requirements.txt")

# --- brand + boilerplate (mirrors DEMEX.html) ------------------------------- #
BRAND = colors.HexColor("#e8332a")
INK = colors.HexColor("#111111")
GREY = colors.HexColor("#555555")
HAIR = colors.HexColor("#dddddd")

DEF_EXCL = [
    "Temporary power, temporary lighting, temporary water. Temporary site offices, temporary sanitary facilities.",
    "Barricade / temporary enclosures / temporary protection / dust barriers / road closures.",
    "Patching, repair work, welding, and refurbishing by others.",
    "Concrete pour back, in-fill. Removal of CMU walls.",
    "Roof demolition. Removal of main trunk lines and RTUs / AHUs.",
    "Remove furniture, equipment, safe, hood, appliances, workstations, and artwork.",
    "Shelving, fixtures, cabinetry, lockers, and such not shown in the drawings will be priced accordingly.",
    "Trash and/or debris left behind by previous tenant not shown in the drawings will be priced accordingly.",
    "Concrete x-ray / scanning by others. Markings & layouts by G.C.",
    "Asbestos abatement, asbestos survey, air monitoring, permit costs, sewer fee cost.",
    "Terminate cap & seal plumbing/sewer/gas lines. Electrical make safe.",
    "Core drilling, saw-cut, trench & remove slab. Excavate dirt / soil. Shoring & bracing by G.C.",
    "Refrigerant to be drained by HVAC contractor prior to demolition.",
    "Grinding, sanding, bead blasting and/or using or removing chemicals/solvents/paints.",
    "Any element found above ceiling, under flooring or behind walls not shown in the drawings is subject to a change order.",
    "Utility work / disconnects / re-routes. Fire protection demolition & sprinkler work by others.",
    "Prepare shop drawings, rebar / bar-bending schedule, core samples, and mock-ups.",
]
DEF_NOTES = [
    "Night work / after-hours access to be coordinated and provided by the GC as required.",
    "All work conforms to state and local code requirements.",
]
DEF_TERMS = [
    "Payment in full shall be made within thirty (30) days of completion.",
    "Taxes are not included in this proposal. Bonding is excluded.",
    "Quote is based on zero (0) retainage being withheld. All unpaid invoices shall carry interest at the rate of 2.5% per month until the balance is paid in full. In the event of any outstanding amount that must be collected by an attorney, the customer shall be liable for attorney's fees and costs of litigation. If payment is not received within 45 days from completion of job, a lien will be placed on the property.",
    "The party accepting this offer agrees that any dispute or conflict arising from the contents of this bid, agreement or contract shall be heard in district court in Bexar County, Texas.",
]


def _esc(s) -> str:
    """Escape &/</> so reportlab's Paragraph parser renders text literally."""
    return _xml_escape("" if s is None else str(s))


def _money(n) -> str:
    try:
        return "${:,.2f}".format(float(n or 0))
    except (TypeError, ValueError):
        return "$0.00"


def _split_lines(value) -> list[str]:
    """A field may arrive as a newline string (app) or a list."""
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = value.split("\n")
    else:
        items = []
    return [x.strip() for x in items if str(x).strip()]


def _seed_inclusions(project: dict) -> list[str]:
    """Mirror ProposalsWorkspace.seedIncl()."""
    eq = project.get("estQty") or project.get("estimated_quantities") or []
    lines = ["Remove and dispose of {} (approx. {} {}).".format(
        str(q.get("label") or q.get("item") or "designated item").lower(),
        q.get("qty"), q.get("unit") or "")
        for q in eq if (q.get("label") or q.get("item"))]
    if not lines:
        lines = ["Selective demolition per the demolition plans and keynotes, "
                 "including removal and disposal of designated finishes, "
                 "partitions, ceilings, fixtures and associated components."]
    lines.append("Remove and dispose of all demolition debris (C&D) generated "
                 "by the work to an approved landfill accepting this debris per "
                 "city, county & federal codes & EPA regulations.")
    lines.append("Provide daily clean up and leave all areas broom clean upon "
                 "completion.")
    return lines


def _bid_no(project: dict, company: dict) -> str:
    if project.get("bidNo"):
        return project["bidNo"]
    today = date.today()
    prefix = company.get("bidPrefix") or "DDD"
    return f"{prefix}-{today.year}/{today.month:02d}{today.day:02d}R1"


def build_pdf(payload: dict, out_path: str) -> None:
    project = payload.get("project") or {}
    company = payload.get("company") or {}
    if not project.get("name"):
        raise SystemExit("project.name is required to build a proposal.")

    # ---- styles ----
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["Normal"], fontName="Times-Roman",
                          fontSize=10.5, leading=15, textColor=INK)
    small = ParagraphStyle("small", parent=body, fontSize=9.5, leading=13)
    bullet = ParagraphStyle("bullet", parent=body, fontSize=9.5, leading=13)
    h2 = ParagraphStyle("h2", parent=body, fontName="Times-Bold", fontSize=11,
                        leading=14, textColor=INK, spaceBefore=14,
                        spaceAfter=4, letterSpacing=1)
    title = ParagraphStyle("title", parent=body, fontName="Times-Bold",
                           fontSize=20, leading=24, textColor=INK)
    meta_l = ParagraphStyle("meta_l", parent=body, fontName="Times-Bold",
                            fontSize=10)
    bidr = ParagraphStyle("bidr", parent=body, alignment=TA_RIGHT, fontSize=10)

    flow = []

    # ---- header band: company name + bid #/date ----
    co_name = company.get("name") or "DEMEX Demolition"
    header_left = Paragraph(
        f'<font color="white" size="15"><b>{_esc(co_name)}</b></font><br/>'
        f'<font color="#ffd9d4" size="8">Demolition &amp; Abatement &bull; '
        f'Commercial Selective Demolition</font>', body)
    bidno = _bid_no(project, company)
    datestr = date.today().strftime("%B %d, %Y")
    header_right = Paragraph(
        f'<font color="white" size="9"><b>BID #:</b> {_esc(bidno)}<br/>'
        f'{_esc(datestr)}</font>', bidr)
    band = Table([[header_left, header_right]], colWidths=[4.2 * inch, 2.6 * inch])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    flow.append(band)
    flow.append(Spacer(1, 16))
    flow.append(Paragraph("DEMOLITION PROPOSAL", title))
    flow.append(Spacer(1, 8))

    # ---- project meta ----
    client = payload.get("client") or {}
    meta_rows = [
        ("PROJECT NAME:", project.get("name", "")),
        ("PROJECT TYPE:", project.get("type") or "Open Shop"),
        ("SCOPE:", (project.get("scope") or "DEMOLITION").upper()),
        ("ADDRESS:", project.get("address") or ""),
    ]
    if client.get("name"):
        meta_rows.insert(1, ("CLIENT:", client.get("name")))
    meta_tbl = Table(
        [[Paragraph(k, meta_l), Paragraph(_esc(v), body)] for k, v in meta_rows],
        colWidths=[1.35 * inch, 5.45 * inch])
    meta_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    flow.append(meta_tbl)

    def section(label, items):
        flow.append(_rule_heading(label))
        flow.append(ListFlowable(
            [ListItem(Paragraph(_esc(x), bullet), leftIndent=10) for x in items],
            bulletType="bullet", start="square", leftIndent=14))

    # ---- inclusions ----
    proposal = project.get("proposal") or {}
    incl = _split_lines(proposal.get("inclusions")) or _seed_inclusions(project)
    flow.append(_rule_heading("OUR SCOPE OF WORK (INCLUSIONS)"))
    flow.append(Paragraph(
        f"{_esc(co_name)} hereby proposes and agrees to furnish all necessary "
        f"labor, material, machinery, equipment, tools, safety gear and "
        f"supervision required to perform the following scope of work:", small))
    flow.append(Spacer(1, 4))
    flow.append(ListFlowable(
        [ListItem(Paragraph(_esc(x), bullet), leftIndent=10) for x in incl],
        bulletType="bullet", start="square", leftIndent=14))

    # ---- exclusions ----
    excl = _split_lines(proposal.get("exclusions")) or DEF_EXCL
    section("EXCLUSIONS", excl)

    # ---- special notes ----
    notes = (_split_lines(proposal.get("specialNotes"))
             if proposal.get("specialNotes") is not None else DEF_NOTES)
    if notes:
        section("SPECIAL NOTES", notes)

    # ---- pricing ----
    est = project.get("estimate") or {}
    base = project.get("bidAmount") or est.get("sell") or 0
    alts = project.get("alternates") or []
    price_rows = [["BASE BID", _money(base)]]
    for a in alts:
        price_rows.append([f"Alternate: {a.get('desc', '')}", _money(a.get("amount"))])
    total = float(base or 0) + sum(float(a.get("amount") or 0) for a in alts)
    price_rows.append(["TOTAL", _money(total)])

    flow.append(_rule_heading("PRICING"))
    ptbl = Table(price_rows, colWidths=[5.3 * inch, 1.5 * inch])
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, HAIR),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEABOVE", (0, -1), (-1, -1), 1.4, INK),
        ("FONTNAME", (0, -1), (-1, -1), "Times-Bold"),
        ("FONTSIZE", (0, -1), (-1, -1), 13),
    ]
    ptbl.setStyle(TableStyle(style))
    flow.append(ptbl)

    # ---- payment terms ----
    section("PAYMENT TERMS", DEF_TERMS)

    # ---- signature ----
    flow.append(Spacer(1, 18))
    flow.append(Paragraph(
        "If you have any queries, please do not hesitate to contact the "
        "undersigned.<br/><br/>Best regards,<br/>"
        f"<b>{_esc(company.get('contact') or co_name)}</b>", body))

    # ---- build with footer ----
    out = Path(out_path)
    doc = BaseDocTemplate(
        str(out), pagesize=letter,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        topMargin=0.7 * inch, bottomMargin=0.85 * inch,
        title=f"{project.get('name')} — Proposal", author=co_name)
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="main")

    footer_txt = " • ".join(
        x for x in [company.get("addr"),
                    f"Mob {company.get('phone')}" if company.get("phone") else "",
                    f"E-Mail {company.get('email')}" if company.get("email") else ""]
        if x)

    def on_page(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Times-Roman", 8)
        canvas.setFillColor(GREY)
        canvas.setStrokeColor(HAIR)
        y = 0.62 * inch
        canvas.line(_doc.leftMargin, y + 12, letter[0] - _doc.rightMargin, y + 12)
        if footer_txt:
            canvas.drawCentredString(letter[0] / 2.0, y, footer_txt)
        canvas.drawRightString(letter[0] - _doc.rightMargin, y,
                               "Page %d" % _doc.page)
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                       onPage=on_page)])
    doc.build(flow)
    log.info("Wrote proposal PDF -> %s", out)


def _rule_heading(text: str):
    """A bold section heading with a brand-colored underline rule."""
    style = ParagraphStyle(
        "ruleHead", fontName="Times-Bold", fontSize=11, leading=14,
        textColor=INK, spaceBefore=16, spaceAfter=2,
        borderColor=BRAND, borderWidth=0, underlineWidth=2)
    t = Table([[Paragraph(text, style)]], colWidths=[6.8 * inch])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 1.6, INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
    ]))
    return t


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate a corporate DEMEX proposal PDF from a project.")
    p.add_argument("--input", required=True,
                   help="JSON with {project, company, client?}.")
    p.add_argument("--out", required=True, help="Output PDF path.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s")
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    build_pdf(payload, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
