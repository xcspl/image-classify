"""Find images under a root and read what the file itself can tell: hash,
size, dimensions, EXIF capture time, camera and GPS. Nothing here calls a
model."""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

from PIL import ExifTags, Image, ImageOps

try:  # optional: iPhone photos
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    HEIF = True
except Exception:  # pragma: no cover - depends on the environment
    HEIF = False

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"} | (
    {".heic", ".heif"} if HEIF else set()
)
# Directories never scanned: the default bundle, our cache, and the usual noise
# (a bundle written elsewhere inside the root is skipped by path).
SKIP_DIRS = {"okf", ".image-classify", ".git", "node_modules", "__pycache__", ".thumbnails", "@eaDir"}


@dataclass
class ImageFile:
    path: Path  # absolute
    rel: str  # posix path relative to the scanned root
    sha256: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    format: str | None = None
    taken_at: str | None = None
    camera: str | None = None
    gps: dict | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)


def find_images(root: Path, skip: list[Path] = ()) -> list[Path]:
    skip_abs = {Path(p).resolve() for p in skip}
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                             and (Path(dirpath) / d).resolve() not in skip_abs)
        for name in sorted(filenames):
            if Path(name).suffix.lower() in IMAGE_EXTS and not name.startswith("."):
                out.append(Path(dirpath) / name)
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ratio(v) -> float:
    try:
        return float(v)
    except Exception:
        n, d = v  # older Pillow tuples
        return n / d


def _gps(gps: dict) -> dict | None:
    try:
        def deg(vals, ref):
            d, m, s = (_ratio(x) for x in vals)
            x = d + m / 60 + s / 3600
            return -x if ref in ("S", "W") else x

        lat = deg(gps[2], gps[1])
        lon = deg(gps[4], gps[3])
        return {"lat": round(lat, 6), "lon": round(lon, 6)}
    except Exception:
        return None


def describe_file(path: Path, root: Path) -> ImageFile:
    st = path.stat()
    info = ImageFile(
        path=path,
        rel=path.relative_to(root).as_posix(),
        sha256=sha256_of(path),
        size_bytes=st.st_size,
    )
    try:
        with Image.open(path) as im:
            info.width, info.height = im.size
            info.format = (im.format or path.suffix.lstrip(".")).lower()
            exif = im.getexif()
            if exif:
                tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
                sub = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
                tags.update({ExifTags.TAGS.get(k, k): v for k, v in sub.items()})
                when = tags.get("DateTimeOriginal") or tags.get("DateTime")
                if isinstance(when, str) and len(when) >= 19:
                    info.taken_at = when[:10].replace(":", "-") + "T" + when[11:19]
                make, model = str(tags.get("Make", "")).strip(), str(tags.get("Model", "")).strip()
                cam = model if make and model.lower().startswith(make.lower()) else f"{make} {model}".strip()
                info.camera = cam or None
                gps_ifd = exif.get_ifd(0x8825) if hasattr(exif, "get_ifd") else None
                if gps_ifd:
                    info.gps = _gps(dict(gps_ifd))
    except Exception as e:  # unreadable or truncated: still listed, never analysed
        info.error = f"{type(e).__name__}: {e}"
    return info


def prepare_for_model(path: Path, max_side: int = 1024, quality: int = 85) -> bytes:
    """A JPEG no longer than `max_side` on its long edge, EXIF rotation
    applied, first frame of animations, alpha flattened on white."""
    with Image.open(path) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        return buf.getvalue()
