import io
import re
import time
from pathlib import Path

import jiwer
import soundfile as sf
from datasets import Audio, load_dataset
from transformers import pipeline

DATA = Path(__file__).parent / "data"
RU_REFERENCE = ("Добрый вечер. Сегодня мы проверяем, насколько хорошо модель распознаёт русскую речь. "
                "Рекомендательные системы помогают пользователям находить интересные фильмы и музыку.")
pipes = {}


def get_asr(model: str = "openai/whisper-small", device: str = "mps"):
    if (model, device) not in pipes:
        pipes[model, device] = pipeline("automatic-speech-recognition", model=model, device=device)
    return pipes[model, device]


def transcribe(audio, language: str = "russian", model: str = "openai/whisper-small") -> str:
    out = get_asr(model)(audio, generate_kwargs={"language": language, "task": "transcribe"},
                         return_timestamps=True)
    return out["text"].strip()


def norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^\w' ]", " ", s)).strip()


def load_librispeech():
    ds = load_dataset("hf-internal-testing/librispeech_asr_dummy", "clean", split="validation")
    ds = ds.cast_column("audio", Audio(decode=False))
    samples = []
    for s in ds:
        array, sr = sf.read(io.BytesIO(s["audio"]["bytes"]), dtype="float32")
        samples.append(({"raw": array, "sampling_rate": sr}, s["text"]))
    return samples


def evaluate(model: str = "openai/whisper-small"):
    samples = load_librispeech()
    transcribe(dict(samples[0][0]), "english", model)  # warm-up

    refs, hyps, audio_sec = [], [], 0.0
    t = time.perf_counter()
    for audio, text in samples:
        audio_sec += len(audio["raw"]) / audio["sampling_rate"]
        hyps.append(transcribe(dict(audio), "english", model))
        refs.append(text)
    elapsed = time.perf_counter() - t

    m = jiwer.process_words([norm(r) for r in refs], [norm(h) for h in hyps])
    whisper_norm = get_asr(model).tokenizer.normalize
    refs_w, hyps_w = [whisper_norm(r) for r in refs], [whisper_norm(h) for h in hyps]
    print(f"[{model}] n={len(samples)} audio={audio_sec:.0f}s WER={m.wer:.3f} "
          f"(S={m.substitutions} D={m.deletions} I={m.insertions}) "
          f"WER_whisper_norm={jiwer.wer(refs_w, hyps_w):.3f} "
          f"RTF={elapsed / audio_sec:.3f} time={elapsed:.1f}s")
    for r, h in list((r, h) for r, h in zip(refs_w, hyps_w) if r != h)[:4]:
        print(f"  REF: {r}\n  HYP: {h}")


if __name__ == "__main__":
    for model in ["openai/whisper-tiny", "openai/whisper-small"]:
        evaluate(model)

    for f in sorted(DATA.glob("*.wav")) + sorted(DATA.glob("*.m4a")) + sorted(DATA.glob("*.mp3")):
        t = time.perf_counter()
        text = transcribe(str(f))
        print(f"\n{f.name} ({time.perf_counter() - t:.1f}s): {text}")
        if f.name == "ru_tts.wav":
            print(f"WER vs reference: {jiwer.wer(norm(RU_REFERENCE), norm(text)):.3f}")
