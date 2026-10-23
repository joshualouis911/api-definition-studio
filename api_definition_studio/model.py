"""The design model: namespaces, entities, attributes (columns) and relationships
(linked entities). The whole model is one JSON document so it can be kept in
version control alongside the generated definitions."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

ATTRIBUTE_TYPES = ["string", "integer", "number", "boolean", "date", "date-time", "enum"]
OPERATIONS = ["list", "get", "create", "update", "patch", "delete"]
CARDINALITIES = ["one-to-one", "one-to-many", "many-to-one", "many-to-many"]
EXPOSE = ["none", "link", "embedded", "subresource"]
SUB_OPS = ["list", "get", "create", "update", "delete"]  # update = PUT
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
STATUSES = ["200", "201", "202", "204"]
ERROR_NAMES = ["BadRequest", "NotFound", "Conflict", "PreconditionFailed", "ValidationFailed"]
REQUEST_VARIANTS = ["create", "update", "full", "fields"]   # fields: only the chosen columns
RESPONSE_MODES = ["full", "key", "fields", "none"]   # what a response body contains
LIST_SHAPES = ["page", "array", "wrapped"]   # GET list body: {items, nextCursor} paged; [ … ]; {agreements: [ … ]}
HEADER_DIRECTIONS = ["request", "response", "both"]
HEADER_FORMATS = ["", "uuid", "date-time", "date"]
FILTER_MATCHES = ["equals", "in", "range", "contains"]   # ?x=  ?x=a,b  ?xFrom=&xTo=  ?x=text
GET_BY = ["path", "query"]
SECURITY = ["none", "bearer", "apiKey", "oauth2", "mutualTLS"]

INVERSE_CARDINALITY = {
    "one-to-one": "one-to-one",
    "one-to-many": "many-to-one",
    "many-to-one": "one-to-many",
    "many-to-many": "many-to-many",
}


def clean_tags(tags) -> List[str]:
    """Trim, drop blanks and repeats (ignoring case), keep the first spelling.
    A string is split on commas, so "Agreements, Case" is two tags."""
    if isinstance(tags, str):
        tags = [tags]
    out: List[str] = []
    seen = set()
    for t in (p for item in tags or [] for p in str(item).split(",")):
        t = " ".join(t.split())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def derived_from(e: Dict[str, Any], a: Dict[str, Any]) -> Tuple[bool, str]:
    """(is derived, the other business component it comes from or ""). A column is derived when it is
    flagged so, or when its own business component is not the entity's."""
    own = (a.get("source_object") or "").strip()
    other = own if own and own.lower() != (e.get("source_object") or "").strip().lower() else ""
    return bool(a.get("derived")) or bool(other), other


def to_many(cardinality: str) -> bool:
    """True when the 'to' end of a relationship holds many records."""
    return cardinality in ("one-to-many", "many-to-many")


class EnumValue(BaseModel):
    code: str
    label: str = ""
    source_value: str = ""


class Server(BaseModel):
    url: str
    description: str = ""


class Namespace(BaseModel):
    id: str = ""
    key: str
    title: str = ""
    base_path: str = ""
    version: str = "1.0.0"
    description: str = ""
    security: str = "bearer"
    owner: str = ""
    base_path_in: str = "paths"  # "paths": /cm/v1/cases ; "server": server URL ends with /cm/v1
    servers: List[Server] = Field(default_factory=list)
    enum_style: str = "UPPER_SNAKE"   # enum codes: UPPER_SNAKE, kebab-case, camelCase or lower_snake
    source_in_openapi: bool = True    # False: the published OpenAPI leaves out x-source-*, DD and other mapping


class SourceSystem(BaseModel):
    """An application data comes from: Siebel, a REST service, a database, …"""
    id: str = ""
    key: str
    name: str = ""
    kind: str = "other"
    description: str = ""
    owner: str = ""
    internal_tokens: Optional[List[str]] = None  # None: the type's default list


class Attribute(BaseModel):
    id: str = ""
    business_name: str
    api_name: str = ""
    type: str = "string"
    format: str = ""
    max_length: Optional[int] = None
    pattern: str = ""              # regular expression a text value must match, e.g. ^[0-9]+-[0-9A-Z]+$ for a Siebel row id
    key: bool = False
    required: bool = False
    read_only: bool = False
    system: bool = False  # set by the source system: never accepted on POST or PUT
    filterable: bool = False       # offered as a query parameter on this entity's GET lists
    filter_required: bool = False  # ... and callers must send it
    filter_match: str = "equals"   # equals, in, range, contains
    pii: bool = False
    money: bool = False            # an amount: sent as { "amount": <this column>, "currency": "GBP" } (integer = pence)
    group_id: str = ""             # sent inside this column group (object or list) instead of at the top level
    derived: bool = False          # not held on the entity's own record: calculated, or looked up elsewhere
                                   # (a column whose source_object is another business component is derived anyway)
    enum: List[EnumValue] = Field(default_factory=list)
    source_object: str = ""        # the column's business component when it is not the entity's (blank: the entity's)
    source_field: str = ""
    source_column: str = ""
    lov_type: str = ""
    transform: str = ""
    dd_reference: str = ""  # data dictionary attribute reference
    data_item: str = ""     # data dictionary: data item full name
    example: str = ""
    description: str = ""


class ColumnGroup(BaseModel):
    """Columns sent together under one property: an object, bankDetails: {bankName, sortCode},
    or a list of them, bankDetails: [{bankName, sortCode}, …]."""
    id: str = ""
    business_name: str = ""
    api_name: str = ""             # camelCase property name, e.g. bankDetails
    many: bool = False             # True: a list of objects
    required: bool = False
    description: str = ""


class OpOverride(BaseModel):
    """Changes to one generated operation. Blank means the default."""
    operation_id: str = ""         # blank: generated, e.g. listAgreements; or your own, e.g. findAgreementByPerson
    status: str = ""               # 200, 201, 202, 204
    request_entity_id: str = ""    # body: that entity's Create (POST/PUT) or Update (PATCH) schema
    response_entity_id: str = ""   # entity id, or "none" for no body (older form of response_mode none)
    request_mode: str = ""         # blank: the entity's create/update form; fields: only request_fields
    request_fields: List[str] = Field(default_factory=list)    # attribute ids sent in the body (request_mode fields)
    request_required: List[str] = Field(default_factory=list)  # … of which required
    response_mode: str = ""        # blank: default; full, key (row id only), fields, none; lists: page, array
    response_fields: List[str] = Field(default_factory=list)  # attribute ids, for response_mode fields
    list_property: str = ""        # lists with response_mode wrapped: the array's property name (blank: entity API name)
    query_params: Optional[List[QueryParam]] = None  # None: the columns' filter settings (lists) / none
    summary: str = ""
    description: str = ""


class QueryParam(BaseModel):
    """A column sent as a parameter of one operation: a GET filter, or on POST/PUT/PATCH/DELETE
    a value taken out of the request body and sent as a query or header parameter instead."""
    attr_id: str
    required: bool = False
    match: str = "equals"
    name: str = ""                 # blank: the column's API name
    location: str = "query"        # query or header


class Header(BaseModel):
    """A standard header applied to every matching operation."""
    id: str = ""
    name: str                      # e.g. request-id
    description: str = ""
    direction: str = "request"     # request, response, or both (sent and echoed back)
    required: bool = True
    type: str = "string"           # string or integer
    format: str = ""               # "", uuid, date-time, date
    pattern: str = ""
    example: str = ""
    methods: List[str] = Field(default_factory=list)        # empty: all methods
    namespace_ids: List[str] = Field(default_factory=list)  # empty: all namespaces


class Action(BaseModel):
    """An operation beyond create/read/update/delete, e.g. POST /changes/{id}/decision."""
    id: str = ""
    name: str                      # camelCase, e.g. decision
    method: str = "POST"
    scope: str = "item"            # item: /things/{id}/name ; collection: /things/name
    path: str = ""                 # path segment; blank: kebab-case of the name
    operation_id: str = ""         # blank: <schema><Name>, e.g. agreementChangeDecision
    summary: str = ""
    description: str = ""
    request_entity_id: str = ""    # blank: no body
    request_variant: str = "create"   # create, update, full, or fields (only request_fields)
    request_fields: List[str] = Field(default_factory=list)
    request_required: List[str] = Field(default_factory=list)
    response_entity_id: str = "self"  # self: the owning entity; blank: no body; or an entity id
    response_mode: str = ""        # blank/full: whole record; key: row id only; fields: response_fields;
                                   # a list of records: page, array or wrapped (e.g. GET /agreements/changes)
    response_fields: List[str] = Field(default_factory=list)
    list_property: str = ""        # wrapped: the array's property name (blank: the entity's API name)
    query_params: List[QueryParam] = Field(default_factory=list)
    status: str = "200"
    if_match: bool = False
    errors: List[str] = Field(default_factory=lambda: ["NotFound", "Conflict", "ValidationFailed"])


class Entity(BaseModel):
    id: str = ""
    namespace_id: str
    source_system_id: str = ""  # blank: not sourced (derived or new data)
    business_name: str
    api_name: str = ""
    plural: str = ""
    schema_name: str = ""
    description: str = ""
    source_object: str = ""
    source_io: str = ""
    operations: List[str] = Field(default_factory=lambda: ["list", "get", "create", "patch"])
    get_by: str = "path"           # path: /things/{id} ; query: /things?id=
    tags: List[str] = Field(default_factory=list)  # e.g. Agreements, Case: filter the diagram and lists
    group: str = ""                # logical group, e.g. Agreements: drawn as a frame round its entities on the diagram
    x: Optional[float] = None
    y: Optional[float] = None
    attributes: List[Attribute] = Field(default_factory=list)
    column_groups: List[ColumnGroup] = Field(default_factory=list)
    op_overrides: Dict[str, OpOverride] = Field(default_factory=dict)
    actions: List[Action] = Field(default_factory=list)


class Relationship(BaseModel):
    id: str = ""
    from_entity_id: str
    to_entity_id: str
    cardinality: str = "many-to-one"
    business_name: str = ""
    api_name: str = ""
    expose: str = "link"
    inverse_business_name: str = ""
    inverse_api_name: str = ""
    inverse_expose: str = "none"
    join_from: str = ""
    join_to: str = ""
    source_link: str = ""
    description: str = ""
    # sub-resource paths under the owning record, per side, e.g. GET/PUT /changes/{id}/declarations/{partyRole}
    sub_ops: List[str] = Field(default_factory=list)
    sub_key: str = ""              # attribute id of the target used as the path key (default: its key column)
    sub_overrides: Dict[str, OpOverride] = Field(default_factory=dict)
    inverse_sub_ops: List[str] = Field(default_factory=list)
    inverse_sub_key: str = ""
    inverse_sub_overrides: Dict[str, OpOverride] = Field(default_factory=dict)
    read_only: bool = False        # from side: set by the system, never in request bodies
    nested_create: bool = False    # from side, embedded: accept the embedded objects in POST, PUT and PATCH
    inverse_nested_create: bool = False  # the same for the other side, when it is embedded


class Settings(BaseModel):
    title: str = "API catalogue"
    organisation: str = ""
    source_system: str = "Siebel CRM"
    post_response: str = "key"     # default body of a POST response: key (row id of the created record) or full
    put_response: str = "full"     # default body of a PUT response: full, key or none
    list_response: str = "page"    # default body of a GET list: page ({items, nextCursor}), array or wrapped
    money_amount: str = "minor"    # 1.14 only: what a 'money' type became (minor: integer, decimal: number)
    money_currency: str = "GBP"    # default ISO 4217 currency of money columns, used in examples


class Model(BaseModel):
    schema_version: int = 2
    settings: Settings = Field(default_factory=Settings)
    headers: List[Header] = Field(default_factory=list)
    source_systems: List[SourceSystem] = Field(default_factory=list)
    namespaces: List[Namespace] = Field(default_factory=list)
    entities: List[Entity] = Field(default_factory=list)
    relationships: List[Relationship] = Field(default_factory=list)


# Partial-update payloads used by PATCH endpoints ---------------------------

class NamespacePatch(BaseModel):
    key: Optional[str] = None
    title: Optional[str] = None
    base_path: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    security: Optional[str] = None
    owner: Optional[str] = None
    base_path_in: Optional[str] = None
    servers: Optional[List[Server]] = None
    enum_style: Optional[str] = None
    source_in_openapi: Optional[bool] = None


class SourceSystemPatch(BaseModel):
    key: Optional[str] = None
    name: Optional[str] = None
    kind: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    internal_tokens: Optional[List[str]] = None


class EntityPatch(BaseModel):
    namespace_id: Optional[str] = None
    source_system_id: Optional[str] = None
    business_name: Optional[str] = None
    api_name: Optional[str] = None
    plural: Optional[str] = None
    schema_name: Optional[str] = None
    description: Optional[str] = None
    source_object: Optional[str] = None
    source_io: Optional[str] = None
    operations: Optional[List[str]] = None
    get_by: Optional[str] = None
    tags: Optional[List[str]] = None
    group: Optional[str] = None
    column_groups: Optional[List[ColumnGroup]] = None
    x: Optional[float] = None
    y: Optional[float] = None
    op_overrides: Optional[Dict[str, OpOverride]] = None


class DictionaryField(BaseModel):
    bc: str
    field: str


class AddFromDictionary(BaseModel):
    fields: List[DictionaryField]
    business_name_from: str = "field"   # field (BC field name) or item (data item full name)


class AttributeIds(BaseModel):
    ids: List[str]
    amount_type: str = "integer"   # integer (pence) or keep the column's type


class GroupMembers(BaseModel):
    entity_ids: List[str]


class TagMembers(BaseModel):
    entity_ids: List[str]


class TagRename(BaseModel):
    name: str


class HeaderPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    direction: Optional[str] = None
    required: Optional[bool] = None
    type: Optional[str] = None
    format: Optional[str] = None
    pattern: Optional[str] = None
    example: Optional[str] = None
    methods: Optional[List[str]] = None
    namespace_ids: Optional[List[str]] = None


class ActionPatch(BaseModel):
    name: Optional[str] = None
    method: Optional[str] = None
    scope: Optional[str] = None
    path: Optional[str] = None
    operation_id: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    request_entity_id: Optional[str] = None
    request_variant: Optional[str] = None
    request_fields: Optional[List[str]] = None
    request_required: Optional[List[str]] = None
    response_entity_id: Optional[str] = None
    response_mode: Optional[str] = None
    response_fields: Optional[List[str]] = None
    list_property: Optional[str] = None
    query_params: Optional[List[QueryParam]] = None
    status: Optional[str] = None
    if_match: Optional[bool] = None
    errors: Optional[List[str]] = None


class AttributePatch(BaseModel):
    business_name: Optional[str] = None
    api_name: Optional[str] = None
    type: Optional[str] = None
    format: Optional[str] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    key: Optional[bool] = None
    required: Optional[bool] = None
    read_only: Optional[bool] = None
    system: Optional[bool] = None
    filterable: Optional[bool] = None
    filter_required: Optional[bool] = None
    filter_match: Optional[str] = None
    pii: Optional[bool] = None
    money: Optional[bool] = None
    derived: Optional[bool] = None
    group_id: Optional[str] = None
    enum: Optional[List[EnumValue]] = None
    source_object: Optional[str] = None
    source_field: Optional[str] = None
    source_column: Optional[str] = None
    lov_type: Optional[str] = None
    transform: Optional[str] = None
    dd_reference: Optional[str] = None
    data_item: Optional[str] = None
    example: Optional[str] = None
    description: Optional[str] = None


class RelationshipPatch(BaseModel):
    from_entity_id: Optional[str] = None
    to_entity_id: Optional[str] = None
    cardinality: Optional[str] = None
    business_name: Optional[str] = None
    api_name: Optional[str] = None
    expose: Optional[str] = None
    inverse_business_name: Optional[str] = None
    inverse_api_name: Optional[str] = None
    inverse_expose: Optional[str] = None
    join_from: Optional[str] = None
    join_to: Optional[str] = None
    source_link: Optional[str] = None
    description: Optional[str] = None
    sub_ops: Optional[List[str]] = None
    sub_key: Optional[str] = None
    sub_overrides: Optional[Dict[str, OpOverride]] = None
    inverse_sub_ops: Optional[List[str]] = None
    inverse_sub_key: Optional[str] = None
    inverse_sub_overrides: Optional[Dict[str, OpOverride]] = None
    read_only: Optional[bool] = None
    nested_create: Optional[bool] = None
    inverse_nested_create: Optional[bool] = None
