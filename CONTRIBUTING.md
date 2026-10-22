# Contributing

Thanks for helping. Issues and pull requests are both welcome.

## Set up

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
pytest
api-definition-studio --data ./studio-data   # http://127.0.0.1:8100/
```

## Before you open a pull request

- Run `pytest`. Every feature has a test in `tests/test_studio.py`; add one for yours.
- Keep the generated OpenAPI valid: tests check it with `openapi-spec-validator`.
- Update `README.md` when behaviour or the mapping sheet changes.
- Keep examples generic. The starter model (`sample.py`), the worked example
  (`examples.py`) and the fixtures in `tests/fixtures/` use an invented
  customer-service domain. Don't add real organisation names, internal system
  names, real data dictionaries, server URLs or personal data.

## Layout

| Path | What it is |
|---|---|
| `api_definition_studio/main.py` | FastAPI app: the studio's REST API |
| `api_definition_studio/model.py` | Pydantic model of the catalogue |
| `api_definition_studio/store.py` | Loading, defaults, migrations, saving |
| `api_definition_studio/openapi_gen.py` | OpenAPI 3.0.3 generation |
| `api_definition_studio/mapping_sheet.py` | Excel mapping sheet export and import |
| `api_definition_studio/diagram.py` | Mermaid and draw.io exports |
| `api_definition_studio/erd_import.py` | Mermaid / HTML logical model import |
| `api_definition_studio/dictionary.py` | Data dictionary upload and lookups |
| `api_definition_studio/names.py` | Name similarity and consistency checks |
| `api_definition_studio/validate.py` | The Issues page |
| `api_definition_studio/static/` | The UI (plain JavaScript, no build step) |
