"""Sample the delivered media for manual QA; samples are not full-video review."""

import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

root = Path(".local/qa")
snapshot = json.loads((root / "acceptance-snapshot.json").read_text(encoding="utf-8"))
ffmpeg = shutil.which("ffmpeg")
font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20)
for name, data in snapshot.items():
    video = data["project"]["data"].get("video_path")
    if not video:
        continue
    folder = root / "contact-frames" / name
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            video,
            "-vf",
            "fps=1/10",
            "-frames:v",
            "12",
            str(folder / "frame-%02d.png"),
        ],
        check=True,
        timeout=60,
    )
    frames = sorted(folder.glob("frame-*.png"))
    sheet = Image.new("RGB", (1280, 390 * ((len(frames) + 1) // 2)), "#e8e8e8")
    draw = ImageDraw.Draw(sheet)
    for i, f in enumerate(frames):
        frame = Image.open(f).convert("RGB")
        frame.thumbnail((640, 360))
        x = (i % 2) * 640
        y = (i // 2) * 390
        sheet.paste(frame, (x, y + 30))
        draw.text((x + 8, y + 4), f"{name}: sample {i + 1}", fill="black", font=font)
    sheet.save(root / (name + "-contact.png"))
    print(name, len(frames), "sampled frames")
