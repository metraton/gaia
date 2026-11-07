"""Synthesize a text file to a 24 kHz WAV with Chatterbox (English), fully offline, on CPU.

Each line is one sentence, voiced alone with torch seed --seed plus its line
number (1-based), followed by real silence when --gaps gives one value per
line. --sentences writes each line's voiced start and end in seconds, which
align.mjs takes as the page's exact sentence spans; Chatterbox has no word
timings. Every sentence is cached by text, voice settings, seed and reference
clip, so a stopped run resumes and an unchanged sentence is never voiced twice.
Run it with the interpreter of the venv where `chatterbox-tts` is installed;
the model is read from --model-dir, never downloaded.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
import soundfile as sf
import torch

MODEL_ID = "ResembleAI/chatterbox"
# A sample louder than this counts as speech when a sentence's span is measured.
VOICED_LEVEL = 0.01


def cache_key(text, exaggeration, cfg_weight, seed, reference_digest):
    """Names a voiced sentence by everything its audio depends on."""
    fields = {"model": MODEL_ID, "text": text, "exaggeration": exaggeration, "cfg_weight": cfg_weight,
              "seed": seed, "reference": reference_digest}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def voiced_span(audio, sample_rate):
    """Seconds from the first to the last sample of the clip that is louder than VOICED_LEVEL."""
    loud = np.flatnonzero(np.abs(audio) > VOICED_LEVEL)
    if not loud.size:
        return 0.0, len(audio) / sample_rate
    return loud[0] / sample_rate, (loud[-1] + 1) / sample_rate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-file", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="output .wav")
    parser.add_argument("--sentences", type=Path, help="optional output .json with each line's voiced start and end")
    parser.add_argument("--model-dir", required=True, type=Path,
                        help="folder with ve.safetensors, t3_cfg.safetensors, s3gen.safetensors, tokenizer.json, conds.pt")
    parser.add_argument("--reference", required=True, type=Path, help="clip of the voice to clone")
    parser.add_argument("--cache-dir", required=True, type=Path, help="where voiced sentences are kept between runs")
    parser.add_argument("--exaggeration", type=float, default=0.5)
    parser.add_argument("--cfg-weight", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--gaps", default="",
                        help="comma-separated seconds of silence written after each line, one per line")
    args = parser.parse_args()

    lines = [line for line in args.text_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    gaps = [float(g) for g in args.gaps.split(",")] if args.gaps else [0.0] * len(lines)
    if len(gaps) != len(lines):
        parser.error(f"--gaps has {len(gaps)} values for {len(lines)} lines")
    if not args.reference.is_file():
        parser.error(f"no reference clip at {args.reference}")
    reference_digest = hashlib.sha256(args.reference.read_bytes()).hexdigest()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    model, sample_rate, generated = None, None, 0
    chunks, spans, offset = [], [], 0.0
    for number, (line, gap) in enumerate(zip(lines, gaps), start=1):
        cached = args.cache_dir / f"{cache_key(line, args.exaggeration, args.cfg_weight, args.seed + number, reference_digest)}.wav"
        if cached.is_file():
            audio, sample_rate = sf.read(cached, dtype="float32")
        else:
            if model is None:
                from chatterbox.tts import ChatterboxTTS
                model = ChatterboxTTS.from_local(args.model_dir, device="cpu")
            torch.manual_seed(args.seed + number)
            audio = model.generate(line, audio_prompt_path=str(args.reference), exaggeration=args.exaggeration,
                                   cfg_weight=args.cfg_weight).squeeze(0).numpy().astype(np.float32)
            sample_rate = model.sr
            # Written aside and renamed, so a stopped run never leaves a truncated sentence in the cache.
            partial = cached.with_suffix(f".{os.getpid()}.partial.wav")
            sf.write(partial, audio, sample_rate)
            partial.replace(cached)
            generated += 1
            print(f"line={number}/{len(lines)} voiced at {time.perf_counter() - started:.1f} s", flush=True)
        start, end = voiced_span(audio, sample_rate)
        spans.append({"text": line, "start": round(offset + start, 3), "end": round(offset + end, 3)})
        silence = np.zeros(int(round(gap * sample_rate)), dtype=np.float32)
        chunks.extend([audio, silence])
        offset += (len(audio) + len(silence)) / sample_rate

    args.out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.out, np.concatenate(chunks), sample_rate)
    if args.sentences:
        args.sentences.write_text(json.dumps(spans, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"voice=chatterbox lines={len(lines)} generated={generated} cached={len(lines) - generated} "
          f"audio_seconds={offset:.2f} total_seconds={time.perf_counter() - started:.2f} out={args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
