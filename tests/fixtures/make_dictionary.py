"""Builds a small sample Siebel dictionary workbook (the six-column export) that
matches the studio's starter model, plus a few fields and business components
that are not columns yet. Used by the tests and shipped in docs/ as an example.

    python tests/fixtures/make_dictionary.py docs/sample-siebel-dictionary.xlsx
"""
from __future__ import annotations

import io
import sys

HEADERS = ["Business Component (BC) Name", "BC Field Name", "Physical Table Name", "Physical Column Name",
           "DD Attribute Reference", "Data Item Full Name"]

ROWS = [
    # Contact: the starter's columns, then fields not yet used
    ("Contact", "Id", "S_CONTACT", "ROW_ID", "DD-PER-001", "Person Identifier"),
    ("Contact", "Customer Number", "S_CONTACT", "X_CUST_NUM", "DD-PER-002", "Customer Number"),
    ("Contact", "First Name", "S_CONTACT", "FST_NAME", "DD-PER-003", "Person First Name"),
    ("Contact", "Last Name", "S_CONTACT", "LAST_NAME", "DD-PER-004", "Person Last Name"),
    ("Contact", "Birth Date", "S_CONTACT", "BIRTH_DT", "DD-PER-005", "Person Date of Birth"),
    ("Contact", "Email Address", "S_CONTACT", "EMAIL_ADDR", "DD-PER-006", "Person Email Address"),
    ("Contact", "Preferred Contact Method", "S_CONTACT", "X_PREF_METHOD", "DD-PER-007", "Preferred Contact Method"),
    ("Contact", "Marketing Opt In Flag", "S_CONTACT", "X_MKTG_OPT_IN_FLG", "DD-PER-008", "Marketing Opt In"),
    ("Contact", "Updated", "S_CONTACT", "LAST_UPD", "DD-PER-009", "Record Last Updated"),
    ("Contact", "Middle Name", "S_CONTACT", "MID_NAME", "DD-PER-010", "Person Middle Name"),
    ("Contact", "Cellular Phone #", "S_CONTACT", "CELL_PH_NUM", "DD-PER-011", "Person Mobile Telephone Number"),
    ("Contact", "Home Phone #", "S_CONTACT", "HOME_PH_NUM", "DD-PER-012", "Person Home Telephone Number"),
    ("Contact", "Gender", "S_CONTACT", "SEX_MF", "DD-PER-013", "Person Gender"),
    ("Contact", "Tax Reference", "S_CONTACT_X", "ATTRIB_03", "DD-PER-014", "Tax Reference Number"),
    # HLS Case
    ("HLS Case", "Id", "S_CASE", "ROW_ID", "DD-CAS-001", "Case Identifier"),
    ("HLS Case", "Case Num", "S_CASE", "CASE_NUM", "DD-CAS-002", "Case Number"),
    ("HLS Case", "Status", "S_CASE", "STATUS_CD", "DD-CAS-003", "Case Status"),
    ("HLS Case", "Sub Status", "S_CASE", "SUB_STATUS_CD", "DD-CAS-004", "Case Stage"),
    ("HLS Case", "Service Type", "S_CASE", "X_SVC_TYPE", "DD-CAS-005", "Case Service Type"),
    ("HLS Case", "Open Date", "S_CASE", "OPEN_DT", "DD-CAS-006", "Case Opened Date"),
    ("HLS Case", "Outstanding Balance", "S_CASE", "X_OUTSTANDING_BAL", "DD-CAS-007", "Outstanding Balance Amount"),
    ("HLS Case", "Updated", "S_CASE", "LAST_UPD", "DD-CAS-008", "Record Last Updated"),
    ("HLS Case", "Account Holder Id", "S_CASE", "X_ACCT_HOLDER_ID", "DD-CAS-009", "Account Holder Identifier"),
    ("HLS Case", "Requester Id", "S_CASE", "X_REQUESTER_ID", "DD-CAS-010", "Requester Identifier"),
    ("HLS Case", "Agreement Flag", "S_CASE_X", "ATTRIB_11", "DD-CAS-011", "Agreement Indicator"),
    # CUT Address
    ("CUT Address", "Id", "S_ADDR_PER", "ROW_ID", "DD-ADR-001", "Address Identifier"),
    ("CUT Address", "Address Type", "S_CON_ADDR", "ADDR_TYPE_CD", "DD-ADR-002", "Address Type"),
    ("CUT Address", "Street Address", "S_ADDR_PER", "ADDR", "DD-ADR-003", "Address Line 1"),
    ("CUT Address", "Street Address 2", "S_ADDR_PER", "ADDR_LINE_2", "DD-ADR-004", "Address Line 2"),
    ("CUT Address", "City", "S_ADDR_PER", "CITY", "DD-ADR-005", "Address Town"),
    ("CUT Address", "Postal Code", "S_ADDR_PER", "ZIPCODE", "DD-ADR-006", "Address Postcode"),
    ("CUT Address", "Country", "S_ADDR_PER", "COUNTRY", "DD-ADR-007", "Address Country"),
    # business components that are not entities yet
    ("Agreement", "Id", "CX_AGREEMENT", "ROW_ID", "DD-AGR-001", "Agreement Identifier"),
    ("Agreement", "Case Id", "CX_AGREEMENT", "CASE_ID", "DD-AGR-002", "Case Identifier"),
    ("Agreement", "Product Id", "CX_AGREEMENT", "PRODUCT_ID", "DD-AGR-003", "Product Identifier"),
    ("Agreement", "Hours Per Month", "CX_AGREEMENT", "HOURS_PER_MONTH", "DD-AGR-004", "Agreement Hours Per Month"),
    ("Agreement", "Band", "CX_AGREEMENT", "BAND_CD", "DD-AGR-005", "Agreement Band"),
    ("Agreement", "Effective Date", "CX_AGREEMENT", "EFF_START_DT", "DD-AGR-006", "Agreement Effective From"),
    ("Agreement", "Person Id", "CX_AGREEMENT", "PER_ID", "DD-AGR-007", "Declaring Person Identifier"),
    ("Service Request", "SR Number", "S_SRV_REQ", "SR_NUM", "DD-SRQ-001", "Service Request Number"),
    ("Service Request", "SR Type", "S_SRV_REQ", "SR_TYPE_CD", "DD-SRQ-002", "Service Request Type"),
    ("Service Request", "Status", "S_SRV_REQ", "SR_STAT_ID", "DD-SRQ-003", "Service Request Status"),
    ("Service Request", "Opened Date", "S_SRV_REQ", "ACT_OPEN_DT", "DD-SRQ-004", "Service Request Opened Date"),
]


def workbook_bytes() -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Data Dictionary"
    ws.append(["Siebel data dictionary extract (sample)"])
    ws.append([])
    ws.append(HEADERS)
    for c in ws[3]:
        c.font = Font(bold=True)
    for r in ROWS:
        ws.append(list(r))
    for col, w in zip("ABCDEF", (28, 30, 20, 22, 22, 34)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample-siebel-dictionary.xlsx"
    with open(out, "wb") as f:
        f.write(workbook_bytes())
    print("wrote", out, len(ROWS), "rows")
