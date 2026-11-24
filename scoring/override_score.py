"""
Correct one of the AI's scores by hand
======================================

  python3 override_score.py <article id> <field> <value>

Fields: relevance, impact, surprise (0-3), direction (hawkish / dovish /
neutral), channel, mas_source ("MAS itself" / "others").

Examples:
  python3 override_score.py 742 relevance 3
  python3 override_score.py 742 impact 2
  python3 override_score.py 31 mas_source "MAS itself"

Your corrections are saved in overrides.csv and always win over the AI's
scores, even after re-scoring. build_index.py prints how many it applied.
To undo one, run the same command with the AI's original value, or delete
that line from overrides.csv in a text editor (not Excel).

Use this sparingly, for clear mistakes on important stories: the index is
meant to be the AI's reading, corrected on average by the hand labels.
"""

import csv
import os
import sys

OVERRIDES_FILE = "overrides.csv"
FIELDS = ["relevance", "impact", "surprise", "direction", "channel", "mas_source"]


def save(article_id, field, value):
    if field not in FIELDS:
        print("Field must be one of: " + ", ".join(FIELDS))
        return
    if field in ["relevance", "impact", "surprise"]:
        if value not in ["0", "1", "2", "3"]:
            print(field + " must be 0, 1, 2 or 3")
            return

    rows = []
    if os.path.exists(OVERRIDES_FILE):
        with open(OVERRIDES_FILE, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                rows.append(row)

    # Replace an earlier override of the same field for this article
    replaced = False
    for row in rows:
        if row["id"] == article_id and row["field"] == field:
            row["value"] = value
            replaced = True
    if not replaced:
        rows.append({"id": article_id, "field": field, "value": value})

    with open(OVERRIDES_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "field", "value"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print("Saved: article " + article_id + ", " + field + " = " + value)
    print("Now run: python3 build_index.py")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 override_score.py <article id> <field> <value>")
    else:
        save(sys.argv[1], sys.argv[2], sys.argv[3])
