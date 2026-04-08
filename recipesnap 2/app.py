import os
import sys
import json
import base64
import logging
import urllib.request
from io import BytesIO
from flask import Flask, render_template, request, jsonify

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
VISION_MODEL = "openrouter/free"
TEXT_MODEL = "openrouter/free"
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# Try to import Pillow for server-side compression (optional)
try:
    from PIL import Image
    HAS_PILLOW = True
    log.info("Pillow available - server-side image compression enabled")
except ImportError:
    HAS_PILLOW = False
    log.info("Pillow not available - using raw images")


def compress_image(raw_bytes, mime_type, max_dim=1024, quality=70):
    """Compress image to reduce API payload size. Returns (b64, mime)."""
    if HAS_PILLOW:
        try:
            img = Image.open(BytesIO(raw_bytes))
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"
        except Exception as e:
            log.warning(f"Compression failed, using raw: {e}")
    return base64.standard_b64encode(raw_bytes).decode(), mime_type


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
        log.error(f"OpenRouter HTTP {e.code}: {body[:500]}")
        raise RuntimeError(f"API error {e.code}: {body[:200]}")
    except Exception as e:
        log.error(f"Request failed: {e}")
        raise

    try:
        text = data["choices"][0]["message"]["content"]
        model_used = data.get("model", "unknown")
        log.info(f"Model: {model_used}")
    except (KeyError, IndexError):
        log.error(f"Bad response: {json.dumps(data)[:500]}")
        raise RuntimeError("Could not parse API response")

    if not text:
        raise RuntimeError("Model returned empty response. Try again.")

    return text.strip()


def call_with_retry(model, messages, retries=2):
    last_err = None
    for attempt in range(retries):
        try:
            return call_openrouter(model, messages)
        except Exception as e:
            last_err = e
            log.warning(f"Attempt {attempt + 1} failed: {e}")
    raise last_err


def parse_json_response(raw):
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]") + 1
    if start >= 0 and end > start:
        cleaned = cleaned[start:end]
    return json.loads(cleaned)


# ── Routes ──

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
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
    files = request.files.getlist("images")
    if not files:
        return jsonify({"error": "No images uploaded"}), 400

    content = []
    for f in files:
        if f.content_type not in ALLOWED_TYPES:
            continue
        raw_bytes = f.read()
        b64, mime = compress_image(raw_bytes, f.content_type)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"}
        })
        log.info(f"Image: {f.filename} ({len(raw_bytes)}B -> {len(b64)}B b64)")

    if not content:
        return jsonify({"error": "No valid images"}), 400

    content.append({
        "type": "text",
        "text": (
            "Identify every food ingredient visible in these images. "
            "Be specific (e.g. 'red bell pepper' not just 'pepper'). "
            "Respond ONLY with a JSON array of strings, no explanation. "
            'Example: ["chicken breast", "garlic", "olive oil"]'
        )
    })

    try:
        raw = call_with_retry(VISION_MODEL, [{"role": "user", "content": content}])
        log.info(f"Identify: {raw[:300]}")
        return jsonify({"ingredients": parse_json_response(raw)})
    except json.JSONDecodeError:
        return jsonify({"error": "Could not parse ingredient list"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/recipes", methods=["POST"])
def recipes():
    data = request.get_json()
    ingredients = data.get("ingredients", [])
    dietary = data.get("dietary", "")

    if not ingredients:
        return jsonify({"error": "No ingredients provided"}), 400

    prompt = f"""I have these ingredients: {json.dumps(ingredients)}
{f"Dietary preferences: {dietary}" if dietary else ""}

Suggest 4-5 recipes. Rules:
- Each uses a DIFFERENT SUBSET of ingredients - not all of them.
- Mix: one quick, one involved, one creative/unexpected.
- Assume pantry staples (salt, pepper, oil, butter, flour, sugar, spices) are available.

Respond ONLY with a JSON array (no markdown, no extra text):
[{{"name":"...","emoji":"🍳","time":"25 min","difficulty":"Easy","vibe":"Quick weeknight dinner","description":"One sentence hook","uses":["ingredient1"],"extra_needed":["non-pantry extras needed"],"ingredients":["1 lb chicken, sliced"],"steps":["Step 1"]}}]"""

    try:
        raw = call_with_retry(TEXT_MODEL, [{"role": "user", "content": prompt}])
        log.info(f"Recipes: {raw[:300]}")
        return jsonify({"recipes": parse_json_response(raw)})
    except json.JSONDecodeError:
        return jsonify({"error": "Could not parse recipes"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    if not OPENROUTER_KEY:
        log.warning("OPENROUTER_API_KEY not set!")
    else:
        log.info(f"Key loaded ({OPENROUTER_KEY[:8]}...)")
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
