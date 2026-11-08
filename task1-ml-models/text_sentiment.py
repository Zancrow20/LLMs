import time

from datasets import load_dataset
from sklearn.metrics import classification_report
from transformers import pipeline

MODEL = "distilbert/distilbert-base-uncased-finetuned-sst-2-english"
clf = pipeline("text-classification", model=MODEL, device="mps")


def predict(text: str) -> dict:
    return clf(text, truncation=True)[0]


def evaluate(n: int = 500, device: str = "mps"):
    model = clf if device == "mps" else pipeline("text-classification", model=MODEL, device=device)
    ds = load_dataset("stanfordnlp/imdb", split="test").shuffle(seed=42).select(range(n))
    texts, labels = list(ds["text"]), list(ds["label"])
    model(texts[:16], truncation=True, batch_size=16)  # warm-up
    t = time.perf_counter()
    preds = model(texts, truncation=True, batch_size=16)
    elapsed = time.perf_counter() - t
    y_pred = [1 if p["label"] == "POSITIVE" else 0 for p in preds]
    print(f"[{device}] {n / elapsed:.1f} texts/s, {elapsed / n * 1000:.1f} ms/text")
    print(classification_report(labels, y_pred, target_names=["neg", "pos"], digits=3))
    return texts, labels, y_pred


if __name__ == "__main__":
    for text in ["I loved this movie, the acting was brilliant!",
                 "Worst two hours of my life.",
                 "Oh great, another sequel nobody asked for."]:
        print(predict(text), "<-", text)

    texts, labels, y_pred = evaluate(device="mps")
    evaluate(n=100, device="cpu")

    errors = [(t, y) for t, y, p in zip(texts, labels, y_pred) if y != p]
    print(f"\nОшибок: {len(errors)}. Примеры:")
    for text, y in errors[:3]:
        print(f"- true={'pos' if y else 'neg'}: {text[:300]}...\n")
