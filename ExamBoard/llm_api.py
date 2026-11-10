"""Send structured requests to Ollama and read JSON responses."""
import json
import urllib.request

API_URL = "http://127.0.0.1:11434"
MODEL = "llama3.1:8b"
TIMEOUT = 660
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 1024}


def response_schema(types):
    """Constrain generation to one typed JSON object with no extra fields."""
    fields = {
        "selected": {"type": "string", "enum": ["a1", "a2"]},
        "approve_correction": {"type": "boolean"},
        "rationale": {"type": "string", "minLength": 1},
        "evidence_quotes": {"type": "array", "minItems": 1,
                            "items": {"type": "string", "minLength": 1}},
        "limitations": {"type": "string"},
        "reason_by_type": {
            "type": "object", "additionalProperties": False,
            "properties": {kind: {"type": "string", "minLength": 1} for kind in types},
            "required": list(types)},
    }
    return {"type": "object", "additionalProperties": False,
            "properties": fields, "required": list(fields)}


def build_request(prompt, packet):
    return {"model": MODEL, "stream": False, "format": response_schema(packet["case_disagreement_types"]), "options": dict(OPTIONS),
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": json.dumps(packet, ensure_ascii=False)}]}


def request_json(route, payload=None, timeout=TIMEOUT):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        API_URL + route, data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def complete(payload):
    return request_json("/api/chat", payload)


def parse_response(response):
    return json.loads(response["message"]["content"])
