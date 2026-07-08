"""
Validate available models and embeddings on the Databricks-hosted Anthropic serving endpoint.

Usage:
    pip install requests
    export DATABRICKS_TOKEN="your-token-here"
    python validate_models.py
"""

import os
import sys
import json
import requests
from datetime import datetime

BASE_URL = "https://adb-3890477425381403.3.azuredatabricks.net/serving-endpoints"
ENDPOINT = f"{BASE_URL}/anthropic"

# Known Anthropic model IDs to probe
CANDIDATE_MODELS = [
    "claude-opus-4-8",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-5-20250514",
    "claude-sonnet-4-20250514",
    "claude-haiku-4-5-20251001",
    "claude-fable-5",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
]

CANDIDATE_EMBEDDING_MODELS = [
    "voyage-3",
    "voyage-3-lite",
    "voyage-code-3",
    "voyage-large-2",
    "voyage-2",
]


def get_token():
    token = os.environ.get("DATABRICKS_TOKEN") or os.environ.get("DATABRICKS_PAT")
    if not token:
        print("ERROR: Set DATABRICKS_TOKEN or DATABRICKS_PAT environment variable.")
        print("  You can generate one at: https://adb-3890477425381403.3.azuredatabricks.net/#setting/account")
        sys.exit(1)
    return token


def get_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def check_endpoint_status(token):
    """Check if the serving endpoint is reachable and get its metadata."""
    print("=" * 60)
    print("ENDPOINT STATUS CHECK")
    print("=" * 60)
    headers = get_headers(token)

    # Try the Databricks serving-endpoints REST API (workspace API)
    workspace_url = BASE_URL.rsplit("/serving-endpoints", 1)[0]
    api_url = f"{workspace_url}/api/2.0/serving-endpoints/anthropic"
    resp = requests.get(api_url, headers=headers, timeout=30)
    if resp.status_code == 200:
        data = resp.json()
        print(f"  Status: ONLINE")
        print(f"  Endpoint: {ENDPOINT}")
        if "state" in data:
            print(f"  State: {json.dumps(data['state'], indent=4)}")
        if "served_entities" in data:
            print(f"  Served entities:")
            for entity in data["served_entities"]:
                print(f"    - {entity.get('name', '?')}: {entity.get('external_model', {}).get('name', '?')}")
        return data
    else:
        print(f"  API status: {resp.status_code}")
        print(f"  Response: {resp.text[:500]}")

    # Fallback: try GET on the endpoint itself
    resp2 = requests.get(ENDPOINT, headers=headers, timeout=30)
    print(f"  Direct GET status: {resp2.status_code}")
    if resp2.status_code == 200:
        return resp2.json()
    print(f"  Direct GET response: {resp2.text[:300]}")
    return None


def probe_chat_model(model_id, token):
    """Send a minimal chat completion request trying multiple API paths and payload styles."""
    base_headers = get_headers(token)
    errors_collected = []

    # Databricks external model endpoints with provider "anthropic"
    # The supported native API surface is /messages (NOT /v1/messages)
    # See: https://docs.databricks.com/aws/en/machine-learning/model-serving/
    attempts = [
        # 1. Anthropic native: /serving-endpoints/{name}/messages (correct Databricks path)
        {
            "label": "anthropic_messages_with_model",
            "url": f"{ENDPOINT}/messages",
            "headers": {**base_headers, "anthropic-version": "2023-06-01"},
            "payload": {
                "model": model_id,
                "messages": [{"role": "user", "content": "Say ok"}],
                "max_tokens": 5,
            },
        },
        # 2. Same but without model (endpoint itself routes to a model)
        {
            "label": "anthropic_messages_no_model",
            "url": f"{ENDPOINT}/messages",
            "headers": {**base_headers, "anthropic-version": "2023-06-01"},
            "payload": {
                "messages": [{"role": "user", "content": "Say ok"}],
                "max_tokens": 5,
            },
        },
        # 3. With /v1/ prefix (some newer Databricks versions)
        {
            "label": "anthropic_v1_messages",
            "url": f"{ENDPOINT}/v1/messages",
            "headers": {**base_headers, "anthropic-version": "2023-06-01"},
            "payload": {
                "model": model_id,
                "messages": [{"role": "user", "content": "Say ok"}],
                "max_tokens": 5,
            },
        },
        # 4. Databricks Foundation Model API style (ai/v1 prefix at workspace root)
        {
            "label": "foundation_model_api",
            "url": f"{BASE_URL.rsplit('/serving-endpoints', 1)[0]}/serving-endpoints/databricks-claude-opus-4-6/invocations",
            "headers": base_headers,
            "payload": {
                "messages": [{"role": "user", "content": "Say ok"}],
                "max_tokens": 5,
            },
        },
        # 5. Per-model endpoint name pattern (databricks-{model})
        {
            "label": f"per_model_endpoint_{model_id}",
            "url": f"{BASE_URL}/{model_id}/messages",
            "headers": {**base_headers, "anthropic-version": "2023-06-01"},
            "payload": {
                "model": model_id,
                "messages": [{"role": "user", "content": "Say ok"}],
                "max_tokens": 5,
            },
        },
    ]

    for attempt in attempts:
        try:
            resp = requests.post(
                attempt["url"], headers=attempt["headers"], json=attempt["payload"], timeout=30
            )
            if resp.status_code == 200:
                data = resp.json()
                usage = data.get("usage", {})
                return {
                    "available": True,
                    "api_style": attempt["label"],
                    "url_used": attempt["url"],
                    "model_returned": data.get("model", model_id),
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                }
            else:
                err_text = resp.text[:300]
                errors_collected.append({
                    "label": attempt["label"],
                    "url": attempt["url"],
                    "status_code": resp.status_code,
                    "response": err_text,
                })
        except requests.exceptions.Timeout:
            errors_collected.append({"label": attempt["label"], "error": "timeout"})
        except requests.exceptions.RequestException as e:
            errors_collected.append({"label": attempt["label"], "error": str(e)})

    return {"available": False, "attempts": errors_collected}


def probe_embedding_model(model_id, token):
    """Send a minimal embedding request trying multiple API paths."""
    base_headers = get_headers(token)
    errors_collected = []

    attempts = [
        {
            "label": "embeddings_no_prefix",
            "url": f"{ENDPOINT}/embeddings",
            "headers": base_headers,
            "payload": {"model": model_id, "input": ["hello"]},
        },
        {
            "label": "embeddings_v1",
            "url": f"{ENDPOINT}/v1/embeddings",
            "headers": base_headers,
            "payload": {"model": model_id, "input": ["hello"]},
        },
        {
            "label": "per_model_embeddings_endpoint",
            "url": f"{BASE_URL}/{model_id}/embeddings",
            "headers": base_headers,
            "payload": {"input": ["hello"]},
        },
    ]

    for attempt in attempts:
        try:
            resp = requests.post(
                attempt["url"], headers=attempt["headers"], json=attempt["payload"], timeout=30
            )
            if resp.status_code == 200:
                data = resp.json()
                embedding = data.get("data", [{}])[0].get("embedding", [])
                return {
                    "available": True,
                    "api_style": attempt["label"],
                    "url_used": attempt["url"],
                    "dimensions": len(embedding) if embedding else "unknown",
                    "model_returned": data.get("model", model_id),
                }
            else:
                errors_collected.append({
                    "label": attempt["label"],
                    "url": attempt["url"],
                    "status_code": resp.status_code,
                    "response": resp.text[:300],
                })
        except requests.exceptions.Timeout:
            errors_collected.append({"label": attempt["label"], "error": "timeout"})
        except requests.exceptions.RequestException as e:
            errors_collected.append({"label": attempt["label"], "error": str(e)})

    return {"available": False, "attempts": errors_collected}


def main():
    print(f"Databricks Anthropic Endpoint Validator")
    print(f"Run at: {datetime.now().isoformat()}")
    print(f"Target: {ENDPOINT}")
    print()

    token = get_token()

    # 1. Check endpoint status
    endpoint_info = check_endpoint_status(token)
    print()

    # 2. Probe chat/completion models
    print("=" * 60)
    print("CHAT/COMPLETION MODELS")
    print("=" * 60)
    available_models = []
    unavailable_models = []

    # First, do a diagnostic probe with just one model to find the right path
    print("  [Diagnostic] Probing path discovery with first model...")
    diag_result = probe_chat_model(CANDIDATE_MODELS[0], token)
    if not diag_result["available"] and "attempts" in diag_result:
        print("  [Diagnostic] All path attempts failed. Details:")
        for att in diag_result["attempts"]:
            print(f"    {att['label']} ({att.get('status_code', '?')}): {att.get('response', att.get('error', '?'))[:150]}")
        print()

    for model_id in CANDIDATE_MODELS:
        result = probe_chat_model(model_id, token)
        status = "OK" if result["available"] else "UNAVAILABLE"
        print(f"  [{status:11}] {model_id}")
        if result["available"]:
            available_models.append({"model": model_id, **result})
        else:
            unavailable_models.append({"model": model_id, **result})
            if "attempts" in result:
                # Show first error only to keep output readable
                first = result["attempts"][0] if result["attempts"] else {}
                print(f"               -> {first.get('status_code', '?')}: {first.get('response', first.get('error', '?'))[:120]}")

    print()

    # 3. Probe embedding models
    print("=" * 60)
    print("EMBEDDING MODELS")
    print("=" * 60)
    available_embeddings = []
    unavailable_embeddings = []

    for model_id in CANDIDATE_EMBEDDING_MODELS:
        result = probe_embedding_model(model_id, token)
        status = "OK" if result["available"] else "UNAVAILABLE"
        print(f"  [{status:11}] {model_id}")
        if result["available"]:
            available_embeddings.append({"model": model_id, **result})
            print(f"               -> dimensions: {result['dimensions']}")
        else:
            unavailable_embeddings.append({"model": model_id, **result})
            if "attempts" in result:
                first = result["attempts"][0] if result["attempts"] else {}
                print(f"               -> {first.get('status_code', '?')}: {first.get('response', first.get('error', '?'))[:120]}")

    print()

    # 4. Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Chat models available:      {len(available_models)}/{len(CANDIDATE_MODELS)}")
    print(f"  Embedding models available: {len(available_embeddings)}/{len(CANDIDATE_EMBEDDING_MODELS)}")
    print()

    if available_models:
        print("  Available chat models:")
        for m in available_models:
            print(f"    - {m['model']} (returned as: {m.get('model_returned', '?')})")
    print()

    if available_embeddings:
        print("  Available embedding models:")
        for m in available_embeddings:
            print(f"    - {m['model']} (dimensions: {m.get('dimensions', '?')})")
    print()

    # 5. Write results to JSON
    output = {
        "endpoint": ENDPOINT,
        "timestamp": datetime.now().isoformat(),
        "endpoint_info": endpoint_info,
        "chat_models": {
            "available": available_models,
            "unavailable": unavailable_models,
        },
        "embedding_models": {
            "available": available_embeddings,
            "unavailable": unavailable_embeddings,
        },
    }

    output_file = "endpoint_validation_results.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"  Full results saved to: {output_file}")


if __name__ == "__main__":
    main()
