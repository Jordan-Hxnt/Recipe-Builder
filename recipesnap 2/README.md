# RecipeSnap

Snap ingredients. Get recipes. Powered by Gemini (free tier).

## Get your free API key

1. Go to https://aistudio.google.com/apikey
2. Click "Create API key"
3. Copy it - that's all you need

## Run locally

```bash
export GEMINI_API_KEY=your_key_here
pip install -r requirements.txt
python app.py
```

Open http://localhost:8080

## Deploy to Render (free)

1. Push this folder to a GitHub repo
2. Go to https://dashboard.render.com/new
3. Select "Web Service", connect your repo
4. Render auto-detects render.yaml
5. Add GEMINI_API_KEY in the Environment tab
6. Deploy

You get a URL like `https://recipesnap-xxxx.onrender.com`.
Share that link. Works on any phone browser.

## Deploy to Fly.io (alternative)

```bash
cp fly.toml.example fly.toml
fly launch
fly secrets set GEMINI_API_KEY=your_key_here
fly deploy
```

## Project structure

```
recipesnap/
  app.py              Python/Flask backend (Gemini API)
  templates/
    index.html         Full frontend (HTML/CSS/JS)
  requirements.txt     Python deps
  Dockerfile           Container config
  render.yaml          Render one-click deploy
  fly.toml.example     Fly.io config template
```

## How it works

1. User uploads ingredient photos from phone camera or gallery
2. Flask sends images to Gemini for ingredient identification
3. User edits the list, adds dietary preferences
4. Flask asks Gemini for 4-5 recipes using different subsets
5. Results show which ingredients each recipe uses

## Cost

$0. Gemini 2.0 Flash free tier includes 15 requests/min
and 1 million tokens/day. More than enough for a prototype.
