"""Starter model: a Siebel Contact exposed as the API resource `customer`, a Case
linked to it twice (account holder and requester), an embedded Address, and
Payments held in a separate REST service."""
from __future__ import annotations

from typing import Any, Dict, List


def _attr(id_: str, business: str, api: str, type_: str = "string", **kw: Any) -> Dict[str, Any]:
    a: Dict[str, Any] = {
        "id": id_, "business_name": business, "api_name": api, "type": type_, "format": "",
        "max_length": None, "key": False, "required": False, "read_only": False,
        "system": False, "filterable": False, "pii": False, "enum": [], "source_field": "", "source_column": "",
        "lov_type": "", "transform": "", "dd_reference": "", "example": "", "description": "",
    }
    a.update(kw)
    return a


def _enum(*rows: str) -> List[Dict[str, str]]:
    out = []
    for r in rows:
        code, label, src = r.split("|")
        out.append({"code": code, "label": label, "source_value": src})
    return out


def sample_model() -> Dict[str, Any]:
    contact = {
        "id": "en_contact", "namespace_id": "ns_cm", "business_name": "Contact",
        "api_name": "customer", "plural": "customers", "schema_name": "Customer",
        "description": "A customer. Held as a Contact in the source system; exposed as `customer`.",
        "source_object": "Contact", "source_io": "Contact IO",
        "operations": ["list", "get", "create", "patch"], "x": 60, "y": 60,
        "attributes": [
            _attr("at_c_id", "Contact Id", "customerId", key=True, read_only=True, system=True,
                  source_field="Id", source_column="S_CONTACT.ROW_ID", example="1-7ABC12",
                  description="Unique identifier of the customer."),
            _attr("at_c_num", "Customer Number", "customerNumber", required=True, filterable=True, pii=True,
                  max_length=12, source_field="Customer Number", source_column="S_CONTACT.X_CUST_NUM",
                  example="C1234567", description="Customer number shown on letters."),
            _attr("at_c_fn", "First Name", "firstName", required=True, pii=True, max_length=50,
                  source_field="First Name", source_column="S_CONTACT.FST_NAME", example="Alex"),
            _attr("at_c_ln", "Last Name", "lastName", required=True, filterable=True, pii=True, max_length=50,
                  source_field="Last Name", source_column="S_CONTACT.LAST_NAME", example="Morgan"),
            _attr("at_c_dob", "Date of Birth", "dateOfBirth", "date", pii=True,
                  source_field="Birth Date", source_column="S_CONTACT.BIRTH_DT", example="1985-04-12"),
            _attr("at_c_em", "Email Address", "email", format="email", pii=True, max_length=100,
                  source_field="Email Address", source_column="S_CONTACT.EMAIL_ADDR",
                  example="alex.morgan@example.com"),
            _attr("at_c_pcm", "Preferred Contact Method", "preferredContactMethod", "enum",
                  enum=_enum("EMAIL|Email|Email", "POST|Post|Letter", "PHONE|Phone|Telephone", "SMS|Text message|SMS"),
                  lov_type="CONTACT_METHOD", source_field="Preferred Contact Method",
                  source_column="S_CONTACT.X_PREF_METHOD", example="EMAIL"),
            _attr("at_c_cy", "Marketing Opt In", "marketingOptIn", "boolean",
                  source_field="Marketing Opt In Flag", source_column="S_CONTACT.X_MKTG_OPT_IN_FLG",
                  transform="Y/N to true/false", example="false"),
            _attr("at_c_upd", "Last Updated", "updatedAt", "date-time", read_only=True, system=True,
                  source_field="Updated", source_column="S_CONTACT.LAST_UPD", example="2026-10-14T09:30:00Z"),
        ],
    }
    case = {
        "id": "en_case", "namespace_id": "ns_cm", "business_name": "Case",
        "api_name": "case", "plural": "cases", "schema_name": "Case",
        "description": "A customer service case, raised by a requester on behalf of an account holder.",
        "source_object": "HLS Case", "source_io": "Case IO",
        "operations": ["list", "get", "create", "patch"], "x": 480, "y": 60,
        "attributes": [
            _attr("at_k_id", "Case Id", "caseId", key=True, read_only=True, system=True,
                  source_field="Id", source_column="S_CASE.ROW_ID", example="1-9XYZ78"),
            _attr("at_k_num", "Case Number", "caseNumber", required=True, filterable=True, max_length=20, system=True,
                  source_field="Case Num", source_column="S_CASE.CASE_NUM", example="CS-10000002"),
            _attr("at_k_st", "Case Status", "status", "enum", required=True, filterable=True,
                  enum=_enum("ACTIVE|Active|Active", "SUSPENDED|Suspended|Suspended", "CLOSED|Closed|Closed"),
                  lov_type="CASE_STATUS", source_field="Status", source_column="S_CASE.STATUS_CD",
                  example="ACTIVE"),
            _attr("at_k_stg", "Case Stage", "stage", "enum",
                  enum=_enum("INTAKE|Intake|Intake", "INVESTIGATION|Investigation|Investigation",
                             "RESOLUTION|Resolution|Resolution", "FOLLOW_UP|Follow up|Follow Up"),
                  lov_type="CASE_STAGE", source_field="Sub Status", source_column="S_CASE.SUB_STATUS_CD",
                  example="RESOLUTION"),
            _attr("at_k_svc", "Service Type", "serviceType", "enum",
                  enum=_enum("STANDARD|Standard|Standard", "PREMIUM|Premium|Premium"),
                  lov_type="SERVICE_TYPE", source_field="Service Type",
                  source_column="S_CASE.X_SVC_TYPE", example="STANDARD"),
            _attr("at_k_open", "Opened Date", "openedDate", "date",
                  source_field="Open Date", source_column="S_CASE.OPEN_DT", example="2024-02-01"),
            _attr("at_k_liab", "Outstanding Balance", "outstandingBalance", "integer", money=True,
                  source_field="Outstanding Balance", source_column="S_CASE.X_OUTSTANDING_BAL",
                  transform="Source amount in pounds × 100 = amount in pence; currency GBP.",
                  example="42.50", description="Amount still owed on the case."),
            _attr("at_k_upd", "Last Updated", "updatedAt", "date-time", read_only=True, system=True,
                  source_field="Updated", source_column="S_CASE.LAST_UPD", example="2026-10-15T11:05:00Z"),
        ],
    }
    address = {
        "id": "en_address", "namespace_id": "ns_cm", "business_name": "Contact Address",
        "api_name": "address", "plural": "addresses", "schema_name": "Address",
        "description": "Postal address of a customer. Not a top-level resource; returned inside a customer.",
        "source_object": "CUT Address", "source_io": "Contact IO",
        "operations": [], "x": 60, "y": 470,
        "attributes": [
            _attr("at_a_id", "Address Id", "addressId", key=True, read_only=True, system=True,
                  source_field="Id", source_column="S_ADDR_PER.ROW_ID", example="1-4QRS56"),
            _attr("at_a_typ", "Address Type", "addressType", "enum",
                  enum=_enum("HOME|Home|Home", "CORRESPONDENCE|Correspondence|Correspondence"),
                  lov_type="ADDRESS_TYPE", source_field="Address Type",
                  source_column="S_CON_ADDR.ADDR_TYPE_CD", example="HOME"),
            _attr("at_a_l1", "Address Line 1", "line1", required=True, pii=True, max_length=100,
                  source_field="Street Address", source_column="S_ADDR_PER.ADDR", example="1 High Street"),
            _attr("at_a_l2", "Address Line 2", "line2", pii=True, max_length=100,
                  source_field="Street Address 2", source_column="S_ADDR_PER.ADDR_LINE_2"),
            _attr("at_a_town", "Town", "town", max_length=50,
                  source_field="City", source_column="S_ADDR_PER.CITY", example="Leeds"),
            _attr("at_a_pc", "Postcode", "postcode", required=True, pii=True, max_length=8,
                  source_field="Postal Code", source_column="S_ADDR_PER.ZIPCODE", example="LS1 4AB"),
        ],
    }
    # example data dictionary references (replace with your DD attribute ids)
    # A non-Siebel example: payments held in a separate REST service.
    payment = {
        "id": "en_payment", "namespace_id": "ns_cm", "source_system_id": "ss_payments",
        "business_name": "Payment", "api_name": "payment", "plural": "payments", "schema_name": "Payment",
        "description": "A payment received or refunded. Held in the payments service, not Siebel.",
        "source_object": "/v2/payments", "source_io": "Payments API v2",
        "operations": ["list", "get"], "x": 480, "y": 470,
        "attributes": [
            _attr("at_p_id", "Payment Id", "paymentId", key=True, read_only=True, system=True,
                  source_field="$.id", source_column="GET /v2/payments/{id}", example="PAY-000123"),
            _attr("at_p_ref", "Payment Reference", "reference", required=True, filterable=True, max_length=20,
                  source_field="$.reference", example="PR-88213"),
            _attr("at_p_amt", "Amount", "amount", "integer", money=True, required=True,
                  source_field="$.amountInPence", transform="Already in pence; currency GBP.", example="42.50"),
            _attr("at_p_dt", "Payment Date", "paymentDate", "date", required=True, filterable=True,
                  source_field="$.paidOn", example="2026-10-01"),
            _attr("at_p_dir", "Direction", "direction", "enum", required=True,
                  enum=_enum("RECEIVED|Received from the customer|IN", "REFUNDED|Refunded to the customer|OUT"),
                  lov_type="PaymentDirection", source_field="$.direction", example="RECEIVED"),
            _attr("at_p_meth", "Payment Method", "method", "enum",
                  enum=_enum("DIRECT_DEBIT|Direct Debit|DD", "STANDING_ORDER|Standing order|SO", "CARD|Debit card|CARD"),
                  lov_type="PaymentMethod", source_field="$.method", example="DIRECT_DEBIT"),
            _attr("at_p_st", "Payment Status", "status", "enum", filterable=True,
                  enum=_enum("PENDING|Pending|pending", "CLEARED|Cleared|cleared", "RETURNED|Returned|returned"),
                  lov_type="PaymentStatus", source_field="$.status", example="CLEARED"),
            _attr("at_p_upd", "Last Updated", "updatedAt", "date-time", read_only=True, system=True,
                  source_field="$.meta.lastModified", example="2026-10-02T06:00:00Z"),
        ],
    }
    for e in (contact, case, address):
        e["source_system_id"] = "ss_siebel"
    for prefix, ent in (("PER", contact), ("CAS", case), ("ADR", address), ("PAY", payment)):
        for i, a in enumerate(ent["attributes"], 1):
            a["dd_reference"] = "DD-" + prefix + "-" + str(i).zfill(3)
    return {
        "schema_version": 2,
        "settings": {"title": "API catalogue", "organisation": "Example Organisation",
                     "source_system": ""},
        "source_systems": [
            {"id": "ss_siebel", "key": "siebel", "name": "Siebel CRM", "kind": "siebel",
             "description": "Customer case management.", "owner": "", "internal_tokens": None},
            {"id": "ss_payments", "key": "payments", "name": "Payments Service", "kind": "rest",
             "description": "Example non-Siebel source: payments held outside the CRM.", "owner": "",
             "internal_tokens": None},
        ],
        "namespaces": [{
            "id": "ns_cm", "key": "cm", "title": "Case Management API", "base_path": "/cm/v1",
            "version": "1.0.0", "description": "Cases and the customers linked to them.",
            "security": "bearer", "owner": "API team",
            "servers": [{"url": "https://api.dev.example.internal", "description": "Development"},
                        {"url": "https://api.test.example.internal", "description": "Test"}],
        }],
        "entities": [contact, case, address, payment],
        "relationships": [
            {"id": "rl_pp", "from_entity_id": "en_case", "to_entity_id": "en_contact",
             "cardinality": "many-to-one", "business_name": "Account holder", "api_name": "accountHolder",
             "expose": "link", "inverse_business_name": "Cases as account holder",
             "inverse_api_name": "casesAsAccountHolder", "inverse_expose": "subresource",
             "join_from": "HLS Case.Account Holder Id", "join_to": "Contact.Id",
             "source_link": "HLS Case/Contact (Account Holder)", "description": "The customer who holds the account."},
            {"id": "rl_rp", "from_entity_id": "en_case", "to_entity_id": "en_contact",
             "cardinality": "many-to-one", "business_name": "Requester", "api_name": "requester",
             "expose": "link", "inverse_business_name": "Cases as requester",
             "inverse_api_name": "casesAsRequester", "inverse_expose": "subresource",
             "join_from": "HLS Case.Requester Id", "join_to": "Contact.Id",
             "source_link": "HLS Case/Contact (Requester)", "description": "The customer who raised the case."},
            {"id": "rl_addr", "from_entity_id": "en_contact", "to_entity_id": "en_address",
             "cardinality": "one-to-many", "business_name": "Addresses", "api_name": "addresses",
             "expose": "embedded", "inverse_business_name": "", "inverse_api_name": "",
             "inverse_expose": "none", "join_from": "Contact.Id", "join_to": "CUT Address.Person Id",
             "source_link": "Contact/CUT Address", "description": "Postal addresses held for the customer.",
             "nested_create": True},
            {"id": "rl_pay", "from_entity_id": "en_case", "to_entity_id": "en_payment",
             "cardinality": "one-to-many", "business_name": "Payments", "api_name": "payments",
             "expose": "subresource", "inverse_business_name": "Case", "inverse_api_name": "case",
             "inverse_expose": "link", "join_from": "HLS Case.Case Num (Siebel)",
             "join_to": "$.caseReference (Payments Service)", "source_link": "",
             "description": "Payments for the case, matched across systems on the case number."},
        ],
    }
