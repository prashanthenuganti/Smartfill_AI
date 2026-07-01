# SmartFill AI — Railway Deployment Checklist

## 0. Files in this folder
- `Procfile` — tells Railway how to start the FastAPI app
- `railway.toml` — explicit build/start config (belt-and-suspenders with Procfile)
- `.env.example` — variables to set in Railway's dashboard (not committed to git)

Copy all three into the root of `smartfill_AI` (same level as `backend/`).

## 1. Pre-flight on your repo
- [ ] Make sure `requirements.txt` exists at the repo root and is up to date
      (run `pip freeze > requirements.txt` locally inside your venv, then trim
      anything dev-only like pytest if present).
- [ ] Confirm the app entrypoint is exactly `backend.app.main:app` (matches Procfile).
      If your module path differs, edit `Procfile` and `railway.toml` to match.
- [ ] Push the repo to GitHub if it isn't already there — Railway deploys from a
      GitHub repo (or via `railway up` from CLI, but GitHub is simpler for updates).

## 2. CORS — required for the Chrome extension to call the backend
Your `backend/app/main.py` needs `CORSMiddleware` allowing the extension's origin.
Add (or update) something like:

```python
from fastapi.middleware.cors import CORSMiddleware
import os

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
```

You won't know the extension's `chrome-extension://<id>` origin until you load it
once (unpacked or via the Web Store) — come back and set `ALLOWED_ORIGINS` after step 5.

## 3. Shared API key gate (recommended before exposing publicly)
Simple dependency you can add to `routes.py`:

```python
from fastapi import Header, HTTPException
import os

async def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != os.getenv("SMARTFILL_API_KEY"):
        raise HTTPException(status_code=401, detail="Invalid API key")
```

Then add `dependencies=[Depends(verify_api_key)]` to the router or individual
endpoints you want gated (at minimum the upload/extract endpoints).
The extension will need to send this same key as a header on every fetch call
— see step 6.

## 4. Create the Railway project
- [ ] Go to railway.app -> New Project -> Deploy from GitHub repo -> select `smartfill_AI`
- [ ] Railway auto-detects Python via Nixpacks; the `Procfile`/`railway.toml` here
      override the start command to be explicit.
- [ ] In the service's **Variables** tab, paste in everything from `.env.example`
      with real values (API keys, `SMARTFILL_API_KEY`, etc). Leave
      `ALLOWED_ORIGINS` as a placeholder for now.
- [ ] Deploy. Railway gives you a `*.up.railway.app` HTTPS domain automatically —
      no separate TLS setup needed, which is what the Chrome extension requires
      (extensions can't call plain `http://` from content scripts on most pages).

## 5. Verify the backend is live
- [ ] Hit `https://<your-app>.up.railway.app/docs` (FastAPI's auto Swagger UI) to
      confirm it's reachable.
- [ ] Test one real endpoint (e.g. an upload) with `curl` or the Swagger UI,
      including the `X-API-Key` header if you added the gate in step 3.

## 6. Point the Chrome extension at the new backend
In `chrome-extension/popup.js` (and anywhere else a base URL is hardcoded,
e.g. `content.js` or `background.js`), replace:

```js
const BACKEND_URL = "http://127.0.0.1:8000";
```

with:

```js
const BACKEND_URL = "https://<your-app>.up.railway.app";
```

If you added the API key gate, also add the header to every fetch call:

```js
fetch(`${BACKEND_URL}/api/v1/...`, {
  headers: { "X-API-Key": "<the SMARTFILL_API_KEY value>" }
})
```

(For a pilot with a handful of trusted operators, hardcoding the key in the
extension is an acceptable shortcut — just know it's visible to anyone who
unpacks the extension. Fine for now, revisit before wider distribution.)

## 7. Close the CORS loop
- [ ] Load the extension unpacked (`chrome://extensions` -> Developer mode ->
      Load unpacked) and note its ID, e.g. `abcdefghijklmnop...`.
- [ ] Go back to Railway -> Variables -> set
      `ALLOWED_ORIGINS=chrome-extension://abcdefghijklmnop...`
      (comma-separate if you'll also test from a regular browser tab during dev).
- [ ] Redeploy (Railway redeploys automatically on variable change, or trigger
      manually).

## 8. End-to-end pilot test
- [ ] Upload a real (or test) document through the extension/review flow.
- [ ] Confirm extraction, review page, and autofill on ssc.gov.in all work
      against the live Railway backend instead of localhost.
- [ ] Check Railway's logs/metrics tab to sanity-check response times and that
      Gemini/Claude calls are succeeding.

## 9. Distribution to pilot operators
- [ ] First 2-3 testers: share the extension folder directly, have them
      "Load unpacked" — free, fastest to iterate.
- [ ] Beyond that: pay the one-time $5 Chrome Web Store developer fee and
      publish (can be unlisted/private if you don't want public discovery yet).

## Not covered here (intentionally deferred per earlier discussion)
- DPDP Act 2023 compliance — flagged as needed before scaling past trusted
  pilot operators, not blocking for this initial deploy.
- Angular custom-dropdown autofill fix — unrelated to deployment, can ship
  pilot with this as a known limitation.
