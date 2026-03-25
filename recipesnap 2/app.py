import os
import json
from flask import Flask, render_template, request, jsonify
from google import genai
from google.genai import types

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB max upload

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
MODEL = "gemini-2.0-flash"

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/identify", methods=["POST"])
def identify():
    """Accept uploaded images, return identified ingredients."""
    files = request.files.getlist("images")
    if not files:
        return jsonify({"error": "No images uploaded"}), 400

    contents = []
    for f in files:
        if f.content_type not in ALLOWED_TYPES:
            continue
        raw_bytes = f.read()
        contents.append(
            types.Part.from_bytes(data=raw_bytes, mime_type=f.content_type)
        )

    if not contents:
        return jsonify({"error": "No valid images"}), 400

    contents.append(
        "Identify every food ingredient visible in these images. "
        "Be specific (e.g. 'red bell pepper' not just 'pepper'). "
        "Respond ONLY with a JSON array of strings, no markdown fences."
    )

    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=contents,
        )
        raw = resp.text.strip()
        ingredients = json.loads(raw.replace("```json", "").replace("```", "").strip())
        return jsonify({"ingredients": ingredients})
    except Exception as e:
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
        resp = client.models.generate_content(
            model=MODEL,
            contents=prompt,
        )
        raw = resp.text.strip()
        result = json.loads(raw.replace("```json", "").replace("```", "").strip())
        return jsonify({"recipes": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
