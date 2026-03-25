# RecipeSnap

Snap ingredients. Get recipes. Free.

## Get your API key (free, no credit card)

1. Go to https://openrouter.ai and sign up
2. Go to https://openrouter.ai/keys and create a key
3. That's it. Free models cost $0.

## Run locally

```bash
export OPENROUTER_API_KEY=sk-or-...
pip install -r requirements.txt
python app.py
```

Open http://localhost:8080

## Deploy to Render (free)

1. Push this folder to a GitHub repo
2. Go to https://dashboard.render.com/new
3. Select "Web Service", connect your repo
4. Render auto-detects render.yaml
5. Add OPENROUTER_API_KEY in the Environment tab
6. Deploy

Share the URL. Works on any phone browser.

## Deploy to Fly.io

```bash
cp fly.toml.example fly.toml
fly launch
fly secrets set OPENROUTER_API_KEY=sk-or-...
fly deploy
```

## How it works

1. User uploads ingredient photos from phone camera
2. Flask sends images to Qwen VL 72B (free) for identification
3. User edits the list, adds dietary preferences
4. Flask asks a free model for 4-5 recipes using different subsets
5. Results show which ingredients each recipe uses

## Cost

$0. OpenRouter free tier: 20 req/min, 200 req/day.
No credit card required. Vision models included.
