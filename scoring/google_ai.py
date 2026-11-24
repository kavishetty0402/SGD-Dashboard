"""
google_ai.py
Talks to Gemma 4 31B through Google AI Studio (free), instead of Ollama.
It is the same model you used through Ollama, so the scores stay comparable.

The three scripts that use AI (score_articles.py, build_index.py,
weekly_digest.py) all call ask_gemma() from here.

SETUP (once)
  1. Go to https://aistudio.google.com/apikey and sign in with a Google account.
  2. Click "Create API key". Copy the key.
  3. In the sgd-project folder, make a text file called google_api_key.txt
     and paste the key into it (just the key, nothing else). Save.
     Never share this file or upload it to GitHub.

CHECK IT WORKS
  python3 google_ai.py
  It lists the Gemma models your key can use and sends one test question.

FREE-TIER LIMITS (as of late 2026, can change)
  About 30 requests a minute, about 16,000 tokens a minute, and a daily cap.
  This file waits between calls to stay under the per-minute limits.
  If the daily cap is hit, it stops cleanly: run the same command tomorrow
  and it carries on where it stopped.
"""
import json
import os
import sys
import time

import requests

MODEL = "gemma-4-31b-it"
# If the 31B model is busy twice in a row, ask this smaller Gemma 4 model
# instead. Articles it scores are tagged "[26B]" so you can re-score them
# with 31B later (python3 score_articles.py rescore fallback).
# Set FALLBACK_MODEL = "" to switch the fallback off.
FALLBACK_MODEL = "gemma-4-26b-a4b-it"
BUSY_BEFORE_FALLBACK = 2
KEY_FILE = "google_api_key.txt"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/"

SECONDS_BETWEEN_CALLS = 8        # stays under ~30 requests a minute, with room
TOKENS_PER_MINUTE = 15000        # a little under the free cap of ~16,000
RETRIES_AFTER_LIMIT = 3          # how often to wait a minute and try again
BUSY_RETRIES = 8                 # if Google is "busy" (503), wait 10s, 20s, ... up to 8 times

_next_allowed_time = 0.0
last_model_used = MODEL          # score_articles.py reads this to tag fallback scores


class DailyLimitReached(Exception):
    """Google's free daily cap is used up. Try again tomorrow."""
    pass


def read_key():
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "r") as f:
            key = f.read().strip()
        if key != "":
            return key
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if key != "":
        return key
    raise Exception("No Google API key. Make " + KEY_FILE + " (see the top of google_ai.py).")


def wait_your_turn(estimated_tokens):
    """Sleep so we never go over the per-minute limits."""
    global _next_allowed_time
    now = time.time()
    if now < _next_allowed_time:
        time.sleep(_next_allowed_time - now)
    gap = SECONDS_BETWEEN_CALLS
    token_gap = estimated_tokens * 60.0 / TOKENS_PER_MINUTE
    if token_gap > gap:
        gap = token_gap
    _next_allowed_time = time.time() + gap


def ask_gemma(instructions, text):
    """Send instructions + text to Gemma. Returns the reply as plain text.
    (Gemma on Google takes everything as one message, so the two are joined.)"""
    key = read_key()
    prompt = instructions + "\n\n---\n\n" + text
    estimated_tokens = len(prompt) / 4 + 300     # roughly 4 characters per token
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0},
    }
    global last_model_used
    model = MODEL
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

    attempt = 0
    busy_count = 0
    while True:
        url = BASE_URL + "models/" + model + ":generateContent"
        wait_your_turn(estimated_tokens)
        response = requests.post(url, headers=headers, json=body, timeout=300)

        if response.status_code == 200:
            break

        message = response.text[:300]
        if response.status_code == 429:
            if "PerDay" in message or "per day" in message.lower():
                raise DailyLimitReached("Google's free daily limit is used up.")
            attempt = attempt + 1
            if attempt > RETRIES_AFTER_LIMIT:
                raise Exception("Google kept saying 'too many requests': " + message)
            print("    (Google says slow down: waiting 60 seconds)")
            time.sleep(60)
            continue
        if response.status_code in [500, 502, 503, 504]:
            # Google's servers are busy ("high demand").
            busy_count = busy_count + 1
            if model == MODEL and FALLBACK_MODEL != "" and busy_count >= BUSY_BEFORE_FALLBACK:
                print("    (31B is busy: asking " + FALLBACK_MODEL + " instead)")
                model = FALLBACK_MODEL
                continue
            attempt = attempt + 1
            if attempt > BUSY_RETRIES:
                raise Exception("Google's servers stayed busy, try again later: " + message[:120])
            wait = 10 * attempt          # 10s, 20s, 30s, ... (short waits = more tries per hour)
            print("    (Google is busy: waiting %d seconds, try %d of %d)" % (wait, attempt, BUSY_RETRIES))
            time.sleep(wait)
            continue
        if response.status_code == 404:
            raise Exception("Model " + model + " not found. Run python3 google_ai.py "
                            "to see which Gemma models your key can use.")
        raise Exception("Google error " + str(response.status_code) + ": " + message)

    last_model_used = model
    reply = response.json()
    candidates = reply.get("candidates", [])
    if len(candidates) == 0:
        raise Exception("Google returned no answer: " + json.dumps(reply)[:200])
    parts = candidates[0].get("content", {}).get("parts", [])
    answer = ""
    for part in parts:
        if part.get("thought"):          # skip any 'thinking' text
            continue
        answer = answer + part.get("text", "")
    return answer


def list_gemma_models():
    key = read_key()
    response = requests.get(BASE_URL + "models", headers={"x-goog-api-key": key},
                            params={"pageSize": 1000}, timeout=60)
    if response.status_code != 200:
        raise Exception("Google error " + str(response.status_code) + ": " + response.text[:300])
    names = []
    for model in response.json().get("models", []):
        name = model.get("name", "").replace("models/", "")
        if "gemma" in name:
            names.append(name)
    return names


# ------------------------------------------------- python3 google_ai.py
if __name__ == "__main__":
    print("1. Reading your key...")
    try:
        read_key()
        print("   found it")
    except Exception as error:
        print("   " + str(error))
        sys.exit(1)

    print("2. Gemma models your key can use:")
    try:
        names = list_gemma_models()
        for name in names:
            print("   " + name)
        if MODEL not in names:
            print("   NOTE: " + MODEL + " is not in this list. Pick the closest Gemma 4 31B")
            print("   name above and put it in MODEL at the top of google_ai.py.")
    except Exception as error:
        print("   " + str(error))
        sys.exit(1)

    print("3. Test question to " + MODEL + "...")
    try:
        answer = ask_gemma("Reply with only a JSON object.", 'Return {"ok": true}')
        print("   reply: " + answer.strip()[:200])
        print("All good. You can score articles now.")
    except Exception as error:
        print("   " + str(error))
