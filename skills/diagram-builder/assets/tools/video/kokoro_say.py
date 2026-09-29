"""Synthesize a text file to a 24 kHz WAV with Kokoro-82M, fully offline.

Optionally writes per-word timestamps (English voices only) as JSON.
The language is the voice's first letter, as in Kokoro's own voice naming:
a = American English, b = British English, e = Spanish.
Run it with the interpreter of the venv where `kokoro` and `soundfile` are
installed; the model is read from --model-dir, never downloaded.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
import soundfile as sf
from kokoro import KModel, KPipeline

REPO_ID = "hexgrad/Kokoro-82M"
MODEL_DIR = Path.home() / ".local" / "share" / "gaia-tts" / "kokoro" / "model"
SAMPLE_RATE = 24000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-file", required=True, type=Path)
    parser.add_argument("--voice", required=True,
                        help="one voice (am_michael) or a comma-separated blend averaged 50/50 "
                             "(am_michael,af_heart); the first voice's letter sets the language")
    parser.add_argument("--out", required=True, type=Path, help="output .wav")
    parser.add_argument("--words", type=Path, help="optional output .json with per-word timestamps")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR,
                        help="folder with config.json, kokoro-v1_0.pth and voices/<voice>.pt")
    args = parser.parse_args()

    text = args.text_file.read_text(encoding="utf-8").strip()
    started = time.perf_counter()

    model = KModel(
        repo_id=REPO_ID,
        config=str(args.model_dir / "config.json"),
        model=str(args.model_dir / "kokoro-v1_0.pth"),
    ).eval()
    pipeline = KPipeline(lang_code=args.voice[0], repo_id=REPO_ID, model=model)
    loaded = time.perf_counter()

    chunks, words, offset = [], [], 0.0
    # KPipeline.load_voice splits this on commas and averages the voices it names.
    voice_path = ",".join(str(args.model_dir / "voices" / f"{name.strip()}.pt") for name in args.voice.split(","))
    for result in pipeline(text, voice=voice_path, speed=args.speed, split_pattern=r"\n+"):
        audio = result.audio.numpy()
        for token in result.tokens or []:
            if token.start_ts is None or token.end_ts is None:
                continue
            words.append({
                "word": token.text,
                "start": round(offset + token.start_ts, 3),
                "end": round(offset + token.end_ts, 3),
            })
        chunks.append(audio)
        offset += len(audio) / SAMPLE_RATE

    args.out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.out, np.concatenate(chunks), SAMPLE_RATE)
    if args.words:
        args.words.write_text(json.dumps(words, indent=1, ensure_ascii=False), encoding="utf-8")

    finished = time.perf_counter()
    print(
        f"voice={args.voice} chunks={len(chunks)} audio_seconds={offset:.2f} "
        f"load_seconds={loaded - started:.2f} synth_seconds={finished - loaded:.2f} "
        f"total_seconds={finished - started:.2f} words={len(words)} out={args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
