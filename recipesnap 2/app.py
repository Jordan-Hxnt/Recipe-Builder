import os
import sys
import json
import base64
import logging
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

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-2.0-flash"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_KEY}"

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def call_gemini(contents_parts):
    """Call Gemini REST API directly - avoids SDK version issues."""
    import urllib.request

    payload = json.dumps({"contents": [{"parts": contents_parts}]}).encode()
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error(f"Gemini API HTTP {e.code}: {body}")
        raise RuntimeError(f"Gemini API error {e.code}: {body[:200]}")
    except Exception as e:
        log.error(f"Gemini request failed: {e}")
        raise

    # Extract text from response
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        log.error(f"Unexpected Gemini response structure: {json.dumps(data)[:500]}")
        raise RuntimeError(f"Could not parse Gemini response: {e}")

    return text.strip()


def parse_json_response(raw):
    """Clean and parse JSON from LLM output."""
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)


# ── Routes ──

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    """Test that Gemini API is reachable and key is valid."""
    if not GEMINI_KEY:
        return jsonify({"status": "error", "message": "GEMINI_API_KEY not set"}), 500
    try:
        text = call_gemini([{"text": "Reply with just the word: OK"}])
        return jsonify({"status": "ok", "reply": text})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/identify", methods=["POST"])
def identify():
    """Accept uploaded images, return identified ingredients."""
    files = request.files.getlist("images")
    if not files:
        return jsonify({"error": "No images uploaded"}), 400

    parts = []
    for f in files:
        if f.content_type not in ALLOWED_TYPES:
            log.warning(f"Skipping file with type: {f.content_type}")
            continue
        raw_bytes = f.read()
        b64 = base64.standard_b64encode(raw_bytes).decode()
        parts.append({
            "inline_data": {
                "mime_type": f.content_type,
                "data": b64,
            }
        })
        log.info(f"Added image: {f.filename} ({f.content_type}, {len(raw_bytes)} bytes)")

    if not parts:
        return jsonify({"error": "No valid images found"}), 400

    parts.append({
        "text": (
            "Identify every food ingredient visible in these images. "
            "Be specific (e.g. 'red bell pepper' not just 'pepper'). "
            "Respond ONLY with a JSON array of strings, no markdown fences. "
            'Example: ["chicken breast", "garlic", "olive oil"]'
        )
    })

    try:
        raw = call_gemini(parts)
        log.info(f"Gemini identify response: {raw[:200]}")
        ingredients = parse_json_response(raw)
        return jsonify({"ingredients": ingredients})
    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed: {e}, raw: {raw[:300]}")
        return jsonify({"error": f"Could not parse ingredient list: {e}"}), 500
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

Respond ONLY with a JSON array (no markdown fences):
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

    try:
        raw = call_gemini([{"text": prompt}])
        log.info(f"Gemini recipes response: {raw[:200]}")
        result = parse_json_response(raw)
        return jsonify({"recipes": result})
    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed: {e}, raw: {raw[:300]}")
        return jsonify({"error": f"Could not parse recipes: {e}"}), 500
    except Exception as e:
        log.error(f"Recipes failed: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    if not GEMINI_KEY:
        log.warning("GEMINI_API_KEY is not set! API calls will fail.")
    else:
        log.info(f"Gemini API key loaded ({GEMINI_KEY[:8]}...)")
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
