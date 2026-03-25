import os
import sys
import json
import base64
import logging
import urllib.request
from flask import Flask, render_template, request, jsonify

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# Free router auto-picks the best free model that supports vision
VISION_MODEL = "openrouter/free"
TEXT_MODEL = "openrouter/free"

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def call_openrouter(model, messages):
    """Call OpenRouter's OpenAI-compatible API."""
    payload = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "messages": messages,
    }).encode()

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "HTTP-Referer": "https://recipesnap.onrender.com",
            "X-Title": "RecipeSnap",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error(f"OpenRouter API HTTP {e.code}: {body[:500]}")
        raise RuntimeError(f"API error {e.code}: {body[:200]}")
    except Exception as e:
        log.error(f"OpenRouter request failed: {e}")
        raise

    try:
        text = data["choices"][0]["message"]["content"]
        model_used = data.get("model", "unknown")
        log.info(f"Response from model: {model_used}")
    except (KeyError, IndexError):
        log.error(f"Unexpected response: {json.dumps(data)[:500]}")
        raise RuntimeError("Could not parse API response")

    if not text:
        log.error(f"Empty response from {data.get('model', 'unknown')}")
        raise RuntimeError("Model returned an empty response. Try again.")

    return text.strip()


def parse_json_response(raw):
    """Clean and parse JSON from LLM output."""
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    # Sometimes models wrap JSON in extra text - find the array
    start = cleaned.find("[")
    end = cleaned.rfind("]") + 1
    if start >= 0 and end > start:
        cleaned = cleaned[start:end]
    return json.loads(cleaned)


def call_with_retry(model, messages, retries=2):
    """Call OpenRouter with retries for flaky free models."""
    last_err = None
    for attempt in range(retries):
        try:
            return call_openrouter(model, messages)
        except Exception as e:
            last_err = e
            log.warning(f"Attempt {attempt + 1} failed: {e}")
    raise last_err


# ── Routes ──

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    """Test that API is reachable and key works."""
    if not OPENROUTER_KEY:
        return jsonify({"status": "error", "message": "OPENROUTER_API_KEY not set"}), 500
    try:
        text = call_with_retry(TEXT_MODEL, [
            {"role": "user", "content": "Reply with just the word: OK"}
        ])
        return jsonify({"status": "ok", "reply": text})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/identify", methods=["POST"])
def identify():
    """Accept uploaded images, return identified ingredients."""
    files = request.files.getlist("images")
    if not files:
        return jsonify({"error": "No images uploaded"}), 400

    content = []
    for f in files:
        if f.content_type not in ALLOWED_TYPES:
            log.warning(f"Skipping file: {f.content_type}")
            continue
        raw_bytes = f.read()
        b64 = base64.standard_b64encode(raw_bytes).decode()
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{f.content_type};base64,{b64}"
            }
        })
        log.info(f"Added image: {f.filename} ({f.content_type}, {len(raw_bytes)} bytes)")

    if not content:
        return jsonify({"error": "No valid images found"}), 400

    content.append({
        "type": "text",
        "text": (
            "Identify every food ingredient visible in these images. "
            "Be specific (e.g. 'red bell pepper' not just 'pepper'). "
            "Respond ONLY with a JSON array of strings, no explanation, no markdown fences. "
            'Example: ["chicken breast", "garlic", "olive oil"]'
        )
    })

    messages = [{"role": "user", "content": content}]

    try:
        raw = call_with_retry(VISION_MODEL, messages)
        log.info(f"Identify response: {raw[:300]}")
        ingredients = parse_json_response(raw)
        return jsonify({"ingredients": ingredients})
    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed: {e}")
        return jsonify({"error": f"Could not parse ingredient list"}), 500
    except Exception as e:
        log.error(f"Identify failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/recipes", methods=["POST"])
def recipes():
    """Generate recipe suggestions from ingredients list."""
    data = request.get_json()
    ingredients = data.get("ingredients", [])
    dietary = data.get("dietary", "")

    if not ingredients:
        return jsonify({"error": "No ingredients provided"}), 400

    prompt = f"""I have these ingredients available: {json.dumps(ingredients)}
{f"Dietary preferences: {dietary}" if dietary else ""}

Suggest 4-5 different recipes. Important rules:
- Each recipe should use a DIFFERENT SUBSET of the ingredients. Not every recipe needs all of them.
- Include a mix: a quick option, something more involved, and a creative/unexpected idea.
- Assume common pantry staples (salt, pepper, oil, butter, flour, sugar, basic spices) are available.
- Mark which of the user's ingredients each recipe actually uses.

Respond ONLY with a JSON array (no markdown fences, no extra text):
[
  {{
    "name": "Recipe Name",
    "emoji": "🍳",
    "time": "25 min",
    "difficulty": "Easy",
    "vibe": "Quick weeknight dinner",
    "description": "One sentence hook about this dish",
    "uses": ["ingredient1", "ingredient2"],
    "extra_needed": ["anything not in their list that is not a basic pantry staple"],
    "ingredients": ["1 lb chicken breast, sliced", "3 cloves garlic, minced"],
    "steps": ["Step 1 instruction", "Step 2 instruction"]
  }}
]"""

    messages = [{"role": "user", "content": prompt}]

    try:
        raw = call_with_retry(TEXT_MODEL, messages)
        log.info(f"Recipes response: {raw[:300]}")
        result = parse_json_response(raw)
        return jsonify({"recipes": result})
    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed: {e}")
        return jsonify({"error": "Could not parse recipes"}), 500
    except Exception as e:
        log.error(f"Recipes failed: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    if not OPENROUTER_KEY:
        log.warning("OPENROUTER_API_KEY is not set! API calls will fail.")
    else:
        log.info(f"OpenRouter key loaded ({OPENROUTER_KEY[:8]}...)")
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
