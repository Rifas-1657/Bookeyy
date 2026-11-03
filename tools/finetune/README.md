# Bookey fine-tuning — native Tanglish *style* (no voice cloning)

Three Kaggle notebooks teach Bookey's voice and ears how native Tamil speakers mix English into
Tamil. They learn from thousands of real speakers in AI4Bharat's Tamil corpora; nobody's voice is
copied. Everything stays on free GPUs and private Hugging Face repos.

| # | Notebook | Kaggle accelerator | Time | Output (private HF repo) |
| --- | --- | --- | --- | --- |
| 1 | `01_build_tanglish_dataset.ipynb` | none (CPU) | 1–3 h | `<you>/bookey-tanglish-speech` + `english_lexicon.json` |
| 2 | `02_finetune_indic_parler_style.ipynb` | GPU T4 ×2 | 30 min smoke test, then 10–20 GPU-h over a few sessions | `<you>/bookey-parler-tanglish` |
| 3 | `03_finetune_whisper_tanglish_lora.ipynb` | GPU T4 | 3–5 h | `<you>/bookey-whisper-turbo-tanglish` (`ct2/` for faster-whisper) |

## One-time setup

1. **Hugging Face:** access to `ai4bharat/IndicVoices` and `ai4bharat/indicvoices_r` (done). Create a
   **write** token: huggingface.co → Settings → Access Tokens → *New token* → type *Write*.
2. **Kaggle:** sign in, verify your phone (Settings → Phone verification), which unlocks GPUs and internet.
3. For each notebook: kaggle.com → *Create* → *New Notebook* → *File* → *Import Notebook* → pick the
   `.ipynb` from this folder. Then in the right panel:
   * *Session options* → **Internet: On**; Accelerator as in the table.
   * *Add-ons* → *Secrets* → add `HF_TOKEN` = your write token, and tick it for this notebook.
4. *Run All*. Kaggle sessions stop after 12 h; notebooks 2 and 3 save checkpoints and resume on re-run.

## Order

1. **Notebook 1** builds the dataset (default 30 h; change `TARGET_HOURS`). It keeps utterances where
   speakers use English words, rated *excellent/good*, 2–15 s, no unintelligible parts, SNR ≥ 15 dB,
   plus 1 in 4 clean pure-Tamil lines for balance. Speakers are split 98/2 into train/test, so the test
   set has voices the model never heard.
2. **Notebook 2** first runs a 60-step **smoke test** (`SMOKE_TEST = True`) and plays base vs tuned audio
   for Bookey's two house voices (male / female teacher descriptions). Download `tuned_*.wav` and rate them
   on Bookey's voice test page (`tools/voice-eval`). If it fits in memory and sounds right, set
   `SMOKE_TEST = False` and run the full training.
   *Out of memory?* Run the same notebook on a 24 GB GPU (Lightning AI or Modal free credits) and
   raise `BATCH` to 4.
3. **Notebook 3** prints character error rate (CER) **before and after** on held-out speakers. Use it only
   if CER drops.

## Bringing results into Bookey

* **Lexicon:** `english_lexicon.json` (English word → native Tamil spelling, e.g. school → ஸ்கூல்) can be
  merged into `backend/app/voice/lexicon.py` so the transliterator spells English the way native
  speakers do.
* **TTS:** set `TTS_PROVIDER=indic_parler` and point the provider at `<you>/bookey-parler-tanglish`
  (pre-render narration at book-build time; it is too slow for live answers on a 4 GB GPU).
* **STT:** download the `ct2/` folder and set `FASTER_WHISPER_MODEL` to its path.
* Ship only if it beats today's engines on the blind voice test **and** on Whisper CER.

## Local checks (before spending Kaggle hours)

```bash
tools/voice-eval/.venv-f5/Scripts/python -m pytest tools/finetune/test_tanglish_data.py -q
tools/voice-eval/.venv-f5/Scripts/python tools/finetune/validate_notebooks.py
```

The second command compiles every cell and dry-runs notebook 1's data loop on cached shards.
After editing `tanglish_data.py` or `build_notebooks.py`, regenerate with `python tools/finetune/build_notebooks.py`.

## Attribution

Training data: AI4Bharat IndicVoices and IndicVoices-R (CC-BY-4.0, https://ai4bharat.iitm.ac.in).
Base models: `ai4bharat/indic-parler-tts` (Apache-2.0), `openai/whisper-large-v3-turbo` (MIT).
Keep these credits in the dataset/model cards and in Bookey's about page.
