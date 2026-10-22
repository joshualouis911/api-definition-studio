"""Worked examples that can be added to any model.

agreements: a service agreements API. A service agreement sets the support
hours a month a customer gets for one product on a case. It is held on the
case's party relationship records and recorded directly while the case is set
up; after that it changes only through an agreement change, which Siebel holds
as a service request with both parties' details and answers. A decision on the
change is applied to the case's agreements as a new agreement version.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .erd_import import merge
from .model import clean_tags
from .store import fill_defaults

EXAMPLES = {"agreements": "Service agreements: versions, changes through a service request, declarations, decision"}


def _attr(id_: str, business: str, api: str, type_: str = "string", **kw: Any) -> Dict[str, Any]:
    a: Dict[str, Any] = {
        "id": id_, "business_name": business, "api_name": api, "type": type_, "format": "", "max_length": None,
        "key": False, "required": False, "read_only": False, "system": False, "filterable": False, "pii": False,
        "enum": [], "source_field": "", "source_column": "", "lov_type": "", "transform": "", "dd_reference": "",
        "example": "", "description": "",
    }
    a.update(kw)
    return a


def _enum(*rows: str) -> List[Dict[str, str]]:
    out = []
    for r in rows:
        code, label = r.split("|")
        out.append({"code": code, "label": label, "source_value": ""})
    return out


PARTY_ROLE = _enum("ACCOUNT_HOLDER|Account holder", "REQUESTER|Requester")
BASIS = _enum("CONTRACT|Signed contract", "AGREEMENT|Agreed between the parties", "PAST_USAGE|Past usage",
              "TRIAL|Trial period", "DEFAULT|Default allowance")
BAND = _enum("NONE|Under 5 hours: no agreement", "BRONZE|5 to 19 hours: bronze",
             "SILVER|20 to 39 hours: silver", "GOLD|40 to 79 hours: gold",
             "PLATINUM|80 hours or more: platinum")


def _entity(id_: str, ns: str, src: str, business: str, api: str, plural: str, schema: str, desc: str,
            source_object: str, ops: List[str], attrs: List[Dict[str, Any]], **kw: Any) -> Dict[str, Any]:
    e = {"id": id_, "namespace_id": ns, "source_system_id": src, "business_name": business, "api_name": api,
         "plural": plural, "schema_name": schema, "description": desc, "source_object": source_object,
         "source_io": "", "operations": ops, "x": None, "y": None, "attributes": attrs,
         "op_overrides": {}, "actions": []}
    e.update(kw)
    return e


def _rel(id_: str, f: str, t: str, card: str, business: str, api: str, expose: str, **kw: Any) -> Dict[str, Any]:
    r = {"id": id_, "from_entity_id": f, "to_entity_id": t, "cardinality": card, "business_name": business,
         "api_name": api, "expose": expose, "inverse_business_name": "", "inverse_api_name": "",
         "inverse_expose": "none", "join_from": "", "join_to": "", "source_link": "", "description": "",
         "sub_ops": [], "sub_key": "", "sub_overrides": {}, "inverse_sub_ops": [], "inverse_sub_key": "",
         "inverse_sub_overrides": {}, "read_only": False, "nested_create": False}
    r.update(kw)
    return r


def service_agreements(model: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """The service agreements fragment, placed in the model's `cm` namespace and its
    Siebel source system (created if missing). Returns (fragment, notes)."""
    notes: List[str] = []
    ns = next((n for n in model["namespaces"] if n["key"] == "cm"), None)
    namespaces = []
    if ns:
        ns_id = ns["id"]
    else:
        ns_id = "ns_cm"
        namespaces.append({"id": ns_id, "key": "cm", "title": "Case Management API", "base_path": "/cm/v1",
                           "version": "1.0.0", "description": "Cases and the customers linked to them.",
                           "security": "bearer", "owner": "", "base_path_in": "paths",
                           "servers": model["namespaces"][0]["servers"] if model["namespaces"] else []})
        notes.append("Added the cm namespace (Case Management API, /cm/v1).")
    siebel = next((s for s in model.get("source_systems", []) if s["kind"] == "siebel"), None)
    src_systems = []
    if siebel:
        src = siebel["id"]
    else:
        src = "ss_siebel"
        src_systems.append({"id": src, "key": "siebel", "name": "Siebel CRM", "kind": "siebel", "description": "",
                            "owner": "", "internal_tokens": None})
        notes.append("Added a Siebel CRM source system.")

    case = _entity("en_sc_case", ns_id, src, "Case", "case", "cases", "Case",
                   "A customer service case.", "HLS Case", ["get"],
                   [_attr("at_sc_case_id", "Case Id", "caseId", key=True, read_only=True, system=True,
                          source_field="Id", example="1-9XYZ78")])

    agreement = _entity(
        "en_sc_arr", ns_id, src, "Service Agreement", "serviceAgreement", "agreements",
        "ServiceAgreement",
        "A service agreement for one product on a case: the support hours a month the customer gets, the band "
        "it puts them in, and each party's declaration. Held on the case's party relationship records. "
        "Recorded directly while the case is set up; after that it changes only through an agreement change, "
        "which adds a new version.",
        "Party Relationship (case party)", ["get"],
        [_attr("at_sca_id", "Agreement Id", "agreementId", key=True, read_only=True, system=True,
               source_field="Id", example="1-SCA1001"),
         _attr("at_sca_prod", "Product Id", "productId", required=True, example="1-PRD771",
               description="The product the agreement is for."),
         _attr("at_sca_hours", "Hours Per Month", "hoursPerMonth", "integer", required=True, example="24",
               description="Support hours a month the customer gets for the product."),
         _attr("at_sca_band", "Agreement Band", "band", "enum", read_only=True, system=True, enum=BAND,
               example="SILVER", description="Set from the hours decided."),
         _attr("at_sca_basis", "Basis", "basis", "enum", required=True, enum=BASIS, example="AGREEMENT",
               description="What the hours are based on."),
         _attr("at_sca_from", "Effective From", "effectiveFrom", "date", required=True, example="2026-11-02"),
         _attr("at_sca_to", "Effective To", "effectiveTo", "date", read_only=True, system=True,
               description="Set when a later version replaces this one."),
         _attr("at_sca_status", "Agreement Status", "status", "enum", read_only=True, system=True, filterable=True,
               enum=_enum("PROPOSED|Proposed, awaiting decision", "DECIDED|Decided", "ENDED|Ended, replaced by a later version"),
               example="DECIDED"),
         _attr("at_sca_origin", "Origin", "origin", "enum", read_only=True, system=True,
               enum=_enum("SET_UP|Recorded while the case was set up", "CHANGE|Applied from an agreement change"),
               example="SET_UP"),
         _attr("at_sca_dec", "Decision Id", "decisionId", read_only=True, system=True, example="1-DEC4410")],
        op_overrides={"get": {"status": "", "request_entity_id": "", "response_entity_id": "", "summary": "",
                              "description": "One agreement version with both parties' declarations."}})

    declaration = _entity(
        "en_sc_decl", ns_id, src, "Agreement Declaration", "agreementDeclaration", "agreement-declarations",
        "AgreementDeclaration",
        "One party's account of the agreement: the hours they say the customer needs, what that is based on, "
        "and their answers. Held on the party relationship while the case is set up, and on the service "
        "request for a change.",
        "Party Relationship (set-up) or Service Request (change)", [],
        [_attr("at_scd_role", "Party Role", "partyRole", "enum", required=True, enum=PARTY_ROLE, example="ACCOUNT_HOLDER"),
         _attr("at_scd_party", "Party Id", "partyId", read_only=True, system=True, example="1-7ABC12",
               description="The customer (contact), set from the case and the role."),
         _attr("at_scd_hours", "Hours Per Month", "hoursPerMonth", "integer", required=True, example="24"),
         _attr("at_scd_basis", "Basis", "basis", "enum", required=True, enum=BASIS, example="AGREEMENT"),
         _attr("at_scd_evid", "Evidence Reference", "evidenceReference", max_length=100,
               example="Contract 2025/SUP/0193",
               description="Contract, order or other evidence the party relies on."),
         _attr("at_scd_at", "Submitted At", "submittedAt", "date-time", read_only=True, system=True,
               example="2026-10-20T10:15:00Z")])

    answer = _entity(
        "en_sc_ans", ns_id, src, "Agreement Answer", "agreementAnswer", "agreement-answers", "AgreementAnswer",
        "A party's answer to one agreement question.", "Service Request (questions and answers)", [],
        [_attr("at_sca_q", "Question Code", "questionCode", required=True, example="AG_USAGE",
               description="Code from the agreement question set."),
         _attr("at_sca_a", "Answer", "answer", required=True, max_length=2000,
               example="Two support calls a week and one site visit a month")])

    change = _entity(
        "en_sc_chg", ns_id, src, "Agreement Change", "agreementChange", "agreement-changes", "AgreementChange",
        "A request to change the service agreement for a product. Siebel holds it as a service request with both "
        "parties' details and answers. Once decided, the outcome is applied to the case's agreements as a new "
        "agreement version.",
        "Service Request (agreement change)", ["get", "update"],
        [_attr("at_scc_id", "Change Id", "changeId", key=True, read_only=True, system=True, source_field="Id",
               example="1-SR9932", description="The service request id."),
         _attr("at_scc_prod", "Product Id", "productId", required=True, example="1-PRD771"),
         _attr("at_scc_by", "Requested By", "requestedBy", "enum", required=True,
               enum=_enum("ACCOUNT_HOLDER|Account holder", "REQUESTER|Requester",
                          "AGENT|Agent initiated"), example="ACCOUNT_HOLDER"),
         _attr("at_scc_rep", "Reported Date", "reportedDate", "date", required=True, example="2026-10-20",
               description="Date the change was reported; usually sets the effective date."),
         _attr("at_scc_eff", "Proposed Effective Date", "proposedEffectiveDate", "date", example="2026-10-20"),
         _attr("at_scc_reason", "Reason", "reason", max_length=500, example="Customer now needs weekend support"),
         _attr("at_scc_status", "Change Status", "status", "enum", read_only=True, system=True, filterable=True,
               enum=_enum("DRAFT|Draft", "SUBMITTED|Submitted", "AWAITING_OTHER_PARTY|Awaiting the other party",
                          "AGREED|Both parties agree", "DISPUTED|Parties disagree", "DECIDED|Decided",
                          "APPLIED|Applied to the agreement", "WITHDRAWN|Withdrawn"),
               example="AWAITING_OTHER_PARTY")],
        op_overrides={"update": {"status": "", "request_entity_id": "", "response_entity_id": "", "summary": "",
                                 "description": "Replaces the change details while it is open, including both "
                                                "declarations if sent. To record one party's details, use "
                                                "PUT .../declarations/{partyRole}. Returns 409 once the change is "
                                                "decided or withdrawn."}},
        actions=[
            {"id": "ac_sc_decision", "name": "decision", "method": "POST", "scope": "item", "path": "decision",
             "summary": "Record the decision on an agreement change",
             "description": "Records the outcome. When the outcome is CHANGED, the decided hours, basis and "
                            "effective date are applied to the case's agreements as a new agreement version "
                            "(see resultingAgreement) and the change moves to APPLIED. Returns 409 if the change "
                            "is already decided or withdrawn.",
             "request_entity_id": "en_sc_out", "request_variant": "create", "response_entity_id": "self",
             "status": "200", "if_match": True, "errors": ["NotFound", "Conflict", "ValidationFailed"]},
            {"id": "ac_sc_withdraw", "name": "withdrawal", "method": "POST", "scope": "item", "path": "withdrawal",
             "summary": "Withdraw an agreement change",
             "description": "Withdraws an open change. Returns 409 once it is decided.",
             "request_entity_id": "", "request_variant": "create", "response_entity_id": "self",
             "status": "200", "if_match": True, "errors": ["NotFound", "Conflict"]},
        ])

    decision = _entity(
        "en_sc_out", ns_id, src, "Agreement Decision", "agreementDecision", "agreement-decisions",
        "AgreementDecision",
        "The outcome of an agreement change: what was decided, from when, and why.",
        "Service Request (outcome)", [],
        [_attr("at_sco_out", "Outcome", "outcome", "enum", required=True,
               enum=_enum("CHANGED|Agreement changed", "NOT_CHANGED|No change to the agreement"), example="CHANGED"),
         _attr("at_sco_hours", "Decided Hours Per Month", "hoursPerMonth", "integer", example="40",
               description="Required when the outcome is CHANGED."),
         _attr("at_sco_band", "Agreement Band", "band", "enum", read_only=True, system=True, enum=BAND,
               example="GOLD"),
         _attr("at_sco_basis", "Basis", "basis", "enum", enum=BASIS, example="AGREEMENT",
               description="Required when the outcome is CHANGED."),
         _attr("at_sco_eff", "Effective Date", "effectiveDate", "date", example="2026-10-20",
               description="Required when the outcome is CHANGED."),
         _attr("at_sco_reasons", "Reasons", "reasons", required=True, max_length=2000,
               example="Both parties confirm the customer needs about forty hours a month."),
         _attr("at_sco_id", "Decision Id", "decisionId", read_only=True, system=True, example="1-DEC4471"),
         _attr("at_sco_at", "Decided At", "decidedAt", "date-time", read_only=True, system=True,
               example="2026-10-27T14:02:00Z"),
         _attr("at_sco_by", "Decided By", "decidedBy", read_only=True, system=True, example="Agent 4471")])

    question = _entity(
        "en_sc_q", ns_id, src, "Agreement Question", "agreementQuestion", "agreement-questions",
        "AgreementQuestion",
        "A question in the agreement question set that each party answers on a change.",
        "Service Request (question set)", ["list"],
        [_attr("at_scq_code", "Question Code", "questionCode", key=True, read_only=True, example="AG_USAGE"),
         _attr("at_scq_text", "Question", "text", required=True, example="Describe how the customer uses support today."),
         _attr("at_scq_type", "Answer Type", "answerType", "enum", required=True,
               enum=_enum("YES_NO|Yes or no", "NUMBER|Number", "DATE|Date", "TEXT|Text", "CHOICE|One of the options"),
               example="TEXT"),
         _attr("at_scq_opts", "Options", "options", example="",
               description="Allowed answers for CHOICE questions, separated by |."),
         _attr("at_scq_for", "Applies To", "appliesTo", "enum", filterable=True,
               enum=_enum("ACCOUNT_HOLDER|Account holder", "REQUESTER|Requester", "BOTH|Both parties"),
               example="BOTH"),
         _attr("at_scq_ver", "Question Set Version", "questionSetVersion", filterable=True, example="2026.1"),
         _attr("at_scq_order", "Display Order", "displayOrder", "integer", example="1")])

    no_ov = {"status": "", "request_entity_id": "", "response_entity_id": "", "summary": ""}
    rels = [
        _rel("rl_sc_case_arr", "en_sc_case", "en_sc_arr", "one-to-many", "Agreements", "agreements", "none",
             inverse_business_name="Case", inverse_api_name="case", inverse_expose="link",
             sub_ops=["list", "create"],
             sub_overrides={
                 "list": dict(no_ov, description="Agreements for each product on the case. Versions "
                                                 "replaced by a change are included with status ENDED."),
                 "create": dict(no_ov, summary="Record an agreement while the case is set up",
                                description="Records an agreement for a product while the case is being set up, with "
                                            "both parties' declarations. Once the case has passed the set-up "
                                            "stage this returns 409 Conflict: report changes through "
                                            "/cases/{caseId}/agreement-changes instead. Returns the row id of the "
                                            "agreement created.")},
             description="Agreements are held on the case's party relationship records."),
        _rel("rl_sc_arr_decl", "en_sc_arr", "en_sc_decl", "one-to-many", "Declarations", "declarations", "embedded",
             nested_create=True, description="Each party's declaration made while the case was set up."),
        _rel("rl_sc_decl_ans", "en_sc_decl", "en_sc_ans", "one-to-many", "Answers", "answers", "embedded",
             nested_create=True),
        _rel("rl_sc_case_chg", "en_sc_case", "en_sc_chg", "one-to-many", "Agreement changes", "agreementChanges",
             "none", inverse_business_name="Case", inverse_api_name="case", inverse_expose="link",
             sub_ops=["list", "create"],
             sub_overrides={
                 "list": dict(no_ov, description="Changes to agreements on the case, newest first."),
                 "create": dict(no_ov, summary="Report a change to an agreement",
                                description="Starts a change: Siebel creates the service request with the "
                                            "reporting party's details and answers, and the other party is asked "
                                            "for theirs. Returns the row id of the change (the service request id).")},
             description="Changes are captured on Siebel service requests."),
        _rel("rl_sc_chg_decl", "en_sc_chg", "en_sc_decl", "one-to-many", "Declarations", "declarations", "embedded",
             nested_create=True, sub_ops=["get", "update"], sub_key="at_scd_role",
             sub_overrides={
                 "update": dict(no_ov, summary="Record one party's details and answers on a change",
                                description="Records or replaces one party's details and answers while the change "
                                            "is open. Each party has their own path, so their submissions never "
                                            "overwrite each other. Returns the declaration; 409 once the change is "
                                            "decided or withdrawn.")}),
        _rel("rl_sc_chg_out", "en_sc_chg", "en_sc_out", "one-to-one", "Outcome", "outcome", "embedded",
             description="Set by the decision action."),
        _rel("rl_sc_chg_arr", "en_sc_chg", "en_sc_arr", "one-to-one", "Resulting agreement", "resultingAgreement",
             "link", read_only=True, inverse_business_name="Change", inverse_api_name="change", inverse_expose="link",
             description="The agreement version a decided change produced."),
    ]
    case["tags"] = ["Case", "Agreements"]
    for e in (agreement, declaration, answer, question):
        e["tags"] = ["Agreements"]
    for e in (change, decision):
        e["tags"] = ["Agreements", "Service request"]
    frag = {"namespaces": namespaces, "source_systems": src_systems,
            "entities": [case, agreement, declaration, answer, change, decision, question],
            "relationships": rels}
    return frag, notes


def add_example(model: Dict[str, Any], name: str) -> Tuple[Dict[str, Any], List[str]]:
    if name != "agreements":
        raise KeyError(name)
    frag, notes = service_agreements(model)
    base = dict(model, source_systems=list(model.get("source_systems", [])) + frag["source_systems"])
    tags = {e["schema_name"].lower(): e["tags"] for e in frag["entities"]}
    merged, merge_notes = merge(base, frag, "merge", "link")
    for e in merged["entities"]:   # entities already there (e.g. Case) get the example's tags too
        if e["schema_name"].lower() in tags:
            e["tags"] = clean_tags((e.get("tags") or []) + tags[e["schema_name"].lower()])
    # a re-run should not duplicate anything: everything already present is linked
    return fill_defaults(merged), notes + [n.replace("the imported columns for it were not added",
                                                      "kept its columns") for n in merge_notes]
