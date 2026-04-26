# shopify-csv-translator

A local, offline Python script that auto-translates Shopify **Translate & Adapt** CSV exports using a locally running LLM via [Ollama](https://ollama.com). No API keys, no cloud services, no per-character fees.

> The only open-source tool that handles the full Shopify Translate & Adapt CSV format end-to-end with a local LLM.

---

## Features

- **Any source language → any target language** — source language is auto-detected or set manually
- **Same-language mode** — if source and target match, it proofreads and adapts copy instead of translating
- **HTML & URL protection** — all `<tags>` and CDN links are masked before translation and restored after, so they are never corrupted
- **Resume by default** — rows that already have a translation are skipped unless you pass `--overwrite`
- **Retry with backoff** — transient Ollama failures are retried up to 3 times with exponential wait
- **Live progress + ETA** — shows current row, language pair, and estimated time remaining
- **Periodic checkpoints** — saves progress every N rows so a crash doesn't lose your work
- **End-of-run summary** — translated / skipped / failed counts and total elapsed time

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally (`http://localhost:11434`)
- A translation-capable model pulled in Ollama, e.g.:
  ```
  ollama pull translategemma
  # or
  ollama pull llama3
  # or
  ollama pull mistral
  ```

No additional Python packages are required — the script uses only the standard library.

---

## Shopify CSV Format

Export your translations from **Shopify Admin → Settings → Languages → Export**.

The script expects the standard 8-column Shopify Translate & Adapt CSV:

| Column | Field |
|--------|-------|
| A | Type |
| B | Identification |
| C | Field |
| D | **Locale** (e.g. `it`, `de`, `fr`) |
| E | Market |
| F | Status |
| G | **Default content** (source text) |
| H | **Translated content** (written by this script) |

---

## Usage

```bash
# Basic — auto-detect source language, skip already-translated rows
python shopify_general_translator.py my_store_export.csv

# Specify source language manually (faster — skips auto-detection call)
python shopify_general_translator.py my_store_export.csv --source-lang Italian

# Use a different Ollama model
python shopify_general_translator.py my_store_export.csv --model llama3

# Re-translate rows that already have a translation
python shopify_general_translator.py my_store_export.csv --overwrite

# Save checkpoint every 25 rows instead of every 10
python shopify_general_translator.py my_store_export.csv --save-every 25
```

### All options

```
positional arguments:
  input                 Input CSV file (Shopify Translate & Adapt format)

options:
  --model MODEL, -m MODEL
                        Ollama model name (default: translategemma:latest)
  --source-lang LANGUAGE, -s LANGUAGE
                        Source language of the Default content column, e.g. 'Italian'.
                        If omitted, auto-detected from the first non-empty row.
  --overwrite           Re-translate rows that already have a Translated content value
  --save-every N        Save progress every N rows (default: 10)
```

---

## Output

The script writes a new file next to your input:

```
my_store_export.csv  →  my_store_export_TRANSLATED.csv
```

Import it back via **Shopify Admin → Settings → Languages → Import**.

---

## Supported Languages

The script resolves Shopify locale codes to language names automatically. Supported codes include:

`en` `it` `de` `fr` `es` `pt` `pt-PT` `pt-BR` `nl` `sv` `da` `fi` `nb` `pl` `cs` `hu` `ro` `sk` `hr` `sl` `bg` `el` `lt` `lv` `et` `ru` `uk` `tr` `ar` `zh` `zh-TW` `ja` `ko`

Unknown locale codes are passed as-is to the LLM prompt.

---

## Recommended Models

| Model | Notes |
|-------|-------|
| `translategemma:latest` | Default. Fine-tuned for translation, very fast. |
| `llama3` | Good balance of quality and speed for European languages. |
| `mistral` | Strong on French, Italian, Spanish. |
| `qwen2` | Best for CJK (Chinese, Japanese, Korean). |

---

## Workflow

```
Shopify Admin
    └── Settings → Languages → Export CSV
            │
            ▼
shopify_general_translator.py
            │
            ▼
    *_TRANSLATED.csv
            │
            ▼
Shopify Admin
    └── Settings → Languages → Import CSV
```

---

## License

MIT — do whatever you want with it.
