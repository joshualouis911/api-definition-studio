import io
import json
import zipfile

import pytest
import yaml
from fastapi.testclient import TestClient
from openapi_spec_validator import validate as validate_spec
from openpyxl import load_workbook

from api_definition_studio import main
from api_definition_studio.diagram import drawio, mermaid
from api_definition_studio.mapping_sheet import export_xlsx, import_xlsx
from api_definition_studio.openapi_gen import build_openapi


@pytest.fixture()
def client(tmp_path):
    main.reset_store(str(tmp_path))
    with TestClient(main.app) as c:
        yield c


def model(c):
    return c.get("/api/model").json()["model"]


def spec(c, ns="cm"):
    return c.get("/api/openapi/" + ns + ".json").json()


# -- starter example -------------------------------------------------------------

def test_sample_is_clean_and_valid_openapi(client):
    d = client.get("/api/model").json()
    assert d["issues"] == []
    doc = spec(client)
    validate_spec(doc)
    assert doc["openapi"] == "3.0.3"
    assert doc["info"]["x-namespace"] == "cm"


def test_contact_is_exposed_as_customer(client):
    doc = spec(client)
    assert "/cm/v1/customers" in doc["paths"]
    assert "/cm/v1/customers/{customerId}" in doc["paths"]
    cust = doc["components"]["schemas"]["Customer"]
    assert cust["title"] == "Contact"
    assert cust["x-business-name"] == "Contact"
    assert cust["x-source-object"] == "Contact"
    fn = cust["properties"]["firstName"]
    assert fn["title"] == "First Name" and fn["x-source-field"] == "First Name" and fn["maxLength"] == 50
    assert fn["x-source-column"] == "S_CONTACT.FST_NAME" and fn["x-pii"] is True
    assert not any("contact" in p for p in doc["paths"])


def test_links_embedded_and_subresources(client):
    doc = spec(client)
    case = doc["components"]["schemas"]["Case"]
    assert case["properties"]["accountHolder"]["allOf"][0]["$ref"].endswith("/ResourceLink")
    assert case["example"]["accountHolder"]["href"].startswith("/cm/v1/customers/")
    addr = doc["components"]["schemas"]["Customer"]["properties"]["addresses"]
    assert addr["type"] == "array" and addr["items"]["$ref"].endswith("/Address")
    sub = doc["paths"]["/cm/v1/customers/{customerId}/cases-as-account-holder"]["get"]
    assert sub["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/CasePage")
    # Address has no operations: schema only
    assert not any("addresses" in p for p in doc["paths"])
    # create accepts a reference to the account holder, not the full object
    assert doc["components"]["schemas"]["CaseCreate"]["properties"]["accountHolder"]["allOf"][0]["$ref"].endswith("/ResourceRef")


def test_enum_and_filters(client):
    doc = spec(client)
    st = doc["components"]["schemas"]["Case"]["properties"]["status"]
    assert st["enum"] == ["ACTIVE", "SUSPENDED", "CLOSED"]
    assert st["x-source-lov-type"] == "CASE_STATUS"
    params = {p["name"] for p in doc["paths"]["/cm/v1/cases"]["get"]["parameters"] if "name" in p}
    assert {"caseNumber", "status", "limit", "cursor"} <= params


# -- editing ----------------------------------------------------------------------

def test_add_column_regenerates_definition(client):
    m = model(client)
    r = client.post("/api/entities/en_contact/attributes",
                    json={"business_name": "Tax Reference Number", "type": "string", "pii": True,
                          "source_field": "Tax Reference", "max_length": 9})
    assert r.status_code == 201
    a = r.json()
    assert a["api_name"] == "taxReferenceNumber"  # derived from the business name
    props = spec(client)["components"]["schemas"]["Customer"]["properties"]
    assert props["taxReferenceNumber"]["x-source-field"] == "Tax Reference"
    assert len(model(client)["entities"][0]["attributes"]) == len(m["entities"][0]["attributes"]) + 1


def test_add_column_after_and_reorder(client):
    a = client.post("/api/entities/en_case/attributes?after=at_k_num", json={"business_name": "Case Type"}).json()
    ids = [x["id"] for x in model(client)["entities"][1]["attributes"]]
    assert ids[2] == a["id"]
    rev = list(reversed(ids))
    r = client.post("/api/entities/en_case/attributes/order", json={"ids": rev})
    assert r.status_code == 200
    assert [x["id"] for x in model(client)["entities"][1]["attributes"]] == rev
    assert client.post("/api/entities/en_case/attributes/order", json={"ids": rev[:-1]}).status_code == 422


def test_rename_and_clash_rules(client):
    ok = client.patch("/api/attributes/at_c_fn", json={"api_name": "givenName"})
    assert ok.status_code == 200
    assert "givenName" in spec(client)["components"]["schemas"]["Customer"]["properties"]
    clash = client.patch("/api/attributes/at_c_fn", json={"api_name": "lastName"})
    assert clash.status_code == 422 and "clashes" in clash.json()["detail"]["message"]
    bad = client.patch("/api/attributes/at_c_fn", json={"api_name": "First Name"})
    assert bad.status_code == 422
    # clash with a link name
    clash2 = client.patch("/api/attributes/at_k_num", json={"api_name": "accountHolder"})
    assert clash2.status_code == 422
    # nothing was saved by refused edits
    names = [a["api_name"] for a in model(client)["entities"][0]["attributes"]]
    assert "givenName" in names and "First Name" not in names


def test_system_column_not_in_post_or_put(client):
    # sample: Case Number is a system column (not read only), Last Updated is system + read only
    doc = spec(client)
    sch = doc["components"]["schemas"]
    assert sch["Case"]["properties"]["caseNumber"]["x-system-column"] is True
    assert "caseNumber" not in sch["CaseCreate"]["properties"]          # POST and PUT body
    assert "caseNumber" not in sch["CaseCreate"].get("required", [])
    assert "caseNumber" in sch["CaseUpdate"]["properties"]              # PATCH body
    assert "updatedAt" not in sch["CaseUpdate"]["properties"]           # read only wins
    # PUT uses the same body as POST
    client.patch("/api/entities/en_case", json={"operations": ["list", "get", "create", "update", "patch"]})
    doc = spec(client)
    put = doc["paths"]["/cm/v1/cases/{caseId}"]["put"]["requestBody"]["content"]["application/json"]["schema"]
    assert put["$ref"].endswith("/CaseCreate")
    # flag a column as system and it leaves the create body
    client.patch("/api/attributes/at_c_num", json={"system": True})
    pc = spec(client)["components"]["schemas"]["CustomerCreate"]
    assert "customerNumber" not in pc["properties"] and "customerNumber" not in pc["required"]
    validate_spec(spec(client))


def test_dd_reference(client):
    r = client.patch("/api/attributes/at_c_fn", json={"dd_reference": "DD-PER-FN-01"})
    assert r.json()["dd_reference"] == "DD-PER-FN-01"
    p = spec(client)["components"]["schemas"]["Customer"]["properties"]["firstName"]
    assert p["x-dd-attribute"] == "DD-PER-FN-01"
    csv = client.get("/api/export/mapping.csv").text
    assert "DD attribute reference" in csv.splitlines()[0] and "DD-PER-FN-01" in csv


def test_old_model_without_new_fields_loads(client, tmp_path):
    m = client.get("/api/model/download").json()
    for e in m["entities"]:
        for a in e["attributes"]:
            a.pop("system"); a.pop("dd_reference")
    assert client.put("/api/model", json=m).status_code == 200
    a = model(client)["entities"][0]["attributes"][0]
    assert a["system"] is False and a["dd_reference"] == ""


def test_key_is_single(client):
    client.patch("/api/attributes/at_c_num", json={"key": True})
    attrs = model(client)["entities"][0]["attributes"]
    assert [a["api_name"] for a in attrs if a["key"]] == ["customerNumber"]
    assert "/cm/v1/customers/{customerNumber}" in spec(client)["paths"]


def test_enum_values_edit(client):
    r = client.patch("/api/attributes/at_k_st", json={"enum": [
        {"code": "ACTIVE", "label": "Active", "source_value": "Active"},
        {"code": "", "label": "Pending closure", "source_value": "Pending Closure"}]})
    assert r.status_code == 200
    assert r.json()["enum"][1]["code"] == "PENDING_CLOSURE"
    st = spec(client)["components"]["schemas"]["Case"]["properties"]["status"]
    assert st["x-source-values"]["PENDING_CLOSURE"] == "Pending Closure"


def test_new_entity_with_key_and_link(client):
    e = client.post("/api/entities", json={"namespace_id": "ns_cm", "business_name": "Service Request",
                                           "api_name": "serviceRequest", "source_object": "Service Request"}).json()
    assert e["plural"] == "service-requests" and e["schema_name"] == "ServiceRequest"
    assert e["attributes"][0]["api_name"] == "serviceRequestId" and e["attributes"][0]["key"]
    r = client.post("/api/relationships", json={
        "from_entity_id": "en_case", "to_entity_id": e["id"], "cardinality": "one-to-many",
        "business_name": "Service requests", "api_name": "serviceRequests", "expose": "subresource",
        "inverse_business_name": "Case", "inverse_api_name": "case", "inverse_expose": "link"})
    assert r.status_code == 201
    doc = spec(client)
    validate_spec(doc)
    assert "/cm/v1/cases/{caseId}/service-requests" in doc["paths"]
    assert doc["components"]["schemas"]["ServiceRequest"]["properties"]["case"]["allOf"][0]["$ref"].endswith("ResourceLink")
    # duplicate link name on one entity is refused
    dup = client.post("/api/relationships", json={"from_entity_id": "en_case", "to_entity_id": e["id"],
                                                  "api_name": "serviceRequests", "expose": "link"})
    assert dup.status_code == 422


def test_delete_entity_removes_links(client):
    assert client.delete("/api/entities/en_address").status_code == 204
    m = model(client)
    assert all(r["to_entity_id"] != "en_address" for r in m["relationships"])
    validate_spec(spec(client))


def test_entity_without_key_is_reported_not_blocked(client):
    e = client.post("/api/entities?key_column=false", json={"namespace_id": "ns_cm", "business_name": "Note"})
    assert e.status_code == 201
    issues = client.get("/api/validate").json()
    assert any("key column" in i["message"] for i in issues if i["object_id"] == e.json()["id"])


# -- namespaces ----------------------------------------------------------------

def test_namespace_config(client):
    n = client.post("/api/namespaces", json={"key": "pay", "title": "Payments API", "base_path": "/payments/v2",
                                              "version": "2.1.0", "security": "oauth2",
                                              "servers": [{"url": "https://api.test", "description": "Test"}]}).json()
    client.patch("/api/entities/en_case", json={"namespace_id": n["id"]})
    pay = spec(client, "pay")
    validate_spec(pay)
    assert pay["info"]["version"] == "2.1.0"
    assert "/payments/v2/cases" in pay["paths"]
    assert pay["components"]["securitySchemes"]["oauth2"]["type"] == "oauth2"
    cm = spec(client, "cm")
    assert not any("/cases" in p and "customers" not in p for p in cm["paths"])
    # cross-namespace sub-resource still resolves: Case schema is included in cm
    assert "Case" in cm["components"]["schemas"]
    assert "/cm/v1/customers/{customerId}/cases-as-account-holder" in cm["paths"]
    # base path in the server URL instead of the paths
    client.patch("/api/namespaces/" + n["id"], json={"base_path_in": "server"})
    pay = spec(client, "pay")
    assert "/cases" in pay["paths"] and pay["servers"][0]["url"] == "https://api.test/payments/v2"
    # invalid and duplicate namespace settings are refused
    assert client.patch("/api/namespaces/" + n["id"], json={"base_path": "payments v2"}).status_code == 422
    assert client.patch("/api/namespaces/" + n["id"], json={"key": "cm"}).status_code == 422
    assert client.delete("/api/namespaces/" + n["id"]).status_code == 409


def test_yaml_output(client):
    r = client.get("/api/openapi/cm.yaml")
    assert r.status_code == 200
    doc = yaml.safe_load(r.text)
    validate_spec(doc)


# -- mapping sheet --------------------------------------------------------------

def test_mapping_xlsx_round_trip(client):
    x = client.get("/api/export/mapping.xlsx")
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Read me", "Headers", "Source systems", "Namespaces", "Entities", "Mapping", "Linked entities",
                             "Enum values", "Operations", "Endpoints"]
    ws = wb["Mapping"]
    headers = [c.value for c in ws[1]]
    row = next(r for r in ws.iter_rows(min_row=2, values_only=True) if r[headers.index("API name")] == "firstName")
    assert row[headers.index("Business name")] == "First Name"
    assert row[headers.index("Source field")] == "First Name"
    assert row[headers.index("JSON path")] == "customer.firstName"
    assert row[headers.index("DD attribute reference")] == "DD-PER-003"
    num = next(r for r in ws.iter_rows(min_row=2, values_only=True) if r[headers.index("API name")] == "caseNumber")
    assert num[headers.index("System")] == "Y" and num[headers.index("Read only")] in (None, "")
    addr = next(r for r in ws.iter_rows(min_row=2, values_only=True) if r[headers.index("API name")] == "postcode")
    assert addr[headers.index("JSON path")] == "customer.addresses[].postcode"

    before = spec(client)
    prev = client.post("/api/import/mapping", content=x.content)
    assert prev.status_code == 200 and prev.json()["applied"] is False
    assert prev.json()["summary"]["links"] == 4
    done = client.post("/api/import/mapping?apply=true", content=x.content)
    assert done.json()["applied"] is True and done.json()["backup"].endswith(".bak.json")
    after = spec(client)
    for d in (before, after):
        d["info"].pop("x-model-fingerprint")
    assert before == after


def test_mapping_import_applies_excel_edits(client, tmp_path):
    wb = load_workbook(io.BytesIO(client.get("/api/export/mapping.xlsx").content))
    ws = wb["Mapping"]
    headers = [c.value for c in ws[1]]
    # add a new column row for the Customer schema
    new = [None] * len(headers)
    new[headers.index("Schema name")] = "Customer"
    new[headers.index("Business name")] = "Mobile Number"
    new[headers.index("Type")] = "string"
    new[headers.index("PII")] = "Y"
    new[headers.index("Source field")] = "Cellular Phone #"
    ws.append(new)
    # rename an entity's API name
    es = wb["Entities"]
    eh = [c.value for c in es[1]]
    for row in es.iter_rows(min_row=2):
        if row[eh.index("Business name")].value == "Case":
            row[eh.index("API name")].value = "supportCase"
            row[eh.index("Path segment")].value = "support-cases"
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/import/mapping?apply=true", content=buf.getvalue())
    assert r.status_code == 200, r.text
    doc = spec(client)
    assert doc["components"]["schemas"]["Customer"]["properties"]["mobileNumber"]["x-source-field"] == "Cellular Phone #"
    assert "/cm/v1/support-cases" in doc["paths"]


def test_mapping_import_rejects_bad_workbook(client):
    assert client.post("/api/import/mapping", content=b"not excel").status_code == 422
    wb = load_workbook(io.BytesIO(client.get("/api/export/mapping.xlsx").content))
    ws = wb["Mapping"]
    headers = [c.value for c in ws[1]]
    row = [None] * len(headers)
    row[headers.index("Schema name")] = "Nope"
    row[headers.index("Business name")] = "X"
    ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/import/mapping", content=buf.getvalue())
    assert r.status_code == 422 and "unknown schema" in r.json()["detail"]


def test_csv(client):
    lines = client.get("/api/export/mapping.csv").text.splitlines()
    assert lines[0].startswith("Namespace,Entity,Schema name,JSON path,Business name,API name")
    assert any(",customerNumber," in line and "Customer Number" in line for line in lines)


# -- diagram ---------------------------------------------------------------------

def test_mermaid_uses_business_names(client):
    t = client.get("/api/export/diagram.mmd").text
    assert t.startswith("erDiagram")
    assert 'Customer["Contact"]' in t
    assert 'Case }o--|| Customer : "Account holder"' in t
    assert 'Customer ||--o{ Address : "Addresses"' in t
    assert 'string firstName "First Name"' in t
    api = client.get("/api/export/diagram.mmd?labels=api").text
    assert 'Customer["customer"]' in api and ': "accountHolder"' in api


def test_drawio_is_xml_with_business_names(client):
    import xml.etree.ElementTree as ET
    t = client.get("/api/export/diagram.drawio").text
    root = ET.fromstring(t)
    values = [c.get("value") for c in root.iter("mxCell") if c.get("value")]
    assert "Contact\nAPI: customer\nSource: Siebel CRM" in values
    assert "Payment\nSource: Payments Service" in values
    assert "Account holder" in values
    edges = [c for c in root.iter("mxCell") if c.get("edge") == "1"]
    assert len(edges) == 4 and "ERzeroToMany" in edges[0].get("style")


def test_positions_saved(client):
    client.post("/api/positions", json={"en_case": {"x": 700, "y": 90}})
    e = [x for x in model(client)["entities"] if x["id"] == "en_case"][0]
    assert (e["x"], e["y"]) == (700, 90)


# -- model file & bundle ------------------------------------------------------------

def test_model_json_round_trip_and_reset(client):
    m = client.get("/api/model/download").json()
    client.delete("/api/entities/en_address")
    assert client.put("/api/model", json=m).status_code == 200
    assert len(model(client)["entities"]) == 4
    assert client.put("/api/model", json={"namespaces": "x"}).status_code == 422
    client.delete("/api/entities/en_case")
    client.post("/api/reset-sample")
    assert len(model(client)["entities"]) == 4


def test_bundle(client):
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/export/bundle.zip").content))
    names = set(z.namelist())
    assert {"openapi/cm-openapi.yaml", "openapi/cm-openapi.json", "mapping/mapping.xlsx", "mapping/mapping.csv",
            "diagram/data-model.mmd", "diagram/data-model.drawio", "model.json", "validation.txt"} <= names
    validate_spec(json.loads(z.read("openapi/cm-openapi.json")))


def test_ui_served(client):
    assert "API Definition Studio" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert "swagger-ui" in client.get("/preview/cm").text


def test_cli_generate(tmp_path):
    from api_definition_studio.cli import main as cli
    out = tmp_path / "out"
    assert cli(["generate", "--data", str(tmp_path / "data"), "--out", str(out), "--strict"]) == 0
    assert (out / "openapi" / "cm-openapi.yaml").exists()
    assert (out / "mapping" / "mapping.xlsx").exists()


# -- source systems (non-Siebel applications) ------------------------------------

def test_sample_has_siebel_and_non_siebel_sources(client):
    doc = spec(client)
    validate_spec(doc)
    sch = doc["components"]["schemas"]
    assert doc["info"]["x-source-systems"] == ["Siebel CRM", "Payments Service"]
    # Siebel keeps its own extension names
    assert sch["Customer"]["x-source-system"] == "Siebel CRM"
    assert sch["Customer"]["x-source-integration-object"] == "Contact IO"
    assert sch["Customer"]["properties"]["firstName"]["x-source-column"] == "S_CONTACT.FST_NAME"
    assert sch["Case"]["properties"]["status"]["x-source-lov-type"] == "CASE_STATUS"
    # a REST source uses neutral names
    pay = sch["Payment"]
    assert pay["x-source-system"] == "Payments Service" and pay["x-source-system-type"] == "rest"
    assert pay["x-source-container"] == "Payments API v2"
    assert pay["properties"]["paymentId"]["x-source-location"] == "GET /v2/payments/{id}"
    assert pay["properties"]["direction"]["x-source-code-list"] == "PaymentDirection"
    assert pay["properties"]["amount"]["x-source-field"] == "$.amountInPence"
    assert not any(k.startswith("x-source-lov") or k == "x-source-column"
                   for p in pay["properties"].values() for k in p)
    assert "/cm/v1/cases/{caseId}/payments" in doc["paths"]


def test_siebel_name_checks_only_apply_to_siebel(client):
    # 'attribValue' is a Siebel-style internal name: flagged on a Siebel entity only
    client.post("/api/entities/en_case/attributes", json={"business_name": "Attrib value", "source_field": "X"})
    client.post("/api/entities/en_payment/attributes", json={"business_name": "Attrib value", "source_field": "X"})
    issues = client.get("/api/validate").json()
    leak = [i for i in issues if "internal name" in i["message"]]
    assert len(leak) == 1 and leak[0]["where"].startswith("Case")
    # a non-Siebel system can have its own internal-name tokens
    client.patch("/api/source-systems/ss_payments", json={"internal_tokens": ["payref"]})
    client.patch("/api/attributes/at_p_ref", json={"api_name": "payRef"})
    issues = client.get("/api/validate").json()
    assert any("payRef" in i["message"] and "Payments Service" in i["message"] for i in issues)


def test_unsourced_entity_needs_no_source_mapping(client):
    e = client.post("/api/entities", json={"namespace_id": "ns_cm", "business_name": "Calculation Result",
                                           "source_system_id": ""}).json()
    client.post("/api/entities/" + e["id"] + "/attributes", json={"business_name": "Weekly Amount", "type": "number"})
    issues = [i for i in client.get("/api/validate").json() if i["object_id"] in
              [e["id"]] + [a["id"] for a in model(client)["entities"][-1]["attributes"]]]
    assert issues == []
    doc = spec(client)
    assert "x-source-system" not in doc["components"]["schemas"]["CalculationResult"]
    # the mapping sheet shows no source system for it
    rows = client.get("/api/mapping").json()["columns"]
    assert all(r["source_system"] == "" for r in rows if r["entity"] == "Calculation Result")


def test_source_system_crud(client):
    r = client.post("/api/source-systems", json={"key": "docs", "name": "Document store", "kind": "database"})
    assert r.status_code == 201
    ss = r.json()
    assert ss["internal_tokens"] == []
    assert client.post("/api/source-systems", json={"key": "docs", "kind": "file"}).status_code == 422
    assert client.post("/api/source-systems", json={"key": "Bad Key"}).status_code == 422
    assert client.patch("/api/source-systems/" + ss["id"], json={"kind": "nope"}).status_code == 422
    # switching to Siebel picks up the Siebel internal-name tokens
    assert "attrib" in client.patch("/api/source-systems/" + ss["id"], json={"kind": "siebel"}).json()["internal_tokens"]
    # move Payment to the new system; unknown ids are refused
    assert client.patch("/api/entities/en_payment", json={"source_system_id": "ss_nope"}).status_code == 404
    assert client.patch("/api/entities/en_payment", json={"source_system_id": ss["id"]}).status_code == 200
    # in use: refused unless detached
    assert client.delete("/api/source-systems/" + ss["id"]).status_code == 409
    assert client.delete("/api/source-systems/" + ss["id"] + "?detach=true").status_code == 204
    pay = [e for e in model(client)["entities"] if e["id"] == "en_payment"][0]
    assert pay["source_system_id"] == ""


def test_1x_model_is_migrated_to_siebel(client):
    old = client.get("/api/model/download").json()
    old["schema_version"] = 1
    old["settings"]["source_system"] = "Siebel CRM"
    old.pop("source_systems")
    for e in old["entities"]:
        e.pop("source_system_id")
    assert client.put("/api/model", json=old).status_code == 200
    m = model(client)
    assert m["schema_version"] == 4
    assert [(x["key"], x["kind"]) for x in m["source_systems"]] == [("siebel-crm", "siebel")]
    assert {e["source_system_id"] for e in m["entities"]} == {m["source_systems"][0]["id"]}
    validate_spec(spec(client))


def test_1x_workbook_imports_as_siebel(client):
    wb = load_workbook(io.BytesIO(client.get("/api/export/mapping.xlsx").content))
    del wb["Source systems"]
    # put the 1.x headers back and drop the new Entities column
    for sheet, old in (("Entities", {"Source container": "Integration object"}),
                       ("Mapping", {"Source location": "Source column", "Code list / LOV type": "LOV type"})):
        for c in wb[sheet][1]:
            if c.value in old:
                c.value = old[c.value]
    wb["Entities"].delete_cols([c.value for c in wb["Entities"][1]].index("Source system") + 1)
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/import/mapping?apply=true", content=buf.getvalue())
    assert r.status_code == 200, r.text
    assert any("1.x workbook" in n for n in r.json()["notes"])
    m = model(client)
    assert [x["kind"] for x in m["source_systems"]] == ["siebel"]
    first = m["entities"][0]["attributes"][2]
    assert first["source_column"] == "S_CONTACT.FST_NAME" and m["entities"][0]["source_io"] == "Contact IO"
    assert first["dd_reference"] == "DD-PER-003"
    # a 1.0 workbook has no DD reference or System column: they come back blank, not "None"
    wb = load_workbook(io.BytesIO(client.get("/api/export/mapping.xlsx").content))
    ws = wb["Mapping"]
    for name in ("DD attribute reference", "System"):
        ws.delete_cols([c.value for c in ws[1]].index(name) + 1)
    buf = io.BytesIO()
    wb.save(buf)
    assert client.post("/api/import/mapping?apply=true", content=buf.getvalue()).status_code == 200
    first = model(client)["entities"][0]["attributes"][2]
    assert first["dd_reference"] == "" and first["system"] is False


def test_workbook_source_system_errors(client):
    wb = load_workbook(io.BytesIO(client.get("/api/export/mapping.xlsx").content))
    ws = wb["Entities"]
    col = [c.value for c in ws[1]].index("Source system")
    ws.cell(row=2, column=col + 1, value="nowhere")
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/import/mapping", content=buf.getvalue())
    assert r.status_code == 422 and "unknown source system 'nowhere'" in r.json()["detail"]


def test_ui_gets_source_kind_labels(client):
    kinds = client.get("/api/model").json()["source_kinds"]
    assert kinds["siebel"]["container"] == "Integration object"
    assert kinds["rest"]["field"] == "JSON path"
    assert set(kinds) == {"siebel", "rest", "database", "file", "mainframe", "other"}


# -- upgrading a data folder saved by an older version --------------------------

import shutil
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("fixture", ["model-1.0.json", "model-1.1.json"])
def test_existing_data_folder_from_older_version(tmp_path, fixture):
    # a model.json exactly as an older version saved it, opened by the new studio
    shutil.copyfile(FIXTURES / fixture, tmp_path / "model.json")
    original = json.loads((tmp_path / "model.json").read_text())
    main.reset_store(str(tmp_path))
    with TestClient(main.app) as c:
        d = c.get("/api/model").json()
        m = d["model"]
        assert m["schema_version"] == 4
        assert [(s["name"], s["kind"]) for s in m["source_systems"]] == [("Siebel CRM", "siebel")]
        assert [h["name"] for h in m["headers"]] == ["request-id", "date-time", "correlation-id",
                                                     "journey-instance-id", "instigating-system"]
        assert all(e["source_system_id"] == m["source_systems"][0]["id"] for e in m["entities"])
        assert len(m["entities"]) == len(original["entities"])
        assert all("system" in a and "dd_reference" in a for e in m["entities"] for a in e["attributes"])
        assert not [i for i in d["issues"] if i["level"] == "error"]
        validate_spec(c.get("/api/openapi/cm.json").json())
        # the upgrade was written back once, with the original kept as a backup
        backups = list(tmp_path.glob("model-*-before-upgrade.bak.json"))
        assert len(backups) == 1 and json.loads(backups[0].read_text()) == original
        assert json.loads((tmp_path / "model.json").read_text())["schema_version"] == 4
        c.get("/api/model")
        assert len(list(tmp_path.glob("*.bak.json"))) == 1
        # and the studio works normally on it
        assert c.post("/api/source-systems", json={"key": "payments", "kind": "rest"}).status_code == 201
        assert c.patch("/api/entities/" + m["entities"][0]["id"], json={"source_system_id": ""}).status_code == 200


def test_page_asset_urls_are_versioned(client):
    from api_definition_studio import __version__
    html = client.get("/").text
    assert 'static/app.js?v=' + __version__ in html and 'static/app.css?v=' + __version__ in html


# -- starting while another studio is still running ------------------------------

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def _serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_start_refuses_when_older_studio_is_running(capsys):
    from api_definition_studio.cli import main as cli

    class OldStudio(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"model": {}, "version": "1.2.0"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = _serve(OldStudio)
    try:
        assert cli(["run", "--port", str(srv.server_port)]) == 1
        err = capsys.readouterr().err
        assert "already in use by API Definition Studio 1.2.0" in err and "Stop it first" in err
    finally:
        srv.shutdown()


def test_start_refuses_when_port_used_by_something_else(capsys):
    from api_definition_studio.cli import main as cli
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        assert cli(["run", "--port", str(s.getsockname()[1])]) == 1
        assert "already in use by another program" in capsys.readouterr().err
    finally:
        s.close()


def test_version_flag(capsys):
    from api_definition_studio import __version__
    from api_definition_studio.cli import main as cli
    with pytest.raises(SystemExit):
        cli(["--version"])
    assert capsys.readouterr().out.strip() == "api-definition-studio " + __version__


def test_static_files_are_revalidated(client):
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"


# -- importing an ERD -------------------------------------------------------------

SMALL_ERD = """
erDiagram
  %% a comment
  PERSON ||--o{ CASE_GROUP : "is account holder of"
  CASE_GROUP ||--|{ SUPPORT_CASE : "groups"
  DECISION |o--o{ DECISION : "revises or supersedes"
  SUPPORT_CASE }o--o{ DECISION : "is decided by"
  PERSON {
    id person_id PK
    string tax_reference "optional at registration"
    date date_of_birth
  }
  CASE_GROUP {
    id case_group_id PK
    id owner_person_id FK "account holder"
    enum status "open, on hold, closed"
    enum closure_reason
    money total_balance
  }
  SUPPORT_CASE {
    id case_id PK
    id case_group_id FK
    bool premium_service
  }
  DECISION {
    id decision_id PK
    id prior_decision_id FK
  }
  DECISION_EVIDENCE {
    id decision_id PK, FK
    id evidence_id PK, FK
  }
"""


def erd(client, text, **params):
    q = "&".join(k + "=" + str(v) for k, v in params.items())
    return client.post("/api/import/erd?" + q, content=text.encode())


def test_import_erd_mermaid(client):
    before = model(client)
    prev = erd(client, SMALL_ERD, apply="false", namespaces="single")
    assert prev.status_code == 200, prev.text
    p = prev.json()
    assert p["applied"] is False and p["summary"] == {"diagrams": 1, "entities": 5, "columns": 15, "links": 4,
                                                      "namespaces": 1}
    assert p["errors"] == 0 and model(client) == before        # preview changes nothing
    r = erd(client, SMALL_ERD, apply="true", namespaces="single").json()
    assert r["applied"] is True
    m = model(client)
    ents = {e["business_name"]: e for e in m["entities"]}
    assert len(m["entities"]) == len(before["entities"]) + 5
    cg = ents["Case Group"]
    assert (cg["api_name"], cg["plural"], cg["schema_name"]) == ("caseGroup", "case-groups", "CaseGroup")
    assert cg["source_system_id"] == "" and cg["operations"] == []
    cols = {a["api_name"]: a for a in cg["attributes"]}
    assert cols["caseGroupId"]["key"] and cols["caseGroupId"]["business_name"] == "Case Group Id"
    assert cols["ownerPersonId"]["business_name"] == "Owner Person Id"
    assert cols["ownerPersonId"]["description"] == "Foreign key to Person. account holder"
    assert [v["code"] for v in cols["status"]["enum"]] == ["OPEN", "ON_HOLD", "CLOSED"]
    assert cols["closureReason"]["type"] == "string" and "to be defined" in cols["closureReason"]["description"]
    assert (cols["totalBalance"]["type"], cols["totalBalance"]["format"]) == ("number", "decimal")
    # composite key: no single key, described instead
    de = ents["Decision Evidence"]
    assert not any(a["key"] for a in de["attributes"]) and "Composite key" in de["description"]
    # links: cardinality, joins, self link, not exposed
    rels = {r["business_name"]: r for r in m["relationships"] if r["from_entity_id"] in {e["id"] for e in ents.values()}}
    pp = rels["Is account holder of"]
    assert pp["cardinality"] == "one-to-many" and pp["expose"] == "none" and pp["inverse_expose"] == "none"
    assert (pp["join_from"], pp["join_to"]) == ("PERSON.person_id", "CASE_GROUP.owner_person_id")
    assert rels["Revises or supersedes"]["join_to"] == "DECISION.prior_decision_id"
    assert rels["Is decided by"]["cardinality"] == "many-to-many" and rels["Is decided by"]["join_from"] == ""
    assert "one or many" in rels["Groups"]["description"]
    # the new namespace is valid OpenAPI, and the mapping sheet still round-trips
    validate_spec(spec(client, "model"))
    x = client.get("/api/export/mapping.xlsx").content
    before_x = spec(client, "model")
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    after_x = spec(client, "model")
    for d in (before_x, after_x):
        d["info"].pop("x-model-fingerprint")
    assert before_x == after_x


def test_import_erd_logical_model_page(client):
    page = (FIXTURES / "sample-logical-data-model.html").read_text()
    p = erd(client, page, apply="false").json()
    assert p["summary"] == {"diagrams": 4, "entities": 13, "columns": 72, "links": 15, "namespaces": 4}
    assert [n["key"] for n in p["new_namespaces"]] == ["cus", "ord", "bil", "ful"]
    assert any("overview" in n for n in p["notes"]) and any("Payment already exists" in n for n in p["notes"])
    assert any("Contact already exists" in n for n in p["notes"])   # Customer matches the starter's API name
    assert p["errors"] == 0
    erd(client, page, apply="true").json()
    m = model(client)
    assert len(m["entities"]) == 4 + 11                         # sample + model, Customer and Payment linked
    ents = {e["business_name"]: e for e in m["entities"]}
    so = ents["Sales Order"]
    assert so["description"].startswith("One order placed by an account") and "Reference: ORD-01–ORD-02" in so["description"]
    cus = [n for n in m["namespaces"] if n["key"] == "cus"][0]
    assert cus["title"] == "Customers and accounts" and cus["servers"] == m["namespaces"][0]["servers"]
    assert sum(1 for r in m["relationships"] if r["join_from"]) >= 15
    for n in m["namespaces"]:
        validate_spec(spec(client, n["key"]))
    assert not [i for i in client.get("/api/validate").json() if i["level"] == "error"]
    # a second import adds nothing new
    again = erd(client, page, apply="false").json()
    assert again["new_namespaces"] == []


def test_import_erd_options(client):
    page = (FIXTURES / "sample-logical-data-model.html").read_text()
    # separate: the clashing Payment is imported under its subject area
    erd(client, page, apply="true", clash="separate", operations="read")
    m = model(client)
    ents = {e["business_name"]: e for e in m["entities"]}
    assert "Payment (BIL)" in ents and ents["Payment (BIL)"]["schema_name"] == "BilPayment"
    # read operations, except entities without a single key
    assert ents["Sales Order"]["operations"] == ["list", "get"]
    assert ents["Account Contact"]["operations"] == []
    assert "/ord/v1/sales-orders/{orderId}" in spec(client, "ord")["paths"]
    assert not [i for i in client.get("/api/validate").json() if i["level"] == "error"]
    # replace: only the logical model remains, source systems kept
    erd(client, page, apply="true", mode="replace")
    m = model(client)
    assert len(m["entities"]) == 13 and len(m["namespaces"]) == 4 and len(m["source_systems"]) == 2


def test_import_erd_rejects_text_without_a_diagram(client):
    r = erd(client, "just some text", apply="false")
    assert r.status_code == 422 and "erDiagram" in r.json()["detail"]
    assert client.post("/api/import/erd?namespaces=single&namespace_id=nope", content=SMALL_ERD.encode()).status_code == 404


def test_cli_import_erd(tmp_path, capsys):
    from api_definition_studio.cli import main as cli
    f = tmp_path / "model.mmd"
    f.write_text(SMALL_ERD)
    data = tmp_path / "data"
    assert cli(["import-erd", str(f), "--data", str(data), "--namespaces", "single", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "5 entities, 15 columns, 4 links" in out and "Dry run" in out
    assert cli(["import-erd", str(f), "--data", str(data), "--namespaces", "single"]) == 0
    m = json.loads((data / "model.json").read_text())
    assert any(e["business_name"] == "Case Group" for e in m["entities"])


# -- per-operation settings, actions, keyed sub-resources ------------------------------

def test_operation_override_changes_response_and_status(client):
    r = client.patch("/api/entities/en_contact", json={"op_overrides": {"create": {
        "status": "202", "response_entity_id": "en_case", "response_mode": "full", "summary": "Register a customer",
        "description": "Accepted for checking."}}})
    assert r.status_code == 200, r.text
    post = spec(client)["paths"]["/cm/v1/customers"]["post"]
    assert post["summary"] == "Register a customer" and post["description"] == "Accepted for checking."
    assert "202" in post["responses"] and "201" not in post["responses"]
    assert post["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith("/Case")
    # no body
    client.patch("/api/entities/en_contact", json={"op_overrides": {"get": {"response_entity_id": "none"}}})
    get = spec(client)["paths"]["/cm/v1/customers/{customerId}"]["get"]
    assert "content" not in get["responses"]["200"]
    # a different request body
    client.patch("/api/entities/en_contact", json={"op_overrides": {"patch": {"request_entity_id": "en_address"}}})
    patch_ = spec(client)["paths"]["/cm/v1/customers/{customerId}"]["patch"]
    assert patch_["requestBody"]["content"]["application/merge-patch+json"]["schema"]["$ref"].endswith("/AddressUpdate")
    validate_spec(spec(client))
    # bad references are refused; settings for an unticked operation are flagged
    assert client.patch("/api/entities/en_contact", json={"op_overrides": {"get": {"response_entity_id": "en_nope"}}}).status_code == 422
    assert client.patch("/api/entities/en_contact", json={"op_overrides": {"get": {"status": "299"}}}).status_code == 422
    client.patch("/api/entities/en_contact", json={"op_overrides": {"delete": {"summary": "x"}}})
    assert any("not ticked" in i["message"] for i in client.get("/api/validate").json())
    # blank settings are dropped
    client.patch("/api/entities/en_contact", json={"op_overrides": {"get": {"summary": ""}}})
    assert model(client)["entities"][0]["op_overrides"] == {}


def test_actions(client):
    r = client.post("/api/entities/en_case/actions", json={
        "name": "closure", "summary": "Close a case", "request_entity_id": "en_payment", "request_variant": "full",
        "response_entity_id": "self", "status": "202", "if_match": True, "errors": ["NotFound", "Conflict"]})
    assert r.status_code == 201, r.text
    act = r.json()
    assert act["path"] == "closure" and act["method"] == "POST"
    op = spec(client)["paths"]["/cm/v1/cases/{caseId}/closure"]["post"]
    assert op["operationId"] == "caseClosure" and op["x-action"] == "closure"
    assert op["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/Payment")
    assert set(op["responses"]) == {"202", "400", "404", "409", "412"}
    assert [p_["name"] for p_ in op["parameters"] if "name" in p_] == ["If-Match"]
    # renaming moves a path that followed the name
    client.patch("/api/actions/" + act["id"], json={"name": "closeCase"})
    assert "/cm/v1/cases/{caseId}/close-case" in spec(client)["paths"]
    # a collection action, with no request and no response body
    client.post("/api/entities/en_case/actions", json={"name": "recalculate", "scope": "collection",
                                                        "response_entity_id": "", "status": "204"})
    rec = spec(client)["paths"]["/cm/v1/cases/recalculate"]["post"]
    assert "requestBody" not in rec and "content" not in rec["responses"]["204"]
    validate_spec(spec(client))
    # mistakes are refused
    assert client.post("/api/entities/en_case/actions", json={"name": "Close Case"}).status_code == 422
    assert client.post("/api/entities/en_case/actions", json={"name": "closeCase"}).status_code == 422
    assert client.post("/api/entities/en_case/actions", json={"name": "x", "method": "FETCH"}).status_code == 422
    assert client.post("/api/entities/en_case/actions", json={"name": "y", "request_entity_id": "en_nope"}).status_code == 422
    # path already used by a sub-resource
    client.patch("/api/relationships/rl_pay", json={"sub_ops": ["list"]})
    assert client.post("/api/entities/en_case/actions", json={"name": "payments"}).status_code == 422
    assert client.delete("/api/actions/" + act["id"]).status_code == 204
    assert "/cm/v1/cases/{caseId}/close-case" not in spec(client)["paths"]


def test_keyed_sub_resources(client):
    # addresses of a customer: list, add, and get/replace/remove one by its address type
    typ = [a for a in model(client)["entities"][2]["attributes"] if a["api_name"] == "addressType"][0]
    r = client.patch("/api/relationships/rl_addr", json={"sub_ops": ["list", "create", "get", "update", "delete"],
                                                         "sub_key": typ["id"]})
    assert r.status_code == 200, r.text
    doc = spec(client)
    validate_spec(doc)
    coll = doc["paths"]["/cm/v1/customers/{customerId}/addresses"]
    assert set(coll) == {"parameters", "get", "post"}
    assert coll["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/AddressCreate")
    item = doc["paths"]["/cm/v1/customers/{customerId}/addresses/{addressType}"]
    assert set(item) == {"parameters", "get", "put", "delete"}
    assert item["parameters"][1]["schema"]["enum"] == ["HOME", "CORRESPONDENCE"]
    assert item["put"]["operationId"] == "replaceCustomerAddressesByAddressType"
    # the embedded property is still there too
    assert "addresses" in doc["components"]["schemas"]["Customer"]["properties"]
    # a sub-resource setting for one operation
    client.patch("/api/relationships/rl_addr", json={"sub_overrides": {"create": {"status": "202", "summary": "Add an address"}}})
    post = spec(client)["paths"]["/cm/v1/customers/{customerId}/addresses"]["post"]
    assert post["summary"] == "Add an address" and "202" in post["responses"]
    # a key that is not a column of the target is refused
    assert client.patch("/api/relationships/rl_addr", json={"sub_key": "at_c_fn"}).status_code == 422
    # unknown operation is refused
    assert client.patch("/api/relationships/rl_addr", json={"sub_ops": ["list", "explode"]}).status_code == 200
    assert model(client)["relationships"][2]["sub_ops"] == ["list"]   # unknown ones dropped


def test_nested_create_and_system_set_links(client):
    client.patch("/api/relationships/rl_addr", json={"nested_create": True})
    client.patch("/api/relationships/rl_pp", json={"read_only": True})
    s_ = spec(client)["components"]["schemas"]
    assert s_["CustomerCreate"]["properties"]["addresses"]["items"]["$ref"].endswith("/AddressCreate")
    assert "AddressCreate" in s_
    assert "accountHolder" not in s_["CaseCreate"]["properties"]          # set by the system
    assert "requester" in s_["CaseCreate"]["properties"]
    assert s_["Case"]["properties"]["accountHolder"]["readOnly"] is True
    validate_spec(spec(client))


# -- the service agreement example -------------------------------------------------------

AGREEMENT_OPS = {
    ("GET", "/cm/v1/cases/{caseId}/agreements"): ("", "ServiceAgreementPage", "200"),
    ("POST", "/cm/v1/cases/{caseId}/agreements"): ("ServiceAgreementCreate", "CreateCaseAgreementsResponse", "201"),
    ("GET", "/cm/v1/agreements/{agreementId}"): ("", "ServiceAgreement", "200"),
    ("GET", "/cm/v1/cases/{caseId}/agreement-changes"): ("", "AgreementChangePage", "200"),
    ("POST", "/cm/v1/cases/{caseId}/agreement-changes"): ("AgreementChangeCreate", "CreateCaseAgreementChangesResponse", "201"),
    ("GET", "/cm/v1/agreement-changes/{changeId}"): ("", "AgreementChange", "200"),
    ("PUT", "/cm/v1/agreement-changes/{changeId}"): ("AgreementChangeCreate", "AgreementChange", "200"),
    ("GET", "/cm/v1/agreement-changes/{changeId}/declarations/{partyRole}"): ("", "AgreementDeclaration", "200"),
    ("PUT", "/cm/v1/agreement-changes/{changeId}/declarations/{partyRole}"): ("AgreementDeclarationCreate", "AgreementDeclaration", "200"),
    ("POST", "/cm/v1/agreement-changes/{changeId}/decision"): ("AgreementDecisionCreate", "AgreementChange", "200"),
    ("POST", "/cm/v1/agreement-changes/{changeId}/withdrawal"): ("", "AgreementChange", "200"),
    ("GET", "/cm/v1/agreement-questions"): ("", "AgreementQuestionPage", "200"),
}


def test_agreements_example(client):
    prev = client.post("/api/examples/agreements?apply=false").json()
    assert prev["applied"] is False and prev["errors"] == 0
    assert "Agreement Change" in prev["added"] and "Case" not in prev["added"]
    assert any("Case already exists" in n for n in prev["notes"])
    assert client.post("/api/examples/agreements?apply=true").json()["applied"] is True
    eps = {(x["method"], x["path"]): (x["request"], x["response"], x["status"])
           for x in client.get("/api/endpoints").json() if "agreement" in x["path"]}
    assert eps == AGREEMENT_OPS
    doc = spec(client)
    validate_spec(doc)
    s_ = doc["components"]["schemas"]
    # POST on the arrangement and POST on a change return different things
    assert s_["ServiceAgreement"]["properties"].keys() != s_["AgreementChange"]["properties"].keys()
    # create bodies: no system columns, nested declarations and answers, no system-set link
    assert set(s_["AgreementChangeCreate"]["properties"]) == {"productId", "requestedBy", "reportedDate",
                                                                "proposedEffectiveDate", "reason", "declarations"}
    assert set(s_["AgreementDeclarationCreate"]["properties"]) == {"partyRole", "hoursPerMonth", "basis",
                                                                     "evidenceReference", "answers"}
    assert set(s_["ServiceAgreementCreate"]["properties"]) == {"productId", "hoursPerMonth", "basis",
                                                                     "effectiveFrom", "declarations"}
    assert s_["AgreementChange"]["properties"]["resultingAgreement"]["readOnly"] is True
    post = doc["paths"]["/cm/v1/cases/{caseId}/agreements"]["post"]
    assert "409" in post["responses"] and "set-up" in post["description"]
    decision = doc["paths"]["/cm/v1/agreement-changes/{changeId}/decision"]["post"]
    assert [p_["name"] for p_ in decision["parameters"] if "name" in p_] == ["If-Match"]
    assert not [i for i in client.get("/api/validate").json() if i["level"] == "error"]
    # adding it again changes nothing
    again = client.post("/api/examples/agreements?apply=false").json()
    assert again["added"] == []
    # and it survives a round trip through the Excel mapping sheet
    before = spec(client)
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    ops = {(r[0], r[1], r[2]) for r in wb["Operations"].iter_rows(min_row=2, values_only=True)}
    assert ("AgreementChange", "action", "decision") in ops and ("Case", "sub-resource", "create") in ops
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    after = spec(client)
    for d in (before, after):
        d["info"].pop("x-model-fingerprint")
    assert before == after


def test_agreements_example_on_an_empty_model(client):
    m = model(client)
    empty = dict(m, namespaces=[], entities=[], relationships=[], source_systems=[])
    assert client.put("/api/model", json=empty).status_code == 200
    r = client.post("/api/examples/agreements?apply=true").json()
    assert "Case" in r["added"] and any("cm namespace" in n for n in r["notes"])
    m = model(client)
    assert [s["kind"] for s in m["source_systems"]] == ["siebel"]
    validate_spec(spec(client))
    assert client.post("/api/examples/nope").status_code == 404


def test_operations_sheet_errors(client):
    client.post("/api/examples/agreements?apply=true")
    wb = load_workbook(io.BytesIO(client.get("/api/export/mapping.xlsx").content))
    ws = wb["Operations"]
    ws.append(["AgreementChange", "sub-resource", "list", "nope"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/import/mapping", content=buf.getvalue())
    assert r.status_code == 422 and "has no link named 'nope'" in r.json()["detail"]


def test_cli_add_example(tmp_path, capsys):
    from api_definition_studio.cli import main as cli
    assert cli(["add-example", "agreements", "--data", str(tmp_path)]) == 0
    m = json.loads((tmp_path / "model.json").read_text())
    assert any(e["schema_name"] == "AgreementChange" for e in m["entities"])
    assert cli(["add-example", "nope", "--data", str(tmp_path)]) == 1


# -- standard headers ------------------------------------------------------------------

STD_HEADERS = ["request-id", "date-time", "correlation-id", "journey-instance-id", "instigating-system"]


def _param_names(doc, op):
    comps = doc["components"].get("parameters", {})
    out = []
    for p_ in op.get("parameters", []):
        if "$ref" in p_:
            out.append(comps[p_["$ref"].split("/")[-1]]["name"])
        else:
            out.append(p_["name"])
    return out


def test_standard_headers_on_every_operation(client):
    m = model(client)
    assert [h["name"] for h in m["headers"]] == STD_HEADERS
    client.post("/api/examples/agreements?apply=true")
    doc = spec(client)
    validate_spec(doc)
    comps = doc["components"]
    assert set(comps["parameters"]) == {"RequestId", "DateTime", "CorrelationId", "JourneyInstanceId", "InstigatingSystem"}
    assert comps["parameters"]["DateTime"]["schema"] == {"type": "string", "format": "date-time"}
    assert comps["parameters"]["RequestId"]["required"] is True
    assert set(comps["headers"]) == {"RequestId", "CorrelationId"}          # the two sent back
    n_ops = 0
    for path, item in doc["paths"].items():
        for method, op in item.items():
            if method == "parameters":
                continue
            n_ops += 1
            names = _param_names(doc, op)
            assert all(h in names for h in STD_HEADERS), (method, path)
            for code, resp in op["responses"].items():
                hs = resp.get("headers") if "$ref" not in resp else comps["responses"][resp["$ref"].split("/")[-1]].get("headers")
                assert set(hs) >= {"request-id", "correlation-id"}, (method, path, code)
    assert n_ops > 20
    # Authorization comes from security, not a header parameter
    assert doc["security"] == [{"bearerAuth": []}]
    assert "Authorization" not in json.dumps(comps["parameters"])


def test_header_scope_and_editing(client):
    ns = model(client)["namespaces"][0]["id"]
    h = client.post("/api/headers", json={"name": "x-channel", "required": False, "methods": ["POST", "PUT"],
                                          "description": "Channel the request came from", "example": "PORTAL"}).json()
    doc = spec(client)
    assert "XChannel" in doc["components"]["parameters"]
    assert "x-channel" in _param_names(doc, doc["paths"]["/cm/v1/cases"]["post"])
    assert "x-channel" not in _param_names(doc, doc["paths"]["/cm/v1/cases"]["get"])
    # limit to a namespace that does not exist -> refused? (unknown namespace is an error, not blocking)
    client.patch("/api/headers/" + h["id"], json={"namespace_ids": [ns], "direction": "response", "format": "uuid"})
    doc = spec(client)
    assert "XChannel" in doc["components"]["headers"] and "XChannel" not in doc["components"]["parameters"]
    post = doc["paths"]["/cm/v1/cases"]["post"]["responses"]["201"]
    assert "x-channel" in post["headers"]
    # mistakes are refused
    assert client.post("/api/headers", json={"name": "request-id"}).status_code == 422          # duplicate
    assert client.post("/api/headers", json={"name": "bad header"}).status_code == 422
    assert client.post("/api/headers", json={"name": "y", "direction": "sideways"}).status_code == 422
    assert client.post("/api/headers", json={"name": "z", "methods": ["FETCH"]}).status_code == 422
    # Authorization is flagged and left out
    a = client.post("/api/headers", json={"name": "Authorization"}).json()
    assert any("security setting" in i["message"] for i in client.get("/api/validate").json() if i["object_id"] == a["id"])
    assert "Authorization" not in json.dumps(spec(client)["components"].get("parameters", {}))
    # delete, then add the standard set back
    for x in model(client)["headers"]:
        client.delete("/api/headers/" + x["id"])
    assert model(client)["headers"] == []
    assert "parameters" not in spec(client)["components"]
    client.post("/api/headers/standard")
    assert [x["name"] for x in model(client)["headers"]] == STD_HEADERS


def test_headers_round_trip_through_excel(client):
    client.post("/api/headers", json={"name": "x-channel", "methods": ["POST"], "required": False, "example": "PORTAL"})
    before = spec(client)
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    rows = list(wb["Headers"].iter_rows(values_only=True))
    assert rows[0][:3] == ("Name", "Direction", "Required") and rows[1][0] == "request-id"
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    after = spec(client)
    for d in (before, after):
        d["info"].pop("x-model-fingerprint")
    assert before == after
    # a workbook without a Headers sheet keeps the current headers
    del wb["Headers"]
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/import/mapping?apply=true", content=buf.getvalue()).json()
    assert any("kept the current model's headers" in n for n in r["notes"])
    assert len(model(client)["headers"]) == 6


# -- what POST and PUT responses contain -------------------------------------------

def test_post_returns_row_id_by_default(client):
    doc = spec(client)
    post = doc["paths"]["/cm/v1/customers"]["post"]["responses"]["201"]
    ref = post["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    assert ref == "CreateCustomerResponse"
    sch = doc["components"]["schemas"][ref]
    assert list(sch["properties"]) == ["customerId"] and sch["required"] == ["customerId"]
    assert sch["example"] == {"customerId": "1-7ABC12"} and "row id" in sch["description"]
    assert "Location" in post["headers"]
    # PUT returns the full record by default
    client.patch("/api/entities/en_contact", json={"operations": ["list", "get", "create", "update"]})
    put = spec(client)["paths"]["/cm/v1/customers/{customerId}"]["put"]["responses"]["200"]
    assert put["content"]["application/json"]["schema"]["$ref"].endswith("/Customer")
    # change the defaults
    st = client.get("/api/model").json()["model"]["settings"]
    client.patch("/api/settings", json=dict(st, post_response="full", put_response="none"))
    doc = spec(client)
    assert doc["paths"]["/cm/v1/customers"]["post"]["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/Customer")
    assert "content" not in doc["paths"]["/cm/v1/customers/{customerId}"]["put"]["responses"]["200"]
    assert client.patch("/api/settings", json=dict(st, post_response="everything")).status_code == 422
    validate_spec(doc)


def test_response_content_per_operation(client):
    attrs = {a["api_name"]: a["id"] for a in model(client)["entities"][0]["attributes"]}
    client.patch("/api/entities/en_contact", json={"operations": ["list", "get", "create", "update"], "op_overrides": {
        "update": {"response_mode": "fields", "response_fields": [attrs["customerId"], attrs["customerNumber"], attrs["updatedAt"]]},
        "create": {"response_mode": "full"}}})
    doc = spec(client)
    put = doc["paths"]["/cm/v1/customers/{customerId}"]["put"]["responses"]["200"]
    ref = put["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    assert ref == "ReplaceCustomerResponse"
    assert list(doc["components"]["schemas"][ref]["properties"]) == ["customerId", "customerNumber", "updatedAt"]
    assert doc["paths"]["/cm/v1/customers"]["post"]["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/Customer")
    # an action can return only the key too
    client.post("/api/entities/en_case/actions", json={"name": "closure", "response_mode": "key"})
    op = spec(client)["paths"]["/cm/v1/cases/{caseId}/closure"]["post"]
    sch = spec(client)["components"]["schemas"][op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]]
    assert list(sch["properties"]) == ["caseId"]
    # bad mode is refused; stale field ids are flagged
    assert client.patch("/api/entities/en_contact", json={"op_overrides": {"get": {"response_mode": "half"}}}).status_code == 422
    client.patch("/api/entities/en_contact", json={"op_overrides": {"get": {"response_mode": "fields", "response_fields": ["at_gone"]}}})
    assert any("not columns" in i["message"] for i in client.get("/api/validate").json())
    # round trip through Excel keeps the selected fields
    client.patch("/api/entities/en_contact", json={"op_overrides": {"update": {"response_mode": "fields", "response_fields": [attrs["customerId"], attrs["customerNumber"]]}}})
    before = spec(client)
    x = client.get("/api/export/mapping.xlsx").content
    ops = {(r[0], r[2]): r[11] for r in load_workbook(io.BytesIO(x))["Operations"].iter_rows(min_row=2, values_only=True)}
    assert ops[("Customer", "update")] == "customerId, customerNumber"
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    after = spec(client)
    for d in (before, after):
        d["info"].pop("x-model-fingerprint")
    assert before == after


def test_existing_model_gets_headers_and_row_id_posts(tmp_path):
    shutil.copyfile(FIXTURES / "model-1.1.json", tmp_path / "model.json")
    main.reset_store(str(tmp_path))
    with TestClient(main.app) as c:
        m = c.get("/api/model").json()["model"]
        assert [h["name"] for h in m["headers"]] == STD_HEADERS and m["settings"]["post_response"] == "key"
        doc = c.get("/api/openapi/cm.json").json()
        validate_spec(doc)
        assert doc["paths"]["/cm/v1/cases"]["post"]["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/CreateCaseResponse")


# -- query parameters ---------------------------------------------------------------

def _qp(doc, op):
    return {p_["name"]: p_ for p_ in op.get("parameters", []) if p_.get("in") == "query" and p_["name"] not in ("limit", "cursor")}


def test_column_filters_optional_required_and_match(client):
    # Case: caseNumber required, status one-of-several, openedDate range
    client.patch("/api/attributes/at_k_num", json={"filterable": True, "filter_required": True})
    client.patch("/api/attributes/at_k_st", json={"filter_match": "in"})
    client.patch("/api/attributes/at_k_open", json={"filterable": True, "filter_match": "range"})
    client.patch("/api/attributes/at_c_ln", json={"filter_match": "contains"})
    doc = spec(client)
    validate_spec(doc)
    q = _qp(doc, doc["paths"]["/cm/v1/cases"]["get"])
    assert q["caseNumber"]["required"] is True and q["caseNumber"]["description"].startswith("Required")
    assert q["status"]["schema"]["type"] == "array" and q["status"]["explode"] is False
    assert q["status"]["schema"]["items"]["enum"] == ["ACTIVE", "SUSPENDED", "CLOSED"]
    assert q["openedDateFrom"]["schema"]["format"] == "date" and "openedDateTo" in q
    assert q["openedDateFrom"]["x-filter-match"] == "range" and q["status"]["x-source-field"] == "Status"
    names = [p_.get("name") for p_ in doc["paths"]["/cm/v1/cases"]["get"]["parameters"] if p_.get("in") == "query"]
    assert names[0] == "caseNumber"                      # required ones first
    assert _qp(doc, doc["paths"]["/cm/v1/customers"]["get"])["lastName"]["description"].startswith("Only records whose last name contains")
    # the same filters on a sub-resource list of cases
    client.patch("/api/relationships/rl_pp", json={"inverse_sub_ops": ["list"]})
    sub = spec(client)["paths"]["/cm/v1/customers/{customerId}/cases-as-account-holder"]["get"]
    assert "caseNumber" in _qp(doc, sub)
    # unticking the filter clears 'required'
    client.patch("/api/attributes/at_k_num", json={"filterable": False})
    a = [x for x in model(client)["entities"][1]["attributes"] if x["id"] == "at_k_num"][0]
    assert a["filter_required"] is False
    # bad match refused; unsuitable match flagged
    assert client.patch("/api/attributes/at_k_st", json={"filter_match": "fuzzy"}).status_code == 422
    client.patch("/api/attributes/at_k_st", json={"filter_match": "range"})
    assert any("range filter needs a number or date" in i["message"] for i in client.get("/api/validate").json())


def test_query_parameters_per_operation(client):
    # GET /cases/{caseId}?customerNumber=  (customerNumber of the account holder, required) and the list with only 'status'
    r = client.patch("/api/entities/en_case", json={"op_overrides": {
        "get": {"query_params": [{"attr_id": "at_c_num", "required": True, "match": "equals", "name": "customerNumber"}]},
        "list": {"query_params": [{"attr_id": "at_k_st", "required": False, "match": "equals"}]}}})
    assert r.status_code == 200, r.text
    doc = spec(client)
    validate_spec(doc)
    get = doc["paths"]["/cm/v1/cases/{caseId}"]["get"]
    assert _qp(doc, get)["customerNumber"]["required"] is True
    assert set(_qp(doc, doc["paths"]["/cm/v1/cases"]["get"])) == {"status"}
    # 'none' removes the column filters from the list
    client.patch("/api/entities/en_case", json={"op_overrides": {"list": {"query_params": []},
        "get": {"query_params": [{"attr_id": "at_c_num", "required": True, "match": "equals", "name": "customerNumber"}]}}})
    assert _qp(spec(client), spec(client)["paths"]["/cm/v1/cases"]["get"]) == {}
    # duplicates and paging names are refused
    assert client.patch("/api/entities/en_case", json={"op_overrides": {"get": {"query_params": [
        {"attr_id": "at_c_num"}, {"attr_id": "at_k_num", "name": "customerNumber"}]}}}).status_code == 422
    assert client.patch("/api/entities/en_case", json={"op_overrides": {"get": {"query_params": [
        {"attr_id": "at_k_num", "name": "limit"}]}}}).status_code == 422
    # endpoints show the query parameters, required ones starred
    eps = {(x["method"], x["path"]): x for x in client.get("/api/endpoints").json()}
    assert eps[("GET", "/cm/v1/cases/{caseId}")]["query"] == "customerNumber*"


def test_get_one_by_query(client):
    client.patch("/api/entities/en_contact", json={"operations": ["get", "create"], "get_by": "query"})
    doc = spec(client)
    validate_spec(doc)
    assert "/cm/v1/customers/{customerId}" not in doc["paths"]
    get = doc["paths"]["/cm/v1/customers"]["get"]
    assert get["operationId"] == "getCustomer" and _qp(doc, get)["customerId"]["required"] is True
    assert get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/Customer")
    assert "post" in doc["paths"]["/cm/v1/customers"]
    # with List ticked too, one record is read through the list: GET /customers?customerId= (optional), no path
    client.patch("/api/entities/en_contact", json={"operations": ["list", "get", "patch"]})
    doc = spec(client)
    validate_spec(doc)
    assert "get" not in doc["paths"].get("/cm/v1/customers/{customerId}", {})
    assert "patch" in doc["paths"]["/cm/v1/customers/{customerId}"]          # writes keep the key in the path
    lst = doc["paths"]["/cm/v1/customers"]["get"]
    assert lst["operationId"] == "listCustomers" and _qp(doc, lst)["customerId"]["required"] is False
    assert not [i for i in client.get("/api/validate").json() if i["level"] == "error"]
    assert client.patch("/api/entities/en_contact", json={"get_by": "header"}).status_code == 422


def test_key_in_path_and_query_is_flagged_and_fixed_by_get_by_query(client):
    # the key column added as a query parameter while still getting one by path
    client.patch("/api/entities/en_contact", json={"op_overrides": {"get": {"query_params": [
        {"attr_id": "at_c_id", "required": True}]}}})
    doc = spec(client)
    get = doc["paths"]["/cm/v1/customers/{customerId}"]["get"]
    assert _qp(doc, get)["customerId"]["required"] is True
    assert any("in the path and also a query parameter" in i["message"] for i in client.get("/api/validate").json())
    # switching to query removes the path GET, and the parameter is not repeated
    client.patch("/api/entities/en_contact", json={"get_by": "query", "operations": ["get", "create"]})
    doc = spec(client)
    validate_spec(doc)
    get = doc["paths"]["/cm/v1/customers"]["get"]
    names = [p["name"] for p in get["parameters"] if p.get("in") == "query"]
    assert names.count("customerId") == 1 and "/cm/v1/customers/{customerId}" not in doc["paths"]


def test_lookup_action_with_query(client):
    r = client.post("/api/entities/en_case/actions", json={
        "name": "lookup", "method": "GET", "scope": "collection", "response_entity_id": "self",
        "query_params": [{"attr_id": "at_c_num", "required": True, "name": "customerNumber"},
                         {"attr_id": "at_k_open", "match": "range"}]})
    assert r.status_code == 201, r.text
    op = spec(client)["paths"]["/cm/v1/cases/lookup"]["get"]
    q = _qp(spec(client), op)
    assert q["customerNumber"]["required"] is True and {"openedDateFrom", "openedDateTo"} <= set(q)
    validate_spec(spec(client))


def test_query_settings_round_trip_through_excel(client):
    client.patch("/api/attributes/at_k_num", json={"filterable": True, "filter_required": True})
    client.patch("/api/attributes/at_k_open", json={"filterable": True, "filter_match": "range"})
    client.patch("/api/entities/en_case", json={"op_overrides": {
        "get": {"query_params": [{"attr_id": "at_c_num", "required": True, "name": "customerRef"}]},
        "list": {"query_params": [{"attr_id": "at_k_st", "match": "in"}, {"attr_id": "at_k_open", "match": "range", "required": True}]}}})
    client.patch("/api/entities/en_contact", json={"operations": ["get", "create"], "get_by": "query"})
    client.post("/api/entities/en_case/actions", json={"name": "lookup", "method": "GET", "scope": "collection",
                                                       "query_params": [{"attr_id": "at_k_num", "required": True}]})
    before = spec(client)
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    ops = wb["Operations"]
    h_ = [c.value for c in ops[1]]
    rows = {(r[0], r[2]): r[h_.index("Query parameters")] for r in ops.iter_rows(min_row=2, values_only=True)}
    assert rows[("Case", "get")] == "Customer.customerNumber as customerRef (required)"
    assert rows[("Case", "list")] == "status (in), openedDate (required, range)"
    assert rows[("Case", "lookup")] == "caseNumber (required)"
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    after = spec(client)
    for d in (before, after):
        d["info"].pop("x-model-fingerprint")
    assert before == after
    # a bad entry is reported
    for r in ops.iter_rows(min_row=2):
        if r[0].value == "Case" and r[2].value == "get":
            r[h_.index("Query parameters")].value = "nope (required)"
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/import/mapping", content=buf.getvalue())
    assert r.status_code == 422 and "query parameter column 'nope' not found" in r.json()["detail"]


# -- tags --------------------------------------------------------------------

def test_entity_tags_are_cleaned_and_published(client):
    r = client.patch("/api/entities/en_case", json={"tags": [" Key  accounts", "case", "Key accounts", "", "Service type, Case"]})
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == ["Key accounts", "case", "Service type"]
    doc = spec(client)
    assert doc["components"]["schemas"]["Case"]["x-tags"] == ["Key accounts", "case", "Service type"]
    assert "x-tags" not in doc["components"]["schemas"]["Customer"]
    validate_spec(doc)


def test_tag_endpoints_set_rename_delete(client):
    r = client.put("/api/tags/Key accounts", json={"entity_ids": ["en_case", "en_contact"]})
    assert r.status_code == 200 and sorted(r.json()["entity_ids"]) == ["en_case", "en_contact"]
    client.patch("/api/entities/en_case", json={"tags": ["Key accounts", "Case"]})
    tags = {t["name"]: sorted(t["entity_ids"]) for t in client.get("/api/tags").json()}
    assert tags == {"Case": ["en_case"], "Key accounts": ["en_case", "en_contact"]}
    # setting membership removes it from entities not listed, keeps their other tags
    client.put("/api/tags/key accounts", json={"entity_ids": ["en_contact"]})
    m = model(client)
    ents = {e["id"]: e["tags"] for e in m["entities"]}
    assert ents["en_case"] == ["Case"] and ents["en_contact"] == ["key accounts"]
    assert client.put("/api/tags/x", json={"entity_ids": ["nope"]}).status_code == 404
    # rename merges into an existing tag without repeating it
    client.patch("/api/entities/en_case", json={"tags": ["Case", "Old"]})
    assert client.post("/api/tags/Old/rename", json={"name": "case"}).status_code == 200
    assert {e["id"]: e["tags"] for e in model(client)["entities"]}["en_case"] == ["Case"]
    assert client.post("/api/tags/missing/rename", json={"name": "x"}).status_code == 404
    assert client.delete("/api/tags/KEY ACCOUNTS").status_code == 204
    assert [t["name"] for t in client.get("/api/tags").json()] == ["Case"]


def test_diagram_exports_filter_by_tag(client):
    client.patch("/api/entities/en_case", json={"tags": ["Key accounts"]})
    mmd = client.get("/api/export/diagram.mmd?tags=key%20accounts&linked=false").text
    assert "    Case[" in mmd and "Customer[" not in mmd
    linked = client.get("/api/export/diagram.mmd?tags=Key%20accounts&linked=true").text
    assert "Customer[" in linked and "Case[" in linked
    everything = client.get("/api/export/diagram.mmd").text
    assert everything.count("[\"") >= linked.count("[\"") >= mmd.count("[\"")
    xml = client.get("/api/export/diagram.drawio?tags=Key%20accounts&linked=false").text
    assert "Tags: Key accounts" in xml and "e_en_contact" not in xml
    # namespace only: linked entities still come along by default, as before
    assert "Customer[" in client.get("/api/export/diagram.mmd?ns=cm").text


def test_tags_round_trip_through_excel_and_old_workbooks_keep_them(client):
    client.patch("/api/entities/en_case", json={"tags": ["Agreements", "Case"]})
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    ws = wb["Entities"]
    hdr = [c.value for c in ws[1]]
    col = hdr.index("Tags (comma separated)")
    row = next(r for r in ws.iter_rows(min_row=2) if r[hdr.index("Business name")].value == "Case")
    assert row[col].value == "Agreements, Case"
    row[col].value = "Agreements, Service type"
    buf = io.BytesIO(); wb.save(buf)
    assert client.post("/api/import/mapping?apply=true", content=buf.getvalue()).status_code == 200
    case = next(e for e in model(client)["entities"] if e["id"] == "en_case" or e["business_name"] == "Case")
    assert case["tags"] == ["Agreements", "Service type"]
    # a workbook without the Tags column keeps the current tags
    ws.delete_cols(col + 1)
    buf = io.BytesIO(); wb.save(buf)
    r = client.post("/api/import/mapping?apply=true", content=buf.getvalue())
    assert r.status_code == 200 and any("kept the current tags" in n for n in r.json()["notes"])
    case = next(e for e in model(client)["entities"] if e["business_name"] == "Case")
    assert case["tags"] == ["Agreements", "Service type"]


def test_erd_import_tags(client):
    r = erd(client, SMALL_ERD, apply="true", namespaces="single", tags="Case groups,  Calc").json()
    assert r["applied"]
    ents = {e["business_name"]: e for e in model(client)["entities"]}
    assert ents["Case Group"]["tags"] == ["Case groups", "Calc"]
    assert ents["Case"].get("tags") in ([], ["Case groups", "Calc"])   # linked to the existing Case only if named alike
    html = (FIXTURES / "sample-logical-data-model.html").read_text()
    r = erd(client, html, apply="false", mode="replace", area_tags="true", tags="Orders")
    assert r.status_code == 200, r.text
    erd(client, html, apply="true", mode="replace", area_tags="true", tags="Orders")
    m = model(client)
    assert all("Orders" in e["tags"] for e in m["entities"])
    assert all(len(e["tags"]) == 2 for e in m["entities"])          # Orders + its subject area
    assert len({e["tags"][1] for e in m["entities"]}) > 1


def test_agreements_example_is_tagged(client):
    client.post("/api/examples/agreements?apply=true")
    m = model(client)
    tagged = [e for e in m["entities"] if "Agreements" in e["tags"]]
    assert len(tagged) == 7
    assert any("Service request" in e["tags"] for e in tagged)


# -- GET lists that return a plain array ------------------------------------------

def _agreements_by_person(client):
    """The shape asked for: GET /cm/v1/agreements?personId=123 returning Agreement[]."""
    r = client.post("/api/entities", json={"namespace_id": "ns_cm", "business_name": "Agreement", "api_name": "agreement",
                                           "plural": "agreements", "operations": ["list", "get"]})
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    client.post("/api/entities/" + eid + "/attributes", json={"business_name": "Agreement Id", "key": True})
    a = client.post("/api/entities/" + eid + "/attributes", json={"business_name": "Person Id", "required": True,
                                                                  "filterable": True, "filter_required": True})
    assert a.status_code == 201, a.text
    client.post("/api/entities/" + eid + "/attributes", json={"business_name": "Hours Per Month", "type": "integer"})
    return eid


def test_list_as_plain_array(client):
    eid = _agreements_by_person(client)
    r = client.patch("/api/entities/" + eid, json={"op_overrides": {"list": {"response_mode": "array"}}})
    assert r.status_code == 200, r.text
    doc = spec(client)
    validate_spec(doc)
    op = doc["paths"]["/cm/v1/agreements"]["get"]
    names = [p.get("name") for p in op["parameters"] if p.get("in") == "query"]
    assert names == ["personId"]                                                    # no limit / cursor
    assert next(p for p in op["parameters"] if p.get("name") == "personId")["required"] is True
    body = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert body == {"type": "array", "items": {"$ref": "#/components/schemas/Agreement"}}
    assert "AgreementPage" not in doc["components"]["schemas"] and "Agreement" in doc["components"]["schemas"]
    row = next(x for x in client.get("/api/endpoints").json() if x["path"] == "/cm/v1/agreements" and x["method"] == "GET")
    assert row["response"] == "Agreement[]" and row["query"] == "personId*"
    # page is still there for the other lists
    assert "CasePage" in doc["components"]["schemas"]


def test_list_shape_default_in_settings_and_validation(client):
    eid = _agreements_by_person(client)
    st = client.get("/api/model").json()["model"]["settings"]
    assert st["list_response"] == "page"
    assert client.patch("/api/settings", json=dict(st, list_response="array")).status_code == 200
    doc = spec(client)
    for p, item in doc["paths"].items():
        if "get" in item and item["get"]["operationId"].startswith("list"):
            assert item["get"]["responses"]["200"]["content"]["application/json"]["schema"]["type"] == "array", p
    # one list can still ask for a page
    client.patch("/api/entities/" + eid, json={"op_overrides": {"list": {"response_mode": "page"}}})
    op = spec(client)["paths"]["/cm/v1/agreements"]["get"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("AgreementPage")
    assert any(p.get("name") == "cursor" for p in op["parameters"])
    # array is only for lists; full/key are not for lists
    assert client.patch("/api/entities/" + eid, json={"op_overrides": {"get": {"response_mode": "array"}}}).status_code == 422
    assert client.patch("/api/entities/" + eid, json={"op_overrides": {"list": {"response_mode": "key"}}}).status_code == 422
    assert client.patch("/api/settings", json=dict(st, list_response="list")).status_code == 422


def test_list_shape_round_trips_through_excel(client):
    eid = _agreements_by_person(client)
    client.patch("/api/entities/" + eid, json={"op_overrides": {"list": {"response_mode": "array"}}})
    before = spec(client)
    x = client.get("/api/export/mapping.xlsx").content
    client.patch("/api/entities/" + eid, json={"op_overrides": {}})
    r = client.post("/api/import/mapping?apply=true", content=x)
    assert r.status_code == 200, r.text
    after = spec(client)
    assert after["paths"]["/cm/v1/agreements"] == before["paths"]["/cm/v1/agreements"]


def test_list_wrapped_in_an_object(client):
    eid = _agreements_by_person(client)
    client.patch("/api/entities/" + eid, json={"op_overrides": {"list": {"response_mode": "wrapped"}}})
    doc = spec(client)
    validate_spec(doc)
    op = doc["paths"]["/cm/v1/agreements"]["get"]
    assert [p.get("name") for p in op["parameters"] if p.get("in") == "query"] == ["personId"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/AgreementList"}
    sch = doc["components"]["schemas"]["AgreementList"]
    assert sch["properties"] == {"agreement": {"type": "array", "items": {"$ref": "#/components/schemas/Agreement"}}}   # the API name
    assert sch["required"] == ["agreement"]
    row = next(x for x in client.get("/api/endpoints").json() if x["path"] == "/cm/v1/agreements" and x["method"] == "GET")
    assert row["response"] == "AgreementList"
    # another property name
    client.patch("/api/entities/" + eid, json={"op_overrides": {"list": {"response_mode": "wrapped", "list_property": "serviceAgreements"}}})
    doc = spec(client)
    ref_ = doc["paths"]["/cm/v1/agreements"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref_.endswith("/AgreementServiceAgreementsList")
    assert list(doc["components"]["schemas"]["AgreementServiceAgreementsList"]["properties"]) == ["serviceAgreements"]
    assert client.patch("/api/entities/" + eid, json={"op_overrides": {"list": {"response_mode": "wrapped", "list_property": "service agreement"}}}).status_code == 422
    # round trip through Excel keeps the property name
    before = spec(client)
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    ws = wb["Operations"]
    hdr = [c.value for c in ws[1]]
    assert any(r[hdr.index("Response content")].value == "wrapped: serviceAgreements" for r in ws.iter_rows(min_row=2))
    client.patch("/api/entities/" + eid, json={"op_overrides": {}})
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    assert spec(client)["paths"]["/cm/v1/agreements"] == before["paths"]["/cm/v1/agreements"]
    # as the default for every list
    st = client.get("/api/model").json()["model"]["settings"]
    client.patch("/api/entities/" + eid, json={"op_overrides": {}})
    assert client.patch("/api/settings", json=dict(st, list_response="wrapped")).status_code == 200
    doc = spec(client)
    assert doc["paths"]["/cm/v1/cases"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/CaseList")
    validate_spec(doc)


# -- embedded objects in request bodies ---------------------------------------------

def test_embedded_objects_accepted_in_post_put_and_patch(client):
    client.patch("/api/relationships/rl_addr", json={"nested_create": False})
    client.patch("/api/entities/en_contact", json={"operations": ["list", "get", "create", "update", "patch"]})
    doc = spec(client)
    assert "addresses" in doc["components"]["schemas"]["Customer"]["properties"]          # GET shows them
    assert "addresses" not in doc["components"]["schemas"]["CustomerCreate"]["properties"]  # read-only
    assert client.patch("/api/relationships/rl_addr", json={"nested_create": True}).status_code == 200
    doc = spec(client)
    validate_spec(doc)
    sc = doc["components"]["schemas"]
    assert sc["CustomerCreate"]["properties"]["addresses"]["items"] == {"$ref": "#/components/schemas/AddressCreate"}
    assert sc["CustomerUpdate"]["properties"]["addresses"]["items"] == {"$ref": "#/components/schemas/AddressCreate"}
    assert "replaces the whole list" in sc["CustomerUpdate"]["properties"]["addresses"]["description"]
    item = doc["paths"]["/cm/v1/customers/{customerId}"]
    assert item["put"]["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/CustomerCreate")
    assert doc["paths"]["/cm/v1/customers"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/CustomerCreate")


def test_embedded_other_side_can_be_accepted_too(client):
    client.patch("/api/entities/en_payment", json={"operations": ["list", "get", "create", "patch"]})
    client.patch("/api/relationships/rl_pay", json={"inverse_expose": "embedded"})
    sc = spec(client)["components"]["schemas"]
    assert "case" in sc["Payment"]["properties"] and "case" not in sc["PaymentCreate"]["properties"]
    client.patch("/api/relationships/rl_pay", json={"inverse_nested_create": True})
    doc = spec(client)
    validate_spec(doc)
    sc = doc["components"]["schemas"]
    assert sc["PaymentCreate"]["properties"]["case"]["allOf"] == [{"$ref": "#/components/schemas/CaseCreate"}]
    assert sc["PaymentUpdate"]["properties"]["case"]["allOf"] == [{"$ref": "#/components/schemas/CaseUpdate"}]
    # round trip through the Linked entities sheet
    x = client.get("/api/export/mapping.xlsx").content
    client.patch("/api/relationships/rl_pay", json={"inverse_nested_create": False})
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    rel = next(r for r in model(client)["relationships"] if r["id"] == "rl_pay" or (r["api_name"] == "payments"))
    assert rel["inverse_nested_create"] is True
    # accepting a side that is not embedded is flagged
    assert client.patch("/api/relationships/" + rel["id"], json={"inverse_expose": "link"}).status_code == 200
    assert any("other side in request bodies" in i["message"] for i in client.get("/api/validate").json())


# -- columns sent as parameters instead of in the body (POST, PUT, PATCH) --------------

def test_write_parameters_leave_the_body(client):
    eid = _agreements_by_person(client)
    ent = next(e for e in model(client)["entities"] if e["id"] == eid)
    pid = next(a["id"] for a in ent["attributes"] if a["api_name"] == "personId")
    client.patch("/api/entities/" + eid, json={"operations": ["list", "get", "create", "update", "patch"], "op_overrides": {
        "create": {"query_params": [{"attr_id": pid, "required": True}]},
        "update": {"query_params": [{"attr_id": pid, "required": True, "location": "header", "name": "person-id"}]}}})
    doc = spec(client)
    validate_spec(doc)
    sc = doc["components"]["schemas"]
    post = doc["paths"]["/cm/v1/agreements"]["post"]
    q = [p for p in post["parameters"] if p.get("name") == "personId"]
    assert q and q[0]["in"] == "query" and q[0]["required"] is True
    assert post["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/CreateAgreementRequest")
    assert "personId" not in sc["CreateAgreementRequest"]["properties"]
    assert "personId" not in sc["CreateAgreementRequest"].get("required", [])
    assert "hoursPerMonth" in sc["CreateAgreementRequest"]["properties"]
    assert "personId" in sc["AgreementCreate"]["properties"]            # the shared schema is unchanged
    put = doc["paths"]["/cm/v1/agreements/{agreementId}"]["put"]
    hp = [p for p in put["parameters"] if p.get("name") == "person-id"]
    assert hp and hp[0]["in"] == "header" and hp[0]["required"] is True
    assert "personId" not in sc["ReplaceAgreementRequest"]["properties"]
    patch_ = doc["paths"]["/cm/v1/agreements/{agreementId}"]["patch"]
    assert patch_["requestBody"]["content"]["application/merge-patch+json"]["schema"]["$ref"].endswith("/AgreementUpdate")
    rows = {(x["method"], x["path"]): x for x in client.get("/api/endpoints").json()}
    assert rows[("POST", "/cm/v1/agreements")]["query"] == "personId*"
    assert rows[("PUT", "/cm/v1/agreements/{agreementId}")]["header_params"] == "person-id*"
    assert not [i for i in client.get("/api/validate").json() if i["level"] == "error"]
    # round trip through Excel
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    ops = wb["Operations"]
    hdr = [c.value for c in ops[1]]
    cells = [r[hdr.index("Query parameters")].value for r in ops.iter_rows(min_row=2)]
    assert "personId (required)" in cells and "personId as person-id (required, header)" in cells
    client.patch("/api/entities/" + eid, json={"op_overrides": {}})
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    assert spec(client)["paths"]["/cm/v1/agreements"] == doc["paths"]["/cm/v1/agreements"]
    # a header name with a space is refused
    ent2 = next(e for e in model(client)["entities"] if e["schema_name"] == "Agreement")
    eid2, pid2 = ent2["id"], next(a["id"] for a in ent2["attributes"] if a["api_name"] == "personId")
    bad = {"update": {"query_params": [{"attr_id": pid2, "location": "header", "name": "person id"}]}}
    assert client.patch("/api/entities/" + eid2, json={"op_overrides": bad}).status_code == 422


def test_action_body_column_as_parameter(client):
    r = client.post("/api/entities/en_case/actions", json={
        "name": "transfer", "method": "POST", "scope": "item", "request_entity_id": "en_contact",
        "query_params": [{"attr_id": "at_c_num", "required": True}]})
    assert r.status_code == 201, r.text
    doc = spec(client)
    validate_spec(doc)
    op = doc["paths"]["/cm/v1/cases/{caseId}/transfer"]["post"]
    assert any(p.get("name") == "customerNumber" and p["in"] == "query" and p["required"] for p in op["parameters"])
    name = op["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    assert name == "CaseTransferRequest" and "customerNumber" not in doc["components"]["schemas"][name]["properties"]


def test_parameters_carry_examples_and_are_described(client):
    eid = _agreements_by_person(client)
    ent = next(e for e in model(client)["entities"] if e["id"] == eid)
    pid = next(a["id"] for a in ent["attributes"] if a["api_name"] == "personId")
    hours = next(a["id"] for a in ent["attributes"] if a["api_name"] == "hoursPerMonth")
    client.patch("/api/attributes/" + pid, json={"example": "123", "description": "Siebel person row id"})
    client.patch("/api/entities/" + eid, json={"operations": ["list", "get", "create"], "op_overrides": {
        "create": {"query_params": [{"attr_id": pid, "required": True, "location": "header", "name": "person-id"}]},
        "list": {"query_params": [{"attr_id": pid, "required": True}, {"attr_id": hours, "match": "range"}]}}})
    doc = spec(client)
    validate_spec(doc)
    lst = doc["paths"]["/cm/v1/agreements"]["get"]
    q = {p["name"]: p for p in lst["parameters"] if p.get("in") == "query"}
    assert q["personId"]["example"] == "123" and "Siebel person row id." in q["personId"]["description"]
    assert q["hoursPerMonthFrom"]["example"] == 1                  # sample by type when the column has none
    assert "Query parameters: `personId` (required), e.g. `123`" in lst["description"]
    post = doc["paths"]["/cm/v1/agreements"]["post"]
    h_ = next(p for p in post["parameters"] if p.get("name") == "person-id")
    assert h_["in"] == "header" and h_["example"] == "123" and "not in the request body" in h_["description"]
    assert "Headers: `person-id` (required), e.g. `123`." in post["description"]
    # standard headers keep their examples, on requests and on responses
    assert doc["components"]["parameters"]["RequestId"]["example"]
    assert all("example" in h for h in doc["components"]["headers"].values())


def test_collection_action_returning_a_list_of_another_entity(client):
    eid = _agreements_by_person(client)
    ent = next(e for e in model(client)["entities"] if e["id"] == eid)
    pid = next(a["id"] for a in ent["attributes"] if a["api_name"] == "personId")
    r = client.post("/api/entities/" + eid + "/actions", json={
        "name": "changes", "method": "GET", "scope": "collection", "response_entity_id": "en_payment",
        "response_mode": "wrapped", "list_property": "changes", "errors": [],
        "query_params": [{"attr_id": pid, "required": True}]})
    assert r.status_code == 201, r.text
    doc = spec(client)
    validate_spec(doc)
    op = doc["paths"]["/cm/v1/agreements/changes"]["get"]
    assert [p["name"] for p in op["parameters"] if p.get("in") == "query"] == ["personId"]
    sch = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    assert doc["components"]["schemas"][sch]["properties"]["changes"]["items"]["$ref"].endswith("/Payment")
    # plain array, and page with paging parameters
    aid = r.json()["id"]
    client.patch("/api/actions/" + aid, json={"response_mode": "array"})
    op = spec(client)["paths"]["/cm/v1/agreements/changes"]["get"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"]["items"]["$ref"].endswith("/Payment")
    client.patch("/api/actions/" + aid, json={"response_mode": "page"})
    doc = spec(client)
    op = doc["paths"]["/cm/v1/agreements/changes"]["get"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/PaymentPage")
    assert any(p.get("name") == "cursor" for p in op["parameters"]) and "PaymentPage" in doc["components"]["schemas"]
    # the mapping sheet keeps it
    client.patch("/api/actions/" + aid, json={"response_mode": "wrapped", "list_property": "changes"})
    before = spec(client)["paths"]["/cm/v1/agreements/changes"]
    x = client.get("/api/export/mapping.xlsx").content
    client.delete("/api/actions/" + aid)
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    assert spec(client)["paths"]["/cm/v1/agreements/changes"] == before


# -- source-system data dictionary (Siebel) ------------------------------------------

import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("make_dictionary", Path(__file__).parent / "fixtures" / "make_dictionary.py")
make_dictionary = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(make_dictionary)


def _load_dictionary(client, data=None, **params):
    q = "&".join(k + "=" + str(v) for k, v in dict({"apply": "true", "filename": "dd.xlsx"}, **params).items())
    return client.post("/api/dictionary/ss_siebel?" + q, content=data or make_dictionary.workbook_bytes())


def test_dictionary_upload_preview_and_search(client):
    prev = _load_dictionary(client, apply="false")
    assert prev.status_code == 200, prev.text
    p = prev.json()
    assert p["applied"] is False and p["rows"] == len(make_dictionary.ROWS) and p["bcs"] == 5
    assert client.get("/api/dictionary").json() == []                      # preview stores nothing
    r = _load_dictionary(client).json()
    assert r["applied"] and r["summary"]["rows"] == len(make_dictionary.ROWS)
    assert client.get("/api/model").json()["dictionaries"][0]["bcs"] == 5
    # business components by name, and by physical table
    assert [b["bc"] for b in client.get("/api/dictionary/ss_siebel/bcs?q=agreem").json()] == ["Agreement"]
    by_table = client.get("/api/dictionary/ss_siebel/bcs?q=S_SRV_REQ").json()
    assert by_table[0]["bc"] == "Service Request" and by_table[0]["tables"] == ["S_SRV_REQ"]
    # fields of one BC; search on BC field, DD attribute, table.column and data item
    f = client.get("/api/dictionary/ss_siebel/fields?bc=contact&q=phone").json()
    assert {x["field"] for x in f} == {"Cellular Phone #", "Home Phone #"}
    assert client.get("/api/dictionary/ss_siebel/fields?q=DD-AGR-004").json()[0]["field"] == "Hours Per Month"
    assert client.get("/api/dictionary/ss_siebel/fields?q=S_CONTACT_X.ATTRIB_03").json()[0]["field"] == "Tax Reference"
    assert client.get("/api/dictionary/ss_siebel/fields?q=tax reference&by=item").json()[0]["dd"] == "DD-PER-014"
    assert client.get("/api/dictionary/ss_siebel/fields?q=tax reference&by=dd").json() == []
    # merge adds, replace swaps
    extra = "Business Component (BC) Name,BC Field Name,Physical Table Name,Physical Column Name,DD Attribute Reference,Data Item Full Name\n" \
            "Product,Id,S_PROD_INT,ROW_ID,DD-PRD-001,Product Identifier\n"
    assert _load_dictionary(client, extra.encode(), mode="merge", filename="x.csv").json()["summary"]["bcs"] == 6
    assert _load_dictionary(client, extra.encode(), filename="x.csv").json()["summary"]["rows"] == 1
    assert client.delete("/api/dictionary/ss_siebel").status_code == 204
    assert client.get("/api/dictionary").json() == []


def test_dictionary_bad_files(client):
    assert client.post("/api/dictionary/ss_siebel", content=b"a,b\n1,2\n").status_code == 422
    assert client.post("/api/dictionary/ss_siebel", content=b"\xd0\xcf\x11\xe0rest").status_code == 422
    assert client.post("/api/dictionary/nope", content=make_dictionary.workbook_bytes()).status_code == 404
    # headers spelt differently, semicolon CSV, some columns missing
    csv_ = "BC Name;Field Name;Table;DD Attribute\nContact;First Name;S_CONTACT;DD-PER-003\n"
    r = client.post("/api/dictionary/ss_siebel?apply=true", content=csv_.encode()).json()
    assert r["rows"] == 1 and any("Physical Column Name" in n for n in r["notes"])


def test_dictionary_columns_lookup_fill_and_checks(client):
    _load_dictionary(client)
    # add columns from the entity's BC and from another BC (an override)
    r = client.post("/api/entities/en_contact/attributes/from-dictionary", json={
        "fields": [{"bc": "Contact", "field": "Middle Name"}, {"bc": "Contact", "field": "First Name"},
                   {"bc": "Agreement", "field": "Hours Per Month"}], "business_name_from": "field"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["skipped"] == ["First Name"]                         # already a column
    added = {a["api_name"]: a for a in body["added"]}
    mid = added["middleName"]
    assert (mid["source_field"], mid["source_column"], mid["dd_reference"], mid["data_item"], mid["source_object"]) == \
           ("Middle Name", "S_CONTACT.MID_NAME", "DD-PER-010", "Person Middle Name", "")
    hours = added["hoursPerMonth"]
    assert hours["source_object"] == "Agreement" and hours["source_column"] == "CX_AGREEMENT.HOURS_PER_MONTH"
    # names from the data item instead
    r = client.post("/api/entities/en_contact/attributes/from-dictionary", json={
        "fields": [{"bc": "Contact", "field": "Tax Reference"}], "business_name_from": "item"}).json()
    assert r["added"][0]["business_name"] == "Tax Reference Number" and r["added"][0]["api_name"] == "taxReferenceNumber"
    # OpenAPI shows the column's own BC and data item
    doc = spec(client)
    props = doc["components"]["schemas"]["Customer"]["properties"]
    assert props["hoursPerMonth"]["x-source-object"] == "Agreement"
    assert props["middleName"]["x-data-item"] == "Person Middle Name" and "x-source-object" not in props["middleName"]
    # checks: a wrong DD reference, an unknown field, an unknown BC
    attrs = {a["api_name"]: a for a in next(e for e in model(client)["entities"] if e["id"] == "en_contact")["attributes"]}
    client.patch("/api/attributes/" + attrs["firstName"]["id"], json={"dd_reference": "DD-PER-999"})
    client.patch("/api/attributes/" + attrs["lastName"]["id"], json={"source_field": "Surname"})
    client.patch("/api/attributes/" + attrs["email"]["id"] if "email" in attrs else "/api/attributes/" + attrs["emailAddress"]["id"],
                 json={"source_object": "No Such BC"})
    msgs = [i["message"] for i in client.get("/api/validate").json()]
    assert any("the dictionary says DD-PER-003" in x for x in msgs)
    assert any("BC field 'Surname' is not on business component 'Contact'" in x for x in msgs)
    assert any("'No Such BC' is not in the dictionary" in x for x in msgs)
    # fill from the dictionary: blanks only, or overwrite
    client.patch("/api/attributes/" + attrs["lastName"]["id"], json={"source_field": "Last Name", "source_column": "",
                                                                     "dd_reference": ""})
    r = client.post("/api/entities/en_contact/fill-from-dictionary").json()
    assert set(r["changed"]["lastName"]) >= {"source_column", "dd_reference", "data_item"}
    assert "firstName" in r["changed"] and r["changed"]["firstName"] == ["data_item"]     # DD kept without overwrite
    r = client.post("/api/entities/en_contact/fill-from-dictionary?overwrite=true").json()
    assert "dd_reference" in r["changed"]["firstName"]
    assert not any("the dictionary says" in i["message"] for i in client.get("/api/validate").json()
                   if "First Name" in i["where"])


def test_column_business_component_round_trips_through_excel(client):
    _load_dictionary(client)
    client.post("/api/entities/en_contact/attributes/from-dictionary", json={
        "fields": [{"bc": "Agreement", "field": "Band"}]})
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    ws = wb["Mapping"]
    hdr = [c.value for c in ws[1]]
    rows = {r[hdr.index("API name")].value: r for r in ws.iter_rows(min_row=2) if r[hdr.index("Schema name")].value == "Customer"}
    assert rows["band"][hdr.index("Source object")].value == "Agreement"
    assert rows["firstName"][hdr.index("Source object")].value == "Contact"
    assert rows["band"][hdr.index("Data item")].value == "Agreement Band"
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    attrs = {a["api_name"]: a for a in next(e for e in model(client)["entities"] if e["schema_name"] == "Customer")["attributes"]}
    assert attrs["band"]["source_object"] == "Agreement" and attrs["firstName"]["source_object"] == ""
    assert attrs["band"]["data_item"] == "Agreement Band"


# -- money columns: { "amount": 2999, "currency": "GBP" } -----------------------------

def test_money_flag_uses_the_shared_money_schema(client):
    doc = spec(client)
    validate_spec(doc)
    sc = doc["components"]["schemas"]
    money = sc["Money"]
    assert money["required"] == ["amount", "currency"]
    assert money["properties"]["amount"]["type"] == "integer" and money["properties"]["currency"]["pattern"] == "^[A-Z]{3}$"
    liab = sc["Case"]["properties"]["outstandingBalance"]
    assert liab["allOf"] == [{"$ref": "#/components/schemas/Money"}] and "pence" in liab["description"] and liab["x-money"]
    assert sc["Case"]["example"]["outstandingBalance"] == {"amount": 4250, "currency": "GBP"}
    assert sc["CaseCreate"]["properties"]["outstandingBalance"]["allOf"][0]["$ref"].endswith("/Money")
    a = next(x for x in model(client)["entities"] if x["id"] == "en_case")["attributes"]
    liab_a = next(x for x in a if x["id"] == "at_k_liab")
    assert liab_a["type"] == "integer" and liab_a["money"] is True
    # a range filter on money is on the amount
    client.patch("/api/attributes/at_k_liab", json={"filterable": True, "filter_match": "range"})
    lst = spec(client)["paths"]["/cm/v1/cases"]["get"]
    p_ = {p["name"]: p for p in lst["parameters"] if p.get("in") == "query"}
    assert p_["outstandingBalanceFrom"]["schema"] == {"type": "integer", "format": "int64"}
    assert p_["outstandingBalanceFrom"]["example"] == 4250
    # a number column flagged as money has a decimal amount; the currency comes from Settings
    st = model(client)["settings"]
    assert client.patch("/api/settings", json=dict(st, money_currency="EUR")).status_code == 200
    client.patch("/api/attributes/at_p_amt", json={"type": "number"})
    doc = spec(client)
    validate_spec(doc)
    sc = doc["components"]["schemas"]
    assert sc["DecimalMoney"]["properties"]["amount"]["type"] == "number"
    assert sc["Payment"]["properties"]["amount"]["allOf"][0]["$ref"].endswith("/DecimalMoney")
    assert sc["Payment"]["example"]["amount"] == {"amount": 42.5, "currency": "EUR"}
    assert client.patch("/api/settings", json=dict(st, money_currency="pounds")).status_code == 422
    # money on a text column is flagged and sent as plain text
    client.patch("/api/attributes/at_k_liab", json={"type": "string"})
    assert any("money column needs an integer" in i["message"] for i in client.get("/api/validate").json())
    assert spec(client)["components"]["schemas"]["Case"]["properties"]["outstandingBalance"]["type"] == "string"
    # no money columns: no Money schema
    client.patch("/api/attributes/at_k_liab", json={"money": False})
    client.patch("/api/attributes/at_p_amt", json={"money": False})
    assert not {"Money", "DecimalMoney"} & set(spec(client)["components"]["schemas"])


def test_money_candidates_and_convert(client):
    client.patch("/api/attributes/at_k_liab", json={"type": "number", "money": False, "transform": ""})
    client.post("/api/entities/en_case/attributes", json={"business_name": "Arrears Balance", "type": "number", "example": "120.5"})
    client.post("/api/entities/en_case/attributes", json={"business_name": "Fee Total", "type": "number"})
    client.post("/api/entities/en_case/attributes", json={"business_name": "Visits", "type": "integer"})
    c = {x["api_name"]: x for x in client.get("/api/money-candidates").json()}
    assert {"outstandingBalance", "arrearsBalance", "feeTotal"} <= set(c) and "visits" not in c
    r = client.post("/api/attributes/to-money", json={"ids": [c["outstandingBalance"]["attr_id"], c["arrearsBalance"]["attr_id"]]})
    assert r.status_code == 200 and len(r.json()["converted"]) == 2
    r = client.post("/api/attributes/to-money", json={"ids": [c["feeTotal"]["attr_id"]], "amount_type": "keep"})
    attrs = {a["api_name"]: a for a in next(e for e in model(client)["entities"] if e["id"] == "en_case")["attributes"]}
    assert (attrs["arrearsBalance"]["type"], attrs["arrearsBalance"]["money"]) == ("integer", True)
    assert "× 100" in attrs["arrearsBalance"]["transform"]
    assert (attrs["feeTotal"]["type"], attrs["feeTotal"]["money"], attrs["feeTotal"]["transform"]) == ("number", True, "")
    assert spec(client)["components"]["schemas"]["Case"]["example"]["arrearsBalance"] == {"amount": 12050, "currency": "GBP"}
    assert client.post("/api/attributes/to-money", json={"ids": ["nope"]}).status_code == 404
    # the mapping sheet keeps the flag
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    hdr = [c_.value for c_ in wb["Mapping"][1]]
    assert "Money" in hdr
    client.patch("/api/attributes/" + attrs["arrearsBalance"]["id"], json={"money": False})
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    attrs = {a["api_name"]: a for a in next(e for e in model(client)["entities"] if e["schema_name"] == "Case")["attributes"]}
    assert attrs["arrearsBalance"]["money"] is True and attrs["arrearsBalance"]["type"] == "integer"


def test_money_type_from_1_14_becomes_the_flag(tmp_path):
    from api_definition_studio.sample import sample_model
    from api_definition_studio.store import Store, fill_defaults
    old = fill_defaults(sample_model())
    old["schema_version"] = 3
    for e in old["entities"]:
        for a in e["attributes"]:
            if a.get("money"):
                a["type"] = "money"
                a.pop("money")
    (tmp_path / "model.json").write_text(json.dumps(old))
    s = Store(str(tmp_path))
    liab = next(a for e in s.load()["entities"] for a in e["attributes"] if a["id"] == "at_k_liab")
    assert (liab["type"], liab["money"]) == ("integer", True)
    assert list(tmp_path.glob("model-*-before-upgrade.bak.json"))


# -- derived columns ---------------------------------------------------------------

def test_derived_columns_from_another_bc_or_calculated(client):
    # from another business component: derived without a flag
    client.post("/api/entities/en_contact/attributes", json={"business_name": "Hours Per Month", "type": "integer",
                                                            "source_object": "Agreement", "source_field": "Hours Per Month"})
    # calculated on the entity: the flag, with how it is worked out
    client.post("/api/entities/en_contact/attributes", json={"business_name": "Age", "type": "integer", "derived": True,
                                                            "transform": "Years since Birth Date"})
    client.post("/api/entities/en_contact/attributes", json={"business_name": "Risk Score", "type": "integer", "derived": True})
    doc = spec(client)
    validate_spec(doc)
    props = doc["components"]["schemas"]["Customer"]["properties"]
    assert props["hoursPerMonth"]["x-derived"] is True and props["hoursPerMonth"]["x-derived-from"] == "Agreement"
    assert props["age"]["x-derived"] is True and "x-derived-from" not in props["age"]
    assert "x-derived" not in props["firstName"]
    msgs = [i for i in client.get("/api/validate").json() if "Derived column" in i["message"]]
    assert len(msgs) == 1 and "Risk Score" in msgs[0]["where"]
    # diagrams say it too
    mmd = client.get("/api/export/diagram.mmd").text
    assert '"Hours Per Month (derived from Agreement)"' in mmd and '"Age (derived)"' in mmd
    xml = client.get("/api/export/diagram.drawio").text
    assert "DRV Hours Per Month : integer ← Agreement" in xml and "fontColor=#6B46C1" in xml
    # mapping sheet: Derived = Y, and it comes back
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    ws = wb["Mapping"]
    hdr = [c.value for c in ws[1]]
    rows = {r[hdr.index("API name")].value: r for r in ws.iter_rows(min_row=2) if r[hdr.index("Schema name")].value == "Customer"}
    assert rows["hoursPerMonth"][hdr.index("Derived")].value == "Y" and rows["age"][hdr.index("Derived")].value == "Y"
    assert rows["firstName"][hdr.index("Derived")].value in (None, "", "N")
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    props2 = spec(client)["components"]["schemas"]["Customer"]["properties"]
    assert props2["hoursPerMonth"]["x-derived-from"] == "Agreement" and props2["age"]["x-derived"] is True


# -- name consistency -------------------------------------------------------------

def test_similar_names_lookup(client):
    r = client.get("/api/names/similar?business=Cust%20No").json()
    assert r[0]["business_name"] == "Customer Number" and r[0]["api_name"] == "customerNumber" and r[0]["score"] == 1.0
    assert r[0]["used_in"][0]["schema_name"] == "Customer" and "same business name" in r[0]["reasons"]
    # API names: typo and abbreviation
    r = client.get("/api/names/similar?api=outstandingBalnce").json()
    assert r[0]["api_name"] == "outstandingBalance" and r[0]["score"] < 1
    r = client.get("/api/names/similar?api=dob").json()
    assert r[0]["api_name"] == "dateOfBirth" and "same API name" in r[0]["reasons"]
    # the column being edited is left out; nothing typed, nothing back
    own = next(a for e in model(client)["entities"] for a in e["attributes"] if a["api_name"] == "customerNumber")
    assert all(g["api_name"] != "customerNumber" for g in client.get("/api/names/similar?business=Client%20Reference%20Number&exclude=" + own["id"]).json())
    assert client.get("/api/names/similar").json() == []
    # unrelated names are not offered
    assert client.get("/api/names/similar?business=Zebra%20Stripes").json() == []


def test_same_business_name_or_dd_with_different_api_names_is_flagged(client):
    # Case gets the customer's number under another API name
    client.post("/api/entities/en_case/attributes", json={"business_name": "Customer Number", "api_name": "clientRef"})
    msgs = [i for i in client.get("/api/validate").json() if "for consistency" in i["message"]]
    assert len(msgs) == 1 and msgs[0]["where"] == "Case › Customer Number"
    assert "is customerNumber in Customer; here it is clientRef" in msgs[0]["message"]
    # same DD attribute, different API names
    client.post("/api/entities/en_case/attributes", json={"business_name": "Opening Date", "api_name": "openDate",
                                                         "dd_reference": "DD-CAS-006"})
    dd = [i for i in client.get("/api/validate").json() if "DD attribute DD-CAS-006" in i["message"]]
    assert len(dd) == 1
    # keys are named after their entity on purpose: not flagged
    assert not any("caseId" in i["message"] or "customerId" in i["message"]
                   for i in client.get("/api/validate").json() if "for consistency" in i["message"])


# -- logical groups ---------------------------------------------------------------

def test_logical_groups(client):
    client.post("/api/examples/agreements?apply=true")
    r = client.put("/api/groups/Agreements", json={"entity_ids": ["en_sc_arr", "en_sc_chg", "en_sc_decl"]})
    assert r.status_code == 200 and sorted(r.json()["entity_ids"]) == ["en_sc_arr", "en_sc_chg", "en_sc_decl"]
    client.patch("/api/entities/en_case", json={"group": "  Case   management "})
    assert {g["name"]: len(g["entity_ids"]) for g in client.get("/api/groups").json()} == {"Case management": 1, "Agreements": 3}
    # an entity is in one group: putting it in another moves it
    client.put("/api/groups/Changes", json={"entity_ids": ["en_sc_chg"]})
    m = {e["id"]: e["group"] for e in model(client)["entities"]}
    assert m["en_sc_chg"] == "Changes" and m["en_sc_arr"] == "Agreements"
    client.put("/api/groups/Agreements", json={"entity_ids": ["en_sc_arr", "en_sc_chg", "en_sc_decl"]})
    # OpenAPI: tag groups for the documentation, x-group on each tag
    doc = spec(client)
    validate_spec(doc)
    tg = {g["name"]: g["tags"] for g in doc["x-tagGroups"]}
    assert set(tg["Agreements"]) == {"Service Agreement", "Agreement Change", "Agreement Declaration"}
    assert "Other" in tg and "Contact" in tg["Other"]
    assert next(t for t in doc["tags"] if t["name"] == "Case")["x-group"] == "Case management"
    # diagrams
    mmd = client.get("/api/export/diagram.mmd").text
    assert "%% group Agreements: " in mmd and "AgreementChange" in mmd.split("%% group Agreements: ")[1]
    xml = client.get("/api/export/diagram.drawio").text
    assert 'value="Agreements"' in xml and "dashed=1" in xml
    # mapping sheet round trip, and a sheet without the Group column keeps the groups
    x = client.get("/api/export/mapping.xlsx").content
    client.put("/api/groups/Agreements", json={"entity_ids": []})
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    assert {g["name"] for g in client.get("/api/groups").json()} == {"Case management", "Agreements"}
    wb = load_workbook(io.BytesIO(x))
    ws = wb["Entities"]
    col = [c.value for c in ws[1]].index("Group")
    ws.delete_cols(col + 1)
    buf = io.BytesIO(); wb.save(buf)
    assert client.post("/api/import/mapping?apply=true", content=buf.getvalue()).status_code == 200
    assert len(next(g for g in client.get("/api/groups").json() if g["name"] == "Agreements")["entity_ids"]) == 3
    # rename and delete
    assert client.post("/api/groups/agreements/rename", json={"name": "Agreement versions"}).status_code == 200
    assert client.delete("/api/groups/Agreement versions").status_code == 204
    assert [g["name"] for g in client.get("/api/groups").json()] == ["Case management"]
    assert client.post("/api/groups/nope/rename", json={"name": "x"}).status_code == 404


def test_two_entities_on_one_path_are_reported_not_overwritten(client):
    client.post("/api/examples/agreements?apply=true")
    arr = next(e for e in model(client)["entities"] if e["id"] == "en_sc_arr")
    client.patch("/api/entities/en_sc_arr", json={"operations": ["list"] + [o for o in arr["operations"] if o != "list"]})
    first = spec(client)["paths"]["/cm/v1/agreements"]["get"]
    # Agreement Change moved onto the arrangement's path segment: both tick List
    client.patch("/api/entities/en_sc_chg", json={"plural": "agreements", "operations": ["list", "get", "create", "update"]})
    doc = spec(client)
    validate_spec(doc)
    assert doc["paths"]["/cm/v1/agreements"]["get"]["operationId"] == first["operationId"]   # the first one is kept
    errs = [i for i in client.get("/api/validate").json() if "is already generated by" in i["message"]]
    assert errs and all(i["level"] == "error" for i in errs)
    assert any(i["message"].startswith("GET /cm/v1/agreements is already generated by Service Agreement's GET list, "
                                       "so Agreement Change's GET list is left out") for i in errs)
    assert any(i["object_id"] == "en_sc_chg" for i in errs)
    # /agreements/{agreementId} and /agreements/{changeId} are one path to a client: reported too
    assert any(i["message"].startswith("GET /cm/v1/agreements/{changeId} is already generated by Service Agreement's GET one")
               for i in errs)
    assert "/cm/v1/agreements/{changeId}" not in doc["paths"]
    # the endpoints list shows the kept operation once
    rows = [x for x in client.get("/api/endpoints").json() if x["path"] == "/cm/v1/agreements" and x["method"] == "GET"]
    assert len(rows) == 1 and rows[0]["entity_id"] == "en_sc_arr"
    # a distinct path segment clears it
    client.patch("/api/entities/en_sc_chg", json={"plural": "agreement-changes"})
    assert not [i for i in client.get("/api/validate").json() if "is already generated by" in i["message"]]


def test_custom_operation_ids(client):
    client.patch("/api/entities/en_case", json={"op_overrides": {"list": {"operation_id": "findCases"},
                                                                 "get": {"operation_id": "readCase"}}})
    r = client.post("/api/entities/en_case/actions", json={"name": "lookup", "method": "GET", "scope": "collection",
                                                           "operation_id": "findCaseByNumber"})
    assert r.status_code == 201, r.text
    doc = spec(client)
    validate_spec(doc)
    assert doc["paths"]["/cm/v1/cases"]["get"]["operationId"] == "findCases"
    assert doc["paths"]["/cm/v1/cases/{caseId}"]["get"]["operationId"] == "readCase"
    assert doc["paths"]["/cm/v1/cases/lookup"]["get"]["operationId"] == "findCaseByNumber"
    rows = {x["path"]: x["operation_id"] for x in client.get("/api/endpoints").json() if x["method"] == "GET"}
    assert rows["/cm/v1/cases"] == "findCases"
    # must be camelCase, and unique
    assert client.patch("/api/entities/en_case", json={"op_overrides": {"list": {"operation_id": "Find-Cases"}}}).status_code == 422
    client.patch("/api/entities/en_contact", json={"op_overrides": {"list": {"operation_id": "findCases"}}})
    dup = [i for i in client.get("/api/validate").json() if "Operation ID findCases" in i["message"]]
    assert len(dup) == 1 and dup[0]["level"] == "error"
    # mapping sheet keeps them
    client.patch("/api/entities/en_contact", json={"op_overrides": {}})
    x = client.get("/api/export/mapping.xlsx").content
    client.patch("/api/entities/en_case", json={"op_overrides": {}})
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    doc = spec(client)
    assert doc["paths"]["/cm/v1/cases"]["get"]["operationId"] == "findCases"
    assert doc["paths"]["/cm/v1/cases/lookup"]["get"]["operationId"] == "findCaseByNumber"


def test_nested_path_segment_puts_one_entity_under_another(client):
    client.post("/api/examples/agreements?apply=true")
    arr = next(e for e in model(client)["entities"] if e["id"] == "en_sc_arr")
    client.patch("/api/entities/en_sc_arr", json={"operations": ["list"] + [o for o in arr["operations"] if o != "list"]})
    r = client.patch("/api/entities/en_sc_chg", json={"plural": "/agreements//changes/", "operations": ["list", "get", "update"]})
    assert r.status_code == 200 and r.json()["plural"] == "agreements/changes"
    doc = spec(client)
    validate_spec(doc)
    p = doc["paths"]
    def ref_of(op):
        sch = op["responses"]["200"]["content"]["application/json"]["schema"]
        return sch.get("$ref", "").split("/")[-1]
    assert ref_of(p["/cm/v1/agreements"]["get"]) == "ServiceAgreementPage"
    assert ref_of(p["/cm/v1/agreements/changes"]["get"]) == "AgreementChangePage"
    assert p["/cm/v1/agreements/changes"]["get"]["operationId"] == "listAgreementsChanges"
    assert "/cm/v1/agreements/changes/{changeId}" in p and "/cm/v1/agreements/changes/{changeId}/decision" in p
    assert "/cm/v1/agreements/{agreementId}" in p
    assert not [i for i in client.get("/api/validate").json() if "already generated" in i["message"]]
    # a bad segment is refused with a hint
    bad = client.patch("/api/entities/en_sc_chg", json={"plural": "agreements/Changes"})
    assert bad.status_code == 422 and "agreements/changes" in bad.text


def test_one_table_column_under_different_names(client):
    # Case maps the customer's number column again, under other names
    client.post("/api/entities/en_case/attributes", json={"business_name": "Account Holder Number", "api_name": "accountHolderNumber",
                                                         "source_object": "Contact", "source_column": "s_contact.x_cust_num"})
    msgs = [i for i in client.get("/api/validate").json() if i["message"].startswith("Table.column S_CONTACT.X_CUST_NUM")]
    assert len(msgs) == 1 and msgs[0]["where"] == "Case › Account Holder Number"
    m = msgs[0]["message"]
    assert "'Customer Number' (customerNumber) in Customer" in m and "% alike" in m
    # same names: nothing to flag
    client.post("/api/entities/en_payment/attributes", json={"business_name": "Customer Number", "api_name": "customerNumber",
                                                            "source_column": "S_CONTACT.X_CUST_NUM"})
    assert len([i for i in client.get("/api/validate").json() if i["message"].startswith("Table.column S_CONTACT.X_CUST_NUM")]) == 1
    # the lookup by table.column
    r = client.get("/api/names/similar?location=S_CONTACT.X_CUST_NUM").json()
    assert {(g["business_name"], g["api_name"]) for g in r} == {("Customer Number", "customerNumber"), ("Account Holder Number", "accountHolderNumber")}
    assert any(x.startswith("same table.column") for x in r[0]["reasons"])
    # REST locations (an endpoint shared by many fields) are not table.columns: not compared
    assert not [i for i in client.get("/api/validate").json() if "GET /v2/payments" in i["message"] and "Table.column" in i["message"]]
    # Decided By is not Decided At
    from api_definition_studio import names as nm
    assert nm.strict_norm("Decided By") != nm.strict_norm("Decided At") and nm.score("Decided By", "Decided At") < 1


# -- column groups: bankDetails { … } and bankDetails [ { … } ] -------------------------

def _bank_columns(client, many):
    r = client.patch("/api/entities/en_contact", json={"column_groups": [
        {"business_name": "Bank Details", "api_name": "bankDetails", "many": many, "required": True}]})
    assert r.status_code == 200, r.text
    gid = r.json()["column_groups"][0]["id"]
    for bn, ex, req in (("Bank Name", "Barclays", True), ("Sort Code", "12-34-56", True), ("Account Number", "12345678", False)):
        a = client.post("/api/entities/en_contact/attributes", json={"business_name": bn, "example": ex, "required": req,
                                                                    "group_id": gid})
        assert a.status_code == 201, a.text
    return gid


def test_column_group_as_an_object(client):
    _bank_columns(client, many=False)
    doc = spec(client)
    validate_spec(doc)
    sc = doc["components"]["schemas"]
    p = sc["Customer"]["properties"]
    assert "bankName" not in p and p["bankDetails"]["type"] == "object"
    assert list(p["bankDetails"]["properties"]) == ["bankName", "sortCode", "accountNumber"]
    assert p["bankDetails"]["required"] == ["bankName", "sortCode"]
    assert sc["Customer"]["example"]["bankDetails"] == {"bankName": "Barclays", "sortCode": "12-34-56", "accountNumber": "12345678"}
    assert "bankDetails" in sc["CustomerCreate"]["required"] and sc["CustomerCreate"]["properties"]["bankDetails"]["required"] == ["bankName", "sortCode"]
    assert "required" not in sc["CustomerUpdate"]["properties"]["bankDetails"]        # merge patch: only what changes
    # mapping sheet: JSON path and the group column, round trip
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    ws = wb["Mapping"]
    hdr = [c.value for c in ws[1]]
    row = next(r for r in ws.iter_rows(min_row=2) if r[hdr.index("API name")].value == "sortCode")
    assert row[hdr.index("Column group")].value == "bankDetails" and "customer.bankDetails.sortCode" in row[hdr.index("JSON path")].value
    before = sc["Customer"]
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    after = spec(client)["components"]["schemas"]["Customer"]
    assert after["properties"]["bankDetails"] == before["properties"]["bankDetails"]
    # diagrams
    assert "(in bankDetails)" in client.get("/api/export/diagram.mmd").text
    assert "bankDetails.Sort Code" in client.get("/api/export/diagram.drawio").text


def test_column_group_as_a_list_and_checks(client):
    gid = _bank_columns(client, many=True)
    doc = spec(client)
    validate_spec(doc)
    p = doc["components"]["schemas"]["Customer"]["properties"]["bankDetails"]
    assert p["type"] == "array" and p["items"]["properties"]["sortCode"]["type"] == "string"
    assert doc["components"]["schemas"]["Customer"]["example"]["bankDetails"] == [{"bankName": "Barclays", "sortCode": "12-34-56", "accountNumber": "12345678"}]
    upd = doc["components"]["schemas"]["CustomerUpdate"]["properties"]["bankDetails"]
    assert "replaces the whole list" in upd["description"]
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    ws = wb["Mapping"]
    hdr = [c.value for c in ws[1]]
    row = next(r for r in ws.iter_rows(min_row=2) if r[hdr.index("API name")].value == "sortCode")
    assert row[hdr.index("Column group")].value == "bankDetails[]" and "customer.bankDetails[].sortCode" in row[hdr.index("JSON path")].value
    # a key column cannot go in a group; a name clash is refused; an empty group is reported
    key = next(a for a in model(client)["entities"][0]["attributes"] if a["key"])
    client.patch("/api/attributes/" + key["id"], json={"group_id": gid})
    assert any("key column stays at the top level" in i["message"] for i in client.get("/api/validate").json())
    client.patch("/api/attributes/" + key["id"], json={"group_id": ""})
    groups = model(client)["entities"][0]["column_groups"]
    assert client.patch("/api/entities/en_contact", json={"column_groups": groups + [{"api_name": "customerNumber"}]}).status_code == 422
    client.patch("/api/entities/en_contact", json={"column_groups": groups + [{"api_name": "phoneNumbers", "many": True}]})
    assert any("phoneNumbers has no columns" in i["message"] for i in client.get("/api/validate").json())
    # removing the group puts its columns back at the top level
    client.patch("/api/entities/en_contact", json={"column_groups": []})
    p2 = spec(client)["components"]["schemas"]["Customer"]["properties"]
    assert "bankDetails" not in p2 and "sortCode" in p2


def test_dates_in_examples_are_iso_8601(client):
    r = client.post("/api/entities/en_case/attributes", json={"business_name": "Review Date", "type": "date", "example": "31/01/2026"})
    assert r.json()["example"] == "2026-01-31"
    r = client.post("/api/entities/en_case/attributes", json={"business_name": "Reviewed At", "type": "date-time",
                                                             "example": "31/01/2026 09:30"})
    assert r.json()["example"] == "2026-01-31T09:30:00Z"
    doc = spec(client)
    ex = doc["components"]["schemas"]["Case"]["example"]
    assert ex["reviewDate"] == "2026-01-31" and ex["reviewedAt"] == "2026-01-31T09:30:00Z"
    # every date and date-time example in the definition is ISO 8601
    import datetime as dt_, re as re_
    def walk(o):
        if isinstance(o, dict):
            if o.get("format") in ("date", "date-time") and "example" in o:
                v = o["example"]
                if o["format"] == "date":
                    dt_.date.fromisoformat(v)
                else:
                    assert re_.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})$", v), v
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(doc)


def test_regex_patterns_on_columns(client):
    cid = next(a for a in model(client)["entities"][0]["attributes"] if a["key"])
    assert client.patch("/api/attributes/" + cid["id"], json={"pattern": "^[0-9A-Z]+-[0-9A-Z]+$"}).status_code == 200
    client.patch("/api/attributes/at_k_num", json={"pattern": "^CS-[0-9]{8}$", "filterable": True})
    doc = spec(client)
    validate_spec(doc)
    assert doc["components"]["schemas"]["Customer"]["properties"][cid["api_name"]]["pattern"] == "^[0-9A-Z]+-[0-9A-Z]+$"
    pp = doc["paths"]["/cm/v1/customers/{customerId}"]["parameters"][0]
    assert pp["schema"]["pattern"] == "^[0-9A-Z]+-[0-9A-Z]+$"                       # path parameter too
    q = {p["name"]: p for p in doc["paths"]["/cm/v1/cases"]["get"]["parameters"] if p.get("in") == "query"}
    assert q["caseNumber"]["schema"]["pattern"] == "^CS-[0-9]{8}$"                    # and query parameters
    # an invalid regex is refused; an example that does not match, or a pattern on a number, is flagged
    assert client.patch("/api/attributes/at_k_num", json={"pattern": "^CS-[0-9{8}$"}).status_code == 422
    client.patch("/api/attributes/at_k_num", json={"example": "CS-12"})
    assert any("does not match the pattern" in i["message"] for i in client.get("/api/validate").json())
    client.patch("/api/attributes/at_k_liab", json={"pattern": "^[0-9]+$"})
    assert any("only applies to text columns" in i["message"] for i in client.get("/api/validate").json())
    assert "pattern" not in spec(client)["components"]["schemas"]["Case"]["properties"]["outstandingBalance"]
    # mapping sheet keeps it
    x = client.get("/api/export/mapping.xlsx").content
    client.patch("/api/attributes/" + cid["id"], json={"pattern": ""})
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    assert spec(client)["components"]["schemas"]["Customer"]["properties"][cid["api_name"]]["pattern"] == "^[0-9A-Z]+-[0-9A-Z]+$"


# -- request bodies of selected fields --------------------------------------------

def test_put_withdrawal_with_only_a_reason(client):
    client.post("/api/examples/agreements?apply=true")
    chg = next(e for e in model(client)["entities"] if e["id"] == "en_sc_chg")
    reason = next(a for a in chg["attributes"] if a["api_name"] == "reason")
    wd = next(a for a in chg["actions"] if a["name"] == "withdrawal")
    client.patch("/api/entities/en_sc_chg", json={"plural": "agreements/changes"})
    r = client.patch("/api/actions/" + wd["id"], json={"method": "PUT", "request_entity_id": "en_sc_chg", "request_variant": "fields",
                                                       "request_fields": [reason["id"]], "request_required": [reason["id"]]})
    assert r.status_code == 200, r.text
    doc = spec(client)
    validate_spec(doc)
    op = doc["paths"]["/cm/v1/agreements/changes/{changeId}/withdrawal"]["put"]
    name = op["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    body = doc["components"]["schemas"][name]
    assert name == "AgreementChangeWithdrawalRequest"
    assert list(body["properties"]) == ["reason"] and body["required"] == ["reason"]
    # the same for a standard PUT through its Edit dialog settings
    client.patch("/api/entities/en_sc_chg", json={"op_overrides": {"update": {"request_mode": "fields",
                                                                              "request_fields": [reason["id"]]}}})
    doc = spec(client)
    put = doc["paths"]["/cm/v1/agreements/changes/{changeId}"]["put"]
    nm = put["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    assert list(doc["components"]["schemas"][nm]["properties"]) == ["reason"] and "required" not in doc["components"]["schemas"][nm]
    # mapping sheet: Request form 'fields: reason*'
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    ws = wb["Operations"]
    hdr = [c.value for c in ws[1]]
    cells = {r[hdr.index("Operation")].value: r[hdr.index("Request form")].value for r in ws.iter_rows(min_row=2)
             if r[hdr.index("Schema name")].value == "AgreementChange" and r[hdr.index("Kind")].value in ("operation", "action")}
    assert cells["withdrawal"] == "fields: reason*" and cells["update"] == "fields: reason"
    client.patch("/api/actions/" + wd["id"], json={"request_variant": "create", "request_fields": []})
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    doc2 = spec(client)
    op2 = doc2["paths"]["/cm/v1/agreements/changes/{changeId}/withdrawal"]["put"]
    n2 = op2["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    assert doc2["components"]["schemas"][n2]["required"] == ["reason"]
    # nothing ticked is flagged
    act = next(a for e in model(client)["entities"] if e["schema_name"] == "AgreementChange"
               for a in e["actions"] if a["name"] == "withdrawal")
    client.patch("/api/actions/" + act["id"], json={"request_fields": [], "request_required": []})
    assert any("none are ticked" in i["message"] for i in client.get("/api/validate").json())


# -- notes: a resource with a polymorphic parent; enum code style; contract-only OpenAPI ----

def test_notes_example(client):
    prev = client.post("/api/examples/notes?apply=false").json()
    assert prev["applied"] is False and prev["errors"] == 0 and prev["added"] == ["Note"]
    assert client.post("/api/examples/notes?apply=true").json()["applied"] is True
    m = model(client)
    ns = next(n for n in m["namespaces"] if n["key"] == "notes")
    assert (ns["base_path"], ns["enum_style"], ns["source_in_openapi"]) == ("/v1", "kebab-case", False)
    doc = spec(client, "notes")
    validate_spec(doc)
    assert set(doc["paths"]) == {"/v1/notes", "/v1/notes/{noteId}"}
    lst = doc["paths"]["/v1/notes"]["get"]
    q = {p["name"]: p for p in lst["parameters"] if p.get("in") == "query"}
    assert list(q)[:4] == ["parentType", "parentId", "parentScopeType", "parentScopeId"]
    assert q["parentType"]["required"] and q["parentId"]["required"] and not q["parentScopeId"]["required"]
    assert q["parentType"]["schema"]["enum"] == ["service-request", "contact", "activity"]
    assert "createdAtFrom" in q and "limit" in q
    sc = doc["components"]["schemas"]
    parent = sc["Note"]["properties"]["parent"]
    assert parent["type"] == "object" and parent["required"] == ["type", "id"]
    assert list(parent["properties"]) == ["type", "id", "scopeType", "scopeId"]
    assert "parent" in sc["NoteCreate"]["required"]
    # PATCH can't move a note: only noteType, text and private
    patch_ref = doc["paths"]["/v1/notes/{noteId}"]["patch"]["requestBody"]["content"]
    sch = next(iter(patch_ref.values()))["schema"]["$ref"].split("/")[-1]
    assert set(sc[sch]["properties"]) == {"noteType", "text", "private"}
    # the published contract carries no Siebel mapping; the full view does
    text = json.dumps(doc)
    assert "x-source" not in text and "S_NOTE" not in text and "x-transform" not in text
    full = client.get("/api/openapi/notes.json?view=full").json()
    note = full["components"]["schemas"]["Note"]
    assert note["x-source-variants"]["selected-by"] == "parent.type"
    assert note["x-source-variants"]["variants"]["contact"]["x-source-object"] == "Contact Note"
    assert note["properties"]["text"]["x-source-variants"]["contact"]["x-source-column"] == "S_NOTE_CON.NOTE"
    assert note["properties"]["parent"]["properties"]["scopeId"]["x-source-variants"]["contact"] == {"x-not-applicable": True}
    # no enum style warnings: kebab-case is the namespace's style
    assert not [i for i in client.get("/api/validate").json() if "Enum codes" in i["message"]]
    # adding it again changes nothing
    again = client.post("/api/examples/notes?apply=false").json()
    assert again["added"] == []


def test_enum_code_style_per_namespace(client):
    client.post("/api/entities/en_case/attributes", json={"business_name": "Channel", "type": "enum",
                "enum": [{"code": "", "label": "Web form", "source_value": ""}, {"code": "", "label": "Phone", "source_value": ""}]})
    attr = next(a for a in model(client)["entities"][1]["attributes"] if a["api_name"] == "channel")
    assert [v["code"] for v in attr["enum"]] == ["WEB_FORM", "PHONE"]          # default UPPER_SNAKE
    assert client.patch("/api/namespaces/ns_cm", json={"enum_style": "kebab-case"}).status_code == 200
    msgs = [i["message"] for i in client.get("/api/validate").json() if "Enum codes" in i["message"]]
    assert msgs and all("kebab-case" in x for x in msgs)                         # existing codes are flagged
    client.post("/api/entities/en_case/attributes", json={"business_name": "Region", "type": "enum",
                "enum": [{"code": "", "label": "North East", "source_value": ""}]})
    attr = next(a for a in model(client)["entities"][1]["attributes"] if a["api_name"] == "region")
    assert attr["enum"][0]["code"] == "north-east"                             # new codes follow the style
    assert client.patch("/api/namespaces/ns_cm", json={"enum_style": "Title Case"}).status_code == 422
    # mapping sheet round trip
    x = client.get("/api/export/mapping.xlsx").content
    client.patch("/api/namespaces/ns_cm", json={"enum_style": "UPPER_SNAKE"})
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    assert model(client)["namespaces"][0]["enum_style"] == "kebab-case"


def test_contract_only_openapi(client):
    full = client.get("/api/openapi/cm.json").json()
    assert "x-source-object" in full["components"]["schemas"]["Customer"]
    c = client.get("/api/openapi/cm.json?view=contract").json()
    validate_spec(c)
    text = json.dumps(c)
    for ext in ("x-source", "x-dd-attribute", "x-transform", "x-system-column", "x-data-item"):
        assert ext not in text, ext
    assert c["components"]["schemas"]["Customer"]["properties"]["firstName"]["x-business-name"] == "First Name"
    assert c["paths"].keys() == full["paths"].keys()
    assert client.get("/api/openapi/cm.json?view=nope").status_code == 422
    # the namespace setting decides what is published, also in the bundle and the mapping sheet
    client.patch("/api/namespaces/ns_cm", json={"source_in_openapi": False})
    assert "x-source-object" not in client.get("/api/openapi/cm.yaml").text
    assert "x-source-object" in client.get("/api/openapi/cm.yaml?view=full").text
    with zipfile.ZipFile(io.BytesIO(client.get("/api/export/bundle.zip").content)) as z:
        assert "x-source-object" not in z.read("openapi/cm-openapi.yaml").decode()
    x = client.get("/api/export/mapping.xlsx").content
    client.patch("/api/namespaces/ns_cm", json={"source_in_openapi": True})
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    assert model(client)["namespaces"][0]["source_in_openapi"] is False


def test_source_variants(client):
    """One entity, sources chosen by a column's value: a mapping per variant, inherited where blank."""
    r = client.patch("/api/entities/en_case", json={"varies_by": "at_k_svc", "source_variants": [
        {"id": "sv_std", "code": "STANDARD", "source_object": "HLS Case", "location_prefix": "S_CASE"}]})
    assert r.status_code == 200, r.text
    iss = [i["message"] for i in client.get("/api/validate").json() if i["object_id"] == "en_case"]
    assert any("'PREMIUM' has no source variant" in x for x in iss)
    client.patch("/api/entities/en_case", json={"source_variants": [
        {"id": "sv_std", "code": "STANDARD", "source_object": "HLS Case", "location_prefix": "S_CASE"},
        {"id": "sv_prem", "code": "PREMIUM", "source_object": "Premium Case", "location_prefix": "S_CASE_PREM"}]})
    client.patch("/api/attributes/at_k_num", json={"variant_sources": {
        "sv_prem": {"source_field": "Premium Case No", "source_column": "S_CASE_PREM.CASE_NO"}}})
    client.patch("/api/attributes/at_k_stg", json={"variant_sources": {"sv_prem": {"not_applicable": True}}})
    client.post("/api/entities/en_case/attributes", json={"business_name": "Priority"})
    msgs = {i["where"]: i["message"] for i in client.get("/api/validate").json() if i["where"].startswith("Case")}
    assert msgs["Case › Priority"] == "No BC field mapped for STANDARD, PREMIUM."
    assert "Case › Case Stage" not in msgs                       # not applicable for PREMIUM, mapped for STANDARD
    full = spec(client)["components"]["schemas"]["Case"]
    assert full["x-source-variants"]["variants"]["PREMIUM"]["x-source-object"] == "Premium Case"
    num = full["properties"]["caseNumber"]["x-source-variants"]
    assert num["STANDARD"] == {"x-source-field": "Case Num", "x-source-column": "S_CASE.CASE_NUM",
                               "x-dd-attribute": "DD-CAS-002"}                           # all inherited
    assert num["PREMIUM"] == {"x-source-field": "Premium Case No", "x-source-column": "S_CASE_PREM.CASE_NO",
                              "x-dd-attribute": "DD-CAS-002"}                            # DD reference inherited
    assert full["properties"]["stage"]["x-source-variants"]["PREMIUM"] == {"x-not-applicable": True}
    assert "x-source-field" not in full["properties"]["caseNumber"]       # the per-variant mapping replaces it
    assert "x-source-variants" not in client.get("/api/openapi/cm.json?view=contract").text
    validate_spec(spec(client))
    # the dictionary is checked per variant
    _load_dictionary(client)
    dmsgs = [i["message"] for i in client.get("/api/validate").json() if i["where"].startswith("Case")]
    assert any("'Premium Case' is not in the dictionary (source variant PREMIUM)" in x for x in dmsgs)
    # mapping sheet round trip
    before = spec(client)
    x = client.get("/api/export/mapping.xlsx").content
    wb = load_workbook(io.BytesIO(x))
    assert {"Source variants", "Variant mapping"} <= set(wb.sheetnames)
    rows = list(wb["Variant mapping"].iter_rows(min_row=3, values_only=True))
    hdr = [c.value for c in wb["Variant mapping"][2]]
    row = next(r for r in rows if r[hdr.index("Value")] == "PREMIUM" and r[hdr.index("Column")] == "caseNumber")
    assert row[hdr.index("Effective location")] == "S_CASE_PREM.CASE_NO"
    client.patch("/api/entities/en_case", json={"source_variants": [], "varies_by": ""})
    assert client.post("/api/import/mapping?apply=true", content=x).status_code == 200
    after = spec(client)
    for d in (before, after):
        d["info"].pop("x-model-fingerprint")
    assert before == after
    # a variant code that is not a value, and a varies-by column that is not an enum (ids change on import)
    case = next(e for e in model(client)["entities"] if e["schema_name"] == "Case")
    ids = {a["api_name"]: a["id"] for a in case["attributes"]}
    assert client.patch("/api/entities/" + case["id"], json={"source_variants": [{"code": "GOLD", "source_object": "X"}]}).status_code == 200
    assert any("'GOLD' is not a value" in i["message"] for i in client.get("/api/validate").json())
    client.patch("/api/entities/" + case["id"], json={"varies_by": ids["caseNumber"]})
    assert any("must be an enum" in i["message"] for i in client.get("/api/validate").json())
    # removing a variant drops its overrides
    client.patch("/api/entities/" + case["id"], json={"varies_by": ids["serviceType"], "source_variants": []})
    case = next(e for e in model(client)["entities"] if e["schema_name"] == "Case")
    assert all(a["variant_sources"] == {} for a in case["attributes"])
