import time
from collections import Counter, defaultdict
from pathlib import Path

import cv2
from ultralytics import YOLO

DATA = Path(__file__).parent / "data"


def load(name: str) -> YOLO:
    return YOLO(DATA / name)


def detect_video(src, model_name="yolo26n.pt", device="mps", conf=0.4,
                 out_path=None, max_frames=None, track=False):
    model = load(model_name)
    cap = cv2.VideoCapture(str(src))
    fps_in = cap.get(cv2.CAP_PROP_FPS)
    size = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    out = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps_in, size) if out_path else None

    ok, frame = cap.read()
    model.predict(frame, device=device, verbose=False)  # warm-up
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    counts, confs, infer_ms, track_ids, n = Counter(), [], [], defaultdict(set), 0
    t = time.perf_counter()
    while max_frames is None or n < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if track:
            r = model.track(frame, device=device, conf=conf, persist=True, tracker="bytetrack.yaml", verbose=False)[0]
        else:
            r = model.predict(frame, device=device, conf=conf, verbose=False)[0]
        infer_ms.append(r.speed["inference"])
        names = [model.names[int(c)] for c in r.boxes.cls]
        counts.update(names)
        confs += r.boxes.conf.tolist()
        if track and r.boxes.id is not None:
            for name, tid in zip(names, r.boxes.id.int().tolist()):
                track_ids[name].add(tid)
        if out:
            out.write(r.plot())
        n += 1
    elapsed = time.perf_counter() - t
    cap.release()
    if out:
        out.release()

    return {"model": model_name, "device": device, "conf": conf, "frames": n, "fps_in": fps_in,
            "fps": round(n / elapsed, 1), "infer_ms": round(sum(infer_ms) / n, 1),
            "det_per_frame": round(sum(counts.values()) / n, 2),
            "mean_conf": round(sum(confs) / max(len(confs), 1), 3),
            "counts": dict(counts.most_common(5)),
            "unique": {k: len(v) for k, v in track_ids.items()} if track else None}


if __name__ == "__main__":
    videos = [p for ext in ("*.avi", "*.mp4", "*.mov") for p in sorted(DATA.glob(ext)) if "_out" not in p.stem]
    for video in videos:
        print(f"\n=== {video.name}: полный прогон + трекинг ===")
        print(detect_video(video, out_path=DATA / f"{video.stem}_out.mp4", track=True))

    video = videos[0]
    print(f"\n=== Сравнения на первых 300 кадрах {video.name} ===")
    for kw in [dict(), dict(device="cpu"), dict(model_name="yolo11n.pt"),
               dict(model_name="yolo26s.pt"), dict(conf=0.25), dict(conf=0.6)]:
        r = detect_video(video, max_frames=300, **kw)
        print({k: r[k] for k in ("model", "device", "conf", "fps", "infer_ms", "det_per_frame", "mean_conf")})

    print("\n=== Валидация на coco8 (демо подсчёта mAP) ===")
    for name, device in [("yolo26n.pt", "mps"), ("yolo26n.pt", "cpu"), ("yolo11n.pt", "cpu")]:
        m = load(name).val(data="coco8.yaml", device=device, batch=1, verbose=False, plots=False)  # batch>1 on MPS is broken
        print(f"{name} [{device}] mAP50={m.box.map50:.3f} mAP50-95={m.box.map:.3f}")
