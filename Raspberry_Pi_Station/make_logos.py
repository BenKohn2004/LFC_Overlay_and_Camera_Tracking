"""Turn every image in logos_src/ into a uniform 128x128 circle in logos/.
Near-square sources are center-cropped to fill the circle; wide/tall ones are
scaled to fit fully inside it on the white disc (no clipped text).
Re-run any time; drop new club images (filename = club name) into logos_src/."""
import os
from PIL import Image, ImageDraw, ImageOps

SRC = os.path.expanduser("~/skewered/logos_src")
DST = os.path.expanduser("~/skewered/logos")
SIZE = 128
SS = 4
FIT_THRESHOLD = 1.15   # aspect ratio beyond this -> fit inside, not crop
FIT_FRACTION = 0.78    # fitted content spans this much of the diameter

os.makedirs(DST, exist_ok=True)
big = SIZE * SS
mask = Image.new("L", (big, big), 0)
ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)

for fn in sorted(os.listdir(SRC)):
    name, ext = os.path.splitext(fn)
    if ext.lower() not in (".png", ".jpg", ".jpeg", ".jfif", ".webp"):
        continue
    img = Image.open(os.path.join(SRC, fn)).convert("RGBA")
    img = ImageOps.exif_transpose(img)
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    img = Image.alpha_composite(white, img)

    ratio = max(img.size) / float(min(img.size))
    if ratio <= FIT_THRESHOLD:
        side = min(img.size)
        l = (img.width - side) // 2
        t = (img.height - side) // 2
        sq = img.crop((l, t, l + side, t + side)).resize((big, big),
                                                         Image.LANCZOS)
    else:
        # scale whole logo to fit inside the circle on a white disc
        target = int(big * FIT_FRACTION)
        w, h = img.size
        if w >= h:
            nw, nh = target, max(1, round(h * target / w))
        else:
            nw, nh = max(1, round(w * target / h)), target
        content = img.resize((nw, nh), Image.LANCZOS)
        sq = Image.new("RGBA", (big, big), (255, 255, 255, 255))
        sq.paste(content, ((big - nw) // 2, (big - nh) // 2))

    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    out.paste(sq, (0, 0), mask)
    ring = ImageDraw.Draw(out)
    ring.ellipse((SS//2, SS//2, big - 1 - SS//2, big - 1 - SS//2),
                 outline=(70, 70, 80, 255), width=2 * SS)
    out = out.resize((SIZE, SIZE), Image.LANCZOS)
    out.save(os.path.join(DST, name + ".png"))
    print("ok:", name)
