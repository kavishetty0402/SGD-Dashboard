"""
mistral_ai.py
Talks to Mistral's models through Mistral's free API tier.

WHY MISTRAL
  Google's free Gemma servers were too often "busy". Mistral's free tier
  (late 2026): about 1 request per second, 500,000 tokens a minute and
  about 1 billion tokens a month. Our whole pool is about 10 million tokens,
  so there is plenty of room.

  NOTE: it is a different model from Gemma, so its scores go into new files
  (ai_scores_..._mistral-large.csv). Check it against your hand labels
  first (python3 score_articles.py test), then re-score the pool with it.
  Mistral's free tier may use what you send for training: fine for public
  news articles, but mention it to Liang since the codebook goes with them.

SETUP (once)
  1. Go to https://console.mistral.ai, sign up (free), choose the free plan.
  2. Open "API keys", create a key, copy it.
  3. In the sgd-project folder, make a plain text file called
     mistral_api_key.txt with only the key in it. Never upload it anywhere.

CHECK IT WORKS
  python3 mistral_ai.py
"""
import os
import sys
import time

import requests

MODEL = "mistral-medium-latest"       # if your plan doesn't allow it, use "mistral-small-
SCORES_NAME = "mistral-medium"         # goes into the score file names
KEY_FILE = "mistral_api_key.txt"
URL = "https://api.mistral.ai/v1/chat/completions"

SECONDS_BETWEEN_CALLS = 0.3           # free tier: about 1 request per second
RETRIES = 6

FALLBACK_MODEL = ""                   # not used for Mistral (kept so other scripts work)
last_model_used = MODEL

_next_allowed_time = 0.0


class DailyLimitReached(Exception):
    """The free allowance is used up. Try again later."""
    pass


def read_key():
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "r") as f:
            key = f.read().strip()
        if key != "":
            return key
    key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if key != "":
        return key
    raise Exception("No Mistral API key. Make " + KEY_FILE + " (see the top of mistral_ai.py).")


def wait_your_turn():
    global _next_allowed_time
    now = time.time()
    if now < _next_allowed_time:
        time.sleep(_next_allowed_time - now)
    _next_allowed_time = time.time() + SECONDS_BETWEEN_CALLS


def ask_gemma(instructions, text):
    """Same name as in google_ai.py so the other scripts need no changes.
    Sends instructions (as the system message) and text. Returns the reply."""
    key = read_key()
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
	"top_p" : 1
    }
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    attempt = 0
    while True:
        wait_your_turn()
        response = requests.post(URL, headers=headers, json=body, timeout=300)
        if response.status_code == 200:
            break
        message = response.text[:300]
        attempt = attempt + 1
        if response.status_code == 429:
            lowered = message.lower()
            if "month" in lowered or "quota" in lowered:
                raise DailyLimitReached("Mistral's free allowance is used up: " + message[:120])
            if attempt > RETRIES:
                raise Exception("Mistral kept saying 'too many requests': " + message)
            print("    (Mistral says slow down: waiting %d seconds)" % (5 * attempt))
            time.sleep(5 * attempt)
            continue
        if response.status_code in [500, 502, 503, 504]:
            if attempt > RETRIES:
                raise Exception("Mistral's servers stayed busy: " + message[:120])
            print("    (Mistral is busy: waiting %d seconds)" % (10 * attempt))
            time.sleep(10 * attempt)
            continue
        if response.status_code in [401, 403]:
            raise Exception("Mistral rejected the key or the model (" + str(response.status_code)
                            + "). Check mistral_api_key.txt, or set MODEL to mistral-small-latest. "
                            + message[:150])
        raise Exception("Mistral error " + str(response.status_code) + ": " + message)

    reply = response.json()
    choices = reply.get("choices", [])
    if len(choices) == 0:
        raise Exception("Mistral returned no answer: " + str(reply)[:200])
    content = choices[0].get("message", {}).get("content", "")
    # some models return a list of parts instead of plain text
    if isinstance(content, list):
        text_out = ""
        for part in content:
            if isinstance(part, dict):
                text_out = text_out + part.get("text", "")
        content = text_out
    return content


# ------------------------------------------------ python3 mistral_ai.py
if __name__ == "__main__":
    print("1. Reading your key...")
    try:
        key = read_key()
        print("   found it")
    except Exception as error:
        print("   " + str(error))
        sys.exit(1)

    print("2. Models your key can use (Mistral's own):")
    response = requests.get("https://api.mistral.ai/v1/models",
                            headers={"Authorization": "Bearer " + key}, timeout=60)
    if response.status_code != 200:
        print("   error " + str(response.status_code) + ": " + response.text[:200])
        sys.exit(1)
    names = []
    for model in response.json().get("data", []):
        name = model.get("id", "")
        if "latest" in name:
            names.append(name)
    names.sort()
    for name in names:
        print("   " + name)

    print("3. Test question to " + MODEL + "...")
    try:
        answer = ask_gemma("Reply with only a JSON object.", 'Return {"ok": true}')
        print("   reply: " + answer.strip()[:200])
        print("All good.")
    except Exception as error:
        print("   " + str(error))
