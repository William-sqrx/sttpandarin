# Chinesely TTS webapp

Password-gated web UI that wraps Alibaba DashScope `qwen-tts`. Three features:

1. **Words → MP3 folder**: upload a `.txt` file (one word/phrase per line), get a ZIP of `<word>.mp3` files.
2. **HSK Exam Excel → audio pack**: upload an `.xlsx` following the HSK listening layout (5 papers × 20 rows, parts 1–4) and an optional `Exam Instructions` sheet. Returns a ZIP organized as `Paper N/PartX/Q##.mp3` plus `Instructions/I##.mp3`.
3. **HSK Browser**: pick an HSK level and lesson from MongoDB (`HelloGuru.newlessons`), play each word's audio, edit pinyin/meaning, and regenerate audio. Writes back to `newlessons.newWords[].audioBlob` and syncs matching docs in the flat `words` collection. Requires `MONGODB_URI`.

All TTS settings (voices, speed, pauses, repetition counts, model, throttle, API key override) are exposed in the UI.

---

## Local run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export APP_PASSWORD="pick-a-password"
export DASHSCOPE_API_KEY="sk-..."
export MONGODB_URI="mongodb+srv://..."   # needed for the HSK Browser tab
# (optional) export MONGODB_DB="HelloGuru"
# (optional) export SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"

uvicorn app:app --reload --port 8000
```

Open http://localhost:8000, sign in with `APP_PASSWORD`.

You need `ffmpeg` on your PATH: `brew install ffmpeg`.

---

## Deploy to Render (free tier)

1. Push this repo to GitHub.
2. On https://dashboard.render.com → **New +** → **Blueprint** → pick the repo → Render reads [`render.yaml`](render.yaml) automatically.
3. On the Environment screen, set:
   - `APP_PASSWORD` — what users type to log in
   - `DASHSCOPE_API_KEY` — your Alibaba DashScope key (server default; UI users can also paste their own)
   - `SESSION_SECRET` is auto-generated
4. Deploy. First build pulls the Python + ffmpeg image (~3 min). After that, cold starts are ~30 s on the free plan.
5. URL will be `https://chinesely-tts.onrender.com` (or similar). Share that link + the `APP_PASSWORD` with whoever should have access.

### Notes on the free plan

- The instance sleeps after 15 min of inactivity — first request after sleep takes ~30 s.
- Long jobs (e.g. 100 items × 1 s throttle + API latency) still run in the background; the UI polls progress. But if the single request handler for the upload itself exceeds Render's ~5 min timeout, switch the upload to smaller batches, or upgrade the plan.
- All output is kept **in memory** and discarded after 1 hour. Download immediately when a job completes.

---

## Excel format (HSK exam tab)

Expected "Questions" sheet layout (matches the provided template):

| Col A | Col B     | Col C | Col D     | Col E | ... (up to 5 papers) |
|-------|-----------|-------|-----------|-------|----------------------|
| (No)  | Paper 1   |       | Paper 2   |       |                      |
| 1     | statement |       | statement |       |                      |
| ...   |           |       |           |       |                      |
| 11    | female Q  | male A| female Q  | male A|                      |
| ...   |           |       |           |       |                      |
| 16    | male stmt | fem Q | male stmt | fem Q |                      |

Row mapping:
- 1–5 → Part 1 (statements, read by female then male)
- 6–10 → Part 2 (questions, read by female then male)
- 11–15 → Part 3 (female Q + male A, played 2×)
- 16–20 → Part 4 (male statement + female question)

### Exam Instructions sheet

Optional. Tab named `Exam Instructions` or `Exam Instructions Example`. Each row's non-empty text is treated as one instruction, rendered with the female voice into `Instructions/I##.mp3`.
# sttpandarin
