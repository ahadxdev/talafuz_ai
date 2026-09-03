# Word-alignment model

Word-level subtitle timing (`app/services/word_alignment_service.py`) uses
[whisper.cpp](https://github.com/ggerganov/whisper.cpp) through the
`pywhispercpp` Python binding. It runs fully offline on CPU — no cloud call,
no torch.

## Required model

| File           | Size    | Source (Hugging Face)                    |
| -------------- | ------- | ---------------------------------------- |
| `ggml-base.bin`| ~147 MB | `ggerganov/whisper.cpp` → `ggml-base.bin` |

Place it in this directory so the default path resolves:

```
backend/models/ggml-base.bin
```

Download (one time, on the machine that runs the backend):

```bash
cd backend/models
# either via the whisper.cpp helper script
bash <(curl -sL https://github.com/ggerganov/whisper.cpp/raw/master/models/download-ggml-model.sh) base
# or directly
curl -L -o ggml-base.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin
```

## Configuration (project root `.env`)

All optional — sensible defaults are used when unset:

| Variable                       | Default                          | Meaning                                        |
| ------------------------------ | -------------------------------- | ---------------------------------------------- |
| `WORD_ALIGNMENT_ENABLED`       | `true`                           | Master switch; `false` keeps proportional timing.|
| `WORD_ALIGNMENT_MODEL_PATH`    | `backend/models/ggml-base.bin`   | ggml model file. Skipped when absent.            |
| `WORD_ALIGNMENT_LANGUAGE`      | `hi`                             | whisper language hint (Hindi/Hinglish speech).   |
| `WORD_ALIGNMENT_THREADS`       | `4`                              | CPU threads for the single per-job pass.         |
| `WORD_ALIGNMENT_MIN_MATCH`     | `0.6`                            | Per-cue quality gate (fraction of words matched).|

## Behaviour

- The audio is recognised **once per job**; the DTW token result is cached in
  the job directory as `word_alignment.json` and reused on re-romanization.
- Word timings are persisted **only** for cues that pass the quality gate.
  Every other cue keeps `words=None` and falls back to the existing
  proportional (speech-weighted) estimate in the editor and the exporter.
- If `pywhispercpp` or the model is missing, word alignment is silently
  skipped and nothing else changes.

Model binaries are git-ignored; only this README is tracked.
