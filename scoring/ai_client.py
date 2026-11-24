"""
ai_client.py
ONE switch for which AI service the scripts use.

  PROVIDER = "mistral"  ->  mistral_ai.py  (free tier, fast; scores saved as ..._mistral-large.csv)
  PROVIDER = "google"   ->  google_ai.py   (Gemma 4 31B; scores saved as ..._gemma4_31b-cloud.csv)

score_articles.py, build_index.py and weekly_digest.py all read this file,
so changing the line below switches everything at once. Each provider's
scores go into their own files, so the two never get mixed.
"""
PROVIDER = "mistral"

if PROVIDER == "google":
    import google_ai as backend
    SCORES_NAME = "gemma4:31b-cloud"
else:
    import mistral_ai as backend
    SCORES_NAME = backend.SCORES_NAME

DailyLimitReached = backend.DailyLimitReached


def ask_gemma(instructions, text):
    """Kept the old name so the other scripts did not need rewriting."""
    return backend.ask_gemma(instructions, text)
