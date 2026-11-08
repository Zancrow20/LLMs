import time
from pathlib import Path

import torch
from PIL import Image
from torch.utils.data import DataLoader, Subset
from torchvision.datasets import ImageFolder
from torchvision.models import ResNet50_Weights, resnet50

DATA = Path(__file__).parent / "data" / "imagenette2-160" / "val"
IMAGENET_IDX = [0, 217, 482, 491, 497, 566, 569, 571, 574, 701]

weights = ResNet50_Weights.DEFAULT
preprocess = weights.transforms()
categories = weights.meta["categories"]
models = {}


def get_model(device: str):
    if device not in models:
        models[device] = resnet50(weights=weights).eval().to(device)
    return models[device]


@torch.inference_mode()
def predict(path, k: int = 5, device: str = "mps"):
    x = preprocess(Image.open(path).convert("RGB")).unsqueeze(0).to(device)
    top = get_model(device)(x).softmax(1)[0].topk(k)
    return [(categories[i], round(p.item(), 3)) for p, i in zip(top.values, top.indices)]


@torch.inference_mode()
def evaluate(n: int = 500, device: str = "mps", batch_size: int = 32):
    model = get_model(device)
    ds = ImageFolder(DATA, transform=preprocess)
    ds = Subset(ds, torch.randperm(len(ds), generator=torch.Generator().manual_seed(0))[:n].tolist())
    target_map = torch.tensor(IMAGENET_IDX)
    model(torch.zeros(1, 3, 224, 224, device=device))  # warm-up

    top1 = top5 = top1_10 = 0
    t = time.perf_counter()
    for x, y in DataLoader(ds, batch_size=batch_size):
        logits = model(x.to(device)).cpu()
        top = logits.topk(5, dim=1).indices
        target = target_map[y]
        top1 += (top[:, 0] == target).sum().item()
        top5 += (top == target[:, None]).any(1).sum().item()
        top1_10 += (logits[:, IMAGENET_IDX].argmax(1) == y).sum().item()
    elapsed = time.perf_counter() - t
    print(f"[{device}] n={n} top1={top1 / n:.3f} top5={top5 / n:.3f} top1_10cls={top1_10 / n:.3f} "
          f"{n / elapsed:.1f} img/s, {elapsed / n * 1000:.1f} ms/img")


if __name__ == "__main__":
    print("Классы Imagenette ->", [categories[i] for i in IMAGENET_IDX])
    for folder in sorted(DATA.iterdir())[:3]:
        img = next(folder.glob("*.JPEG"))
        print(img.name, predict(img))

    evaluate(device="mps")
    evaluate(n=200, device="cpu")
