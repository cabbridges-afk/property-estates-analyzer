#!/usr/bin/env python3
"""Local-only server for the estates analyzer's AI RAP research panel."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8765"))
MAX_BODY_BYTES = 80_000
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
COURTLISTENER_URL = "https://www.courtlistener.com/api/rest/v4/search/"
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4.1-mini")


def json_request(url: str, payload: dict, headers: dict, timeout: int = 45) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, headers: dict, timeout: int = 20) -> dict:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_json_object(content: str) -> dict:
    """Accept a plain JSON object or a JSON code-fence without trusting prose."""
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The model did not return the required JSON object.")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("The model response was not a JSON object.")
    return data


def ai_prompt(hypothetical: str, classification: list[dict]) -> list[dict]:
    deterministic = "\n".join(
        f"- {item.get('party', '?')}: {item.get('label', '?')}"
        for item in classification
        if isinstance(item, dict)
    )
    system = """You are a U.S. property-law study assistant. Analyze ONLY the
common-law Rule Against Perpetuities (RAP) question presented by the hypothetical.
Do not give legal advice. Treat the deterministic estate classifications supplied by
the application as tentative inputs rather than authorities. Be concise, name the
future interest(s) that need RAP review, identify a validating life if one exists,
and explain whether the interest is valid, void, or fact-dependent under the common-
law lives-in-being-plus-21-years rule. Do not invent authorities or case citations.
Return exactly one JSON object with this schema:
{"conclusion":"likely valid|likely void|fact-dependent|not implicated",
"summary":"2-4 sentences","interests":[{"interest":"string","rap_status":"valid|void|fact-dependent|not implicated","reason":"string"}],
"assumptions":["string"],"study_note":"string"}"""
    user = f"""Hypothetical:
{hypothetical}

Deterministic classification output:
{deterministic or "- No classified interests supplied."}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def normalize_memo(raw: dict) -> dict:
    allowed = {"likely valid", "likely void", "fact-dependent", "not implicated"}
    conclusion = raw.get("conclusion", "fact-dependent")
    if conclusion not in allowed:
        conclusion = "fact-dependent"
    interests = raw.get("interests", [])
    if not isinstance(interests, list):
        interests = []
    return {
        "conclusion": conclusion,
        "summary": str(raw.get("summary", "")).strip()[:4000],
        "interests": [
            {
                "interest": str(item.get("interest", "")).strip()[:300],
                "rap_status": str(item.get("rap_status", "fact-dependent")).strip()[:100],
                "reason": str(item.get("reason", "")).strip()[:1500],
            }
            for item in interests[:8]
            if isinstance(item, dict)
        ],
        "assumptions": [str(item).strip()[:500] for item in raw.get("assumptions", [])[:8]],
        "study_note": str(raw.get("study_note", "")).strip()[:1000],
    }


def courtlistener_query(hypothetical: str, classification: list[dict]) -> str:
    labels = " ".join(
        str(item.get("label", ""))
        for item in classification
        if isinstance(item, dict)
    )
    terms = "rule against perpetuities future interest " + labels + " " + hypothetical
    words = re.findall(r"[A-Za-z]{4,}", terms)
    # Preserve doctrinal terms but avoid transmitting unbounded user prose.
    return " ".join(words[:32])


def strip_html(value: str) -> str:
    return re.sub(r"<[^>]*>", "", value or "").replace("\n", " ").strip()


def normalize_cases(payload: dict) -> list[dict]:
    cases = []
    for result in payload.get("results", [])[:3]:
        if not isinstance(result, dict) or result.get("blocked"):
            continue
        citation = result.get("citation") or []
        if isinstance(citation, list):
            citation = ", ".join(str(x) for x in citation if x)
        url = result.get("absolute_url", "")
        if url.startswith("/"):
            url = "https://www.courtlistener.com" + url
        if not url.startswith("https://www.courtlistener.com/"):
            continue
        opinion_snippets = result.get("opinions") or []
        snippet = ""
        if opinion_snippets and isinstance(opinion_snippets[0], dict):
            snippet = opinion_snippets[0].get("snippet", "")
        cases.append({
            "name": str(result.get("caseNameFull") or result.get("caseName") or "Untitled opinion")[:500],
            "citation": str(citation or "Citation unavailable")[:500],
            "court": str(result.get("court") or "Court unavailable")[:300],
            "date": str(result.get("dateFiled") or "")[:50],
            "snippet": strip_html(str(snippet))[:1000],
            "url": url,
        })
    return cases


def research_cases(query: str) -> tuple[list[dict], str | None]:
    params = urllib.parse.urlencode({
        "q": query,
        "type": "o",
        "semantic": "true",
        "highlight": "on",
    })
    headers = {"Accept": "application/json"}
    token = os.environ.get("COURTLISTENER_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Token {token}"
    try:
        return normalize_cases(get_json(f"{COURTLISTENER_URL}?{params}", headers)), None
    except urllib.error.HTTPError as error:
        return [], f"CourtListener search unavailable ({error.code})."
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return [], "CourtListener search is temporarily unavailable."


def analyze_rap(payload: dict) -> dict:
    hypothetical = str(payload.get("hypothetical", "")).strip()
    classification = payload.get("classification", [])
    if not hypothetical:
        raise ValueError("A hypothetical is required.")
    if len(hypothetical) > 20_000:
        raise ValueError("The hypothetical is too long.")
    if not isinstance(classification, list):
        classification = []

    query = courtlistener_query(hypothetical, classification)
    cases, case_error = research_cases(query)
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return {
            "memo": None,
            "cases": cases,
            "case_error": case_error,
            "ai_error": "AI analysis is not configured. Start the server with OPENROUTER_API_KEY set.",
            "search_query": query,
        }

    try:
        response = json_request(
            OPENROUTER_URL,
            {
                "model": DEFAULT_MODEL,
                "temperature": 0,
                "max_tokens": 1200,
                "response_format": {"type": "json_object"},
                "messages": ai_prompt(hypothetical, classification),
            },
            {
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": "http://127.0.0.1",
                "X-OpenRouter-Title": "Present & Future Estates Analyzer",
            },
        )
        content = response["choices"][0]["message"]["content"]
        memo = normalize_memo(parse_json_object(content))
        return {"memo": memo, "cases": cases, "case_error": case_error, "ai_error": None, "search_query": query}
    except urllib.error.HTTPError as error:
        return {
            "memo": None, "cases": cases, "case_error": case_error,
            "ai_error": f"OpenRouter request failed ({error.code}). Check the model, key, and account limits.",
            "search_query": query,
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError, urllib.error.URLError, TimeoutError):
        return {
            "memo": None, "cases": cases, "case_error": case_error,
            "ai_error": "The AI response could not be completed or validated.",
            "search_query": query,
        }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/analyze-rap":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                raise ValueError("Invalid request size.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            self.send_json(HTTPStatus.OK, analyze_rap(payload))
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})


if __name__ == "__main__":
    print(f"Serving http://{HOST}:{PORT}/estates_analyzer.html")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
