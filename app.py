import os
import sys
import json
import base64
import logging
import urllib.request
from io import BytesIO
from flask import Flask, render_template, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
VISION_MODEL = "openrouter/free"
TEXT_MODEL = "openrouter/free"
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


def compress_image(raw_bytes, mime_type, max_dim=800, quality=65):
    if HAS_PILLOW:
        try:
            img = Image.open(BytesIO(raw_bytes))
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"
        except Exception:
            pass
    return base64.standard_b64encode(raw_bytes).decode(), mime_type


def call_openrouter(model, messages):
    payload = json.dumps({"model": model, "max_tokens": 2048, "messages": messages}).encode()
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
        log.error(f"HTTP {e.code}: {body[:500]}")
        raise RuntimeError(f"API error {e.code}: {body[:200]}")
    except Exception as e:
        log.error(f"Request failed: {e}")
        raise

    try:
        text = data["choices"][0]["message"]["content"]
        log.info(f"Model: {data.get('model','?')}")
    except (KeyError, IndexError):
        log.error(f"Bad response: {json.dumps(data)[:500]}")
        raise RuntimeError("Could not parse response")

    if not text:
        raise RuntimeError("Empty response. Try again.")
    return text.strip()


def call_with_retry(model, messages, retries=2):
    last = None
    for i in range(retries):
        try:
            return call_openrouter(model, messages)
        except Exception as e:
            last = e
            log.warning(f"Attempt {i+1} failed: {e}")
    raise last


def parse_json(raw):
    """Aggressively clean and parse JSON from LLM output."""
    c = raw.strip()
    # Strip markdown fences
    c = c.replace("```json", "").replace("```", "").strip()
    # Find the JSON array
    s, e = c.find("["), c.rfind("]") + 1
    if s >= 0 and e > s:
        c = c[s:e]
    # Fix trailing commas before ] or }
    import re
    c = re.sub(r',\s*([}\]])', r'\1', c)
    # Try parsing
    try:
        return json.loads(c)
    except json.JSONDecodeError:
        pass
    # Try fixing single quotes
    try:
        return json.loads(c.replace("'", '"'))
    except json.JSONDecodeError:
        pass
    raise json.JSONDecodeError("Could not parse", c, 0)


def repair_json(broken, model):
    """Ask the model to fix its own broken JSON."""
    log.info("Attempting JSON repair via model")
    prompt = (
        "The following text was supposed to be a valid JSON array but has errors. "
        "Fix it and return ONLY the corrected JSON array, nothing else:\n\n" + broken[:3000]
    )
    try:
        fixed = call_openrouter(model, [{"role": "user", "content": prompt}])
        return parse_json(fixed)
    except Exception as e:
        log.error(f"JSON repair failed: {e}")
        raise


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    if not OPENROUTER_KEY:
        return jsonify({"status": "error", "message": "OPENROUTER_API_KEY not set"}), 500
    try:
        t = call_with_retry(TEXT_MODEL, [{"role": "user", "content": "Say OK"}])
        return jsonify({"status": "ok", "reply": t})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/identify", methods=["POST"])
def identify():
    files = request.files.getlist("images")
    if not files:
        return jsonify({"error": "No images"}), 400

    content = []
    for f in files:
        if f.content_type not in ALLOWED_TYPES:
            continue
        b64, mime = compress_image(f.read(), f.content_type)
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

    if not content:
        return jsonify({"error": "No valid images"}), 400

    content.append({"type": "text", "text":
        'List every food ingredient in these images as a JSON array of strings. Be specific. '
        'No explanation, no markdown. Example: ["chicken breast","garlic","olive oil"]'
    })

    try:
        raw = call_with_retry(VISION_MODEL, [{"role": "user", "content": content}])
        try:
            return jsonify({"ingredients": parse_json(raw)})
        except json.JSONDecodeError:
            return jsonify({"ingredients": repair_json(raw, TEXT_MODEL)})
    except json.JSONDecodeError:
        return jsonify({"error": "Could not parse ingredients"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/recipes", methods=["POST"])
def recipes():
    data = request.get_json()
    ings = data.get("ingredients", [])
    diet = data.get("dietary", "")
    if not ings:
        return jsonify({"error": "No ingredients"}), 400

    prompt = f"""Ingredients: {json.dumps(ings)}
{f"Diet: {diet}" if diet else ""}
Give me 3 recipes. Each uses a DIFFERENT subset - not all ingredients. One quick, one creative, one hearty. Assume basic pantry staples available.
JSON array only, no markdown:
[{{"name":"...","emoji":"🍳","time":"20 min","difficulty":"Easy","vibe":"Quick & easy","description":"One line","uses":["from their list"],"extra_needed":["non-pantry extras"],"ingredients":["measured amounts"],"steps":["concise steps"]}}]"""

    try:
        raw = call_with_retry(TEXT_MODEL, [{"role": "user", "content": prompt}])
        try:
            return jsonify({"recipes": parse_json(raw)})
        except json.JSONDecodeError:
            return jsonify({"recipes": repair_json(raw, TEXT_MODEL)})
    except json.JSONDecodeError:
        return jsonify({"error": "Could not parse recipes"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    if not OPENROUTER_KEY:
        log.warning("OPENROUTER_API_KEY not set!")
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
