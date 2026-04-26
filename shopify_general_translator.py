#!/usr/bin/env python3
"""
General Shopify CSV Translator
Translates 'Default content' into 'Translated content' using Shopify's CSV structure.
Uses Ollama with a local LLM (defaults to translategemma).
Protects links and image tags from being corrupted during translation.

Supports translation from ANY source language to ANY target language.
The source language is auto-detected from the 'Default content' column unless
overridden with --source-lang.

Usage:
    python shopify_general_translator.py input.csv
    python shopify_general_translator.py input.csv --model llama3 --source-lang Italian
    python shopify_general_translator.py input.csv --overwrite
"""

import csv
import sys
import json
import time
import re
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import timedelta

# ── Force UTF-8 stdout on Windows ────────────────────────────────────────────
import io
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Column Indices (Standard Shopify Translate & Adapt CSV) ──────────────────
# Headers: Type, Identification, Field, Locale, Market, Status, Default content, Translated content
COL_LOCALE      = 3   # D
COL_DEFAULT     = 6   # G
COL_TRANSLATED  = 7   # H
COL_VALIDATION  = 8   # I (optional — may not exist)

# ── Locale → Language Name Map ───────────────────────────────────────────────
LOCALE_MAP = {
    "en":    "English",
    "de":    "German",
    "es":    "Spanish",
    "fr":    "French",
    "it":    "Italian",
    "pl":    "Polish",
    "pt-PT": "Portuguese (Portugal)",
    "pt-BR": "Portuguese (Brazil)",
    "pt":    "Portuguese",
    "nl":    "Dutch",
    "sv":    "Swedish",
    "da":    "Danish",
    "fi":    "Finnish",
    "nb":    "Norwegian",
    "cs":    "Czech",
    "hu":    "Hungarian",
    "ro":    "Romanian",
    "sk":    "Slovak",
    "hr":    "Croatian",
    "sl":    "Slovenian",
    "bg":    "Bulgarian",
    "el":    "Greek",
    "lt":    "Lithuanian",
    "lv":    "Latvian",
    "et":    "Estonian",
    "ru":    "Russian",
    "uk":    "Ukrainian",
    "tr":    "Turkish",
    "ar":    "Arabic",
    "zh":    "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)",
    "ja":    "Japanese",
    "ko":    "Korean",
}

OLLAMA_URL  = "http://localhost:11434/api/generate"
MAX_RETRIES = 3

# ── Ollama HTTP call with retry ───────────────────────────────────────────────

def call_ollama(payload_bytes: bytes, timeout: int = 120) -> dict:
    """POST to Ollama with exponential-backoff retry."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                OLLAMA_URL,
                data=payload_bytes,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"\n[retry] Attempt {attempt + 1} failed ({exc}). Retrying in {wait}s…")
                time.sleep(wait)
    raise RuntimeError(f"Ollama call failed after {MAX_RETRIES} attempts: {last_exc}")

# ── Link and Tag Protection (Masking) ────────────────────────────────────────

def mask_content(text: str) -> tuple[str, dict]:
    """
    Replace HTML tags and URLs with stable placeholders so the LLM cannot mangle them.
    Returns (masked_text, placeholder_map).
    """
    if not text:
        return text, {}

    placeholders: dict[str, str] = {}
    counter = 0

    # 1. Shopify CDN URLs and generic https URLs
    url_pattern = re.compile(
        r'https?://[^\s"\'<>{}|\\^~\[\]`]+',
        re.IGNORECASE,
    )
    # 2. All HTML tags
    tag_pattern = re.compile(r'<[^>]+>', re.IGNORECASE)

    masked = text

    for url in sorted(set(url_pattern.findall(masked)), key=len, reverse=True):
        token = f"[[URL_{counter}]]"
        placeholders[token] = url
        masked = masked.replace(url, token)
        counter += 1

    for tag in sorted(set(tag_pattern.findall(masked)), key=len, reverse=True):
        token = f"[[TAG_{counter}]]"
        placeholders[token] = tag
        masked = masked.replace(tag, token)
        counter += 1

    return masked, placeholders


def unmask_content(masked_text: str, placeholders: dict) -> str:
    """
    Restore original tags and URLs from placeholders.
    Tries exact match first, then case-insensitive fallback.
    Logs any tokens that could not be restored.
    """
    if not masked_text:
        return masked_text

    result = masked_text
    missing: list[str] = []

    for token, original in placeholders.items():
        if token in result:
            result = result.replace(token, original)
        else:
            # Case-insensitive fallback (LLM sometimes changes case)
            new = re.sub(re.escape(token), original, result, flags=re.IGNORECASE)
            if new == result:
                missing.append(token)
            result = new

    if missing:
        print(f"\n[warn] Could not restore placeholder(s): {missing}")

    return result

# ── Text Cleanup ─────────────────────────────────────────────────────────────

def clean_quotes(text: str) -> str:
    """Replace smart/curly quotes with straight ASCII equivalents."""
    if not text:
        return text
    for smart, straight in {
        "\u201c": '"', "\u201d": '"',
        "\u2018": "'", "\u2019": "'",
    }.items():
        text = text.replace(smart, straight)
    return text


def clean_ai_garbage(text: str) -> str:
    """Strip LLM conversational preamble and wrapping quotes."""
    if not text:
        return ""
    cleaned = text.strip()
    for prefix in ("Result:", "Output:", "Translation:", "Translated:", "Here is", "Sure,", "Certainly,"):
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned.split(":", 1)[1].strip() if ":" in cleaned else cleaned.split("\n", 1)[1].strip()
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1].strip()
    return cleaned

# ── Prompt Builder ────────────────────────────────────────────────────────────

def build_prompt(masked_text: str, source_lang: str, target_lang: str) -> str:
    """
    Build a translation prompt that works for any source → target language pair,
    including cases where source and target are the same (proofread/adapt mode).
    """
    if source_lang.lower() == target_lang.lower():
        instruction = (
            f"You are a professional e-commerce copywriter.\n"
            f"The following text is in {source_lang}. "
            f"Correct any errors and ensure it reads naturally as {target_lang} "
            f"e-commerce copy. Do NOT translate — keep the same language.\n"
        )
    else:
        instruction = (
            f"You are a professional e-commerce translator for a Shopify store.\n"
            f"Translate the following text from {source_lang} into {target_lang}.\n"
        )

    return (
        f"{instruction}\n"
        f"CRITICAL RULES:\n"
        f"- Preserve all placeholders like [[URL_0]] or [[TAG_1]] exactly as written. "
        f"Do NOT translate, modify, or remove them.\n"
        f"- Return ONLY the translated/adapted text — no notes, no explanations.\n"
        f"- Preserve the original tone and HTML structure.\n\n"
        f"Text:\n{masked_text}"
    )

# ── Core Translation Function ─────────────────────────────────────────────────

def translate_one(text: str, model: str, source_lang: str, target_lang: str) -> str:
    """
    Translate a single string from source_lang to target_lang via Ollama.
    Masks links/tags before sending, restores them after.
    """
    masked_text, placeholders = mask_content(text)
    prompt = build_prompt(masked_text, source_lang, target_lang)

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 4096},
    }).encode()

    response = call_ollama(payload)
    raw = clean_ai_garbage(response.get("response", "").strip())
    return clean_quotes(unmask_content(raw, placeholders))

# ── Source Language Detection ─────────────────────────────────────────────────

def detect_source_language(model: str, sample_text: str) -> str:
    """
    Ask the LLM to identify the language of sample_text.
    Returns a plain English language name (e.g. 'Italian', 'French').
    Falls back to 'English' on failure.
    """
    prompt = (
        "Identify the language of the following text. "
        "Reply with ONLY the English name of the language (e.g. Italian, French, German). "
        "Do not add any other words.\n\n"
        f"Text:\n{sample_text[:500]}"
    )
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 1024},
    }).encode()

    try:
        response = call_ollama(payload)
        detected = response.get("response", "").strip().split("\n")[0].strip()
        # Sanity-check: should be a single word or short phrase, no punctuation
        detected = re.sub(r"[^\w\s\(\)-]", "", detected).strip()
        return detected if detected else "English"
    except Exception as exc:
        print(f"\n[warn] Language detection failed ({exc}). Defaulting to English.")
        return "English"

# ── CSV Save Helper ───────────────────────────────────────────────────────────

def save_csv(path: Path, header: list, data_rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(data_rows)

# ── Validation Helper ─────────────────────────────────────────────────────────

def get_cell(row: list, col: int, default: str = "") -> str:
    return row[col].strip() if len(row) > col else default

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="General Shopify CSV Translator — any source language, any target language.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="Input CSV file (Shopify Translate & Adapt format)")
    parser.add_argument(
        "--model", "-m",
        default="translategemma:latest",
        help="Ollama model name (default: translategemma:latest)",
    )
    parser.add_argument(
        "--source-lang", "-s",
        default=None,
        metavar="LANGUAGE",
        help=(
            "Source language of the Default content column, e.g. 'Italian', 'French'. "
            "If omitted, the language is auto-detected from the first non-empty row."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-translate rows that already have a Translated content value.",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=10,
        metavar="N",
        help="Save progress every N rows (default: 10).",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"[error] File not found: {input_path}")

    output_path = input_path.with_stem(input_path.stem + "_TRANSLATED")

    # ── Load CSV ──────────────────────────────────────────────────────────────
    print(f"[*] Reading {input_path}…")
    with open(input_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    if len(rows) < 2:
        sys.exit("[error] CSV has no data rows.")

    header = rows[0]
    data   = rows[1:]
    total  = len(data)
    print(f"[*] {total} data rows found.")

    # ── Detect / confirm source language ─────────────────────────────────────
    if args.source_lang:
        source_lang = args.source_lang.strip()
        print(f"[*] Source language (manual): {source_lang}")
    else:
        # Find first non-empty Default content cell for detection
        sample = next(
            (get_cell(r, COL_DEFAULT) for r in data if get_cell(r, COL_DEFAULT)),
            None,
        )
        if not sample:
            sys.exit("[error] No non-empty Default content cells found.")
        print(f"[*] Auto-detecting source language from first row…")
        source_lang = detect_source_language(args.model, sample)
        print(f"[*] Detected source language: {source_lang}")

    # ── Translation loop ──────────────────────────────────────────────────────
    start_time  = time.time()
    translated  = 0
    skipped     = 0
    failed      = 0

    for i, row in enumerate(data):
        locale   = get_cell(row, COL_LOCALE)
        source   = get_cell(row, COL_DEFAULT)
        existing = get_cell(row, COL_TRANSLATED)

        # Skip empty source
        if not source:
            skipped += 1
            continue

        # Skip already-translated rows unless --overwrite
        if existing and not args.overwrite:
            skipped += 1
            continue

        # Resolve target language name from locale code
        target_lang = LOCALE_MAP.get(locale, locale)  # fall back to raw locale string

        # Progress display with ETA
        elapsed   = time.time() - start_time
        rate      = translated / elapsed if elapsed > 0 and translated > 0 else None
        remaining = ((total - i - 1) / rate) if rate else 0
        eta       = str(timedelta(seconds=int(remaining))) if rate else "–"
        print(
            f"\r[{i+1}/{total}] {source_lang} → {target_lang} ({locale}) | ETA {eta}   ",
            end="",
            flush=True,
        )

        # Translate
        try:
            result = translate_one(source, args.model, source_lang, target_lang)
            if result:
                row[COL_TRANSLATED] = result
                translated += 1
            else:
                print(f"\n[warn] Empty result for row {i+1}, skipping.")
                failed += 1
        except Exception as exc:
            print(f"\n[error] Row {i+1} failed after {MAX_RETRIES} retries: {exc}")
            failed += 1

        # Periodic checkpoint save
        if (i + 1) % args.save_every == 0:
            save_csv(output_path, header, data)

    # ── Final save ────────────────────────────────────────────────────────────
    save_csv(output_path, header, data)

    elapsed_total = timedelta(seconds=int(time.time() - start_time))
    print(f"\n")
    print(f"[done] Finished in {elapsed_total}.")
    print(f"       Translated : {translated}")
    print(f"       Skipped    : {skipped}")
    print(f"       Failed     : {failed}")
    print(f"       Output     : {output_path}")


if __name__ == "__main__":
    main()
