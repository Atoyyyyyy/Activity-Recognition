
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ICON_DIR = BASE_DIR / "static" / "icons"

ICON_DIR.mkdir(parents=True, exist_ok=True)

BACKGROUND = "#F6D365"
CREAM = "#FFF9E9"
TEXT = "#956100"

for size in [192, 512]:

    image = Image.new(
        "RGB",
        (size, size),
        BACKGROUND
    )

    draw = ImageDraw.Draw(image)

    margin = int(size * 0.14)

    draw.rounded_rectangle(
        (
            margin,
            margin,
            size - margin,
            size - margin
        ),
        radius=int(size * 0.18),
        fill=CREAM
    )

    try:
        font = ImageFont.truetype(
            "C:/Windows/Fonts/arialbd.ttf",
            int(size * 0.30)
        )
    except OSError:
        font = ImageFont.load_default()

    label = "AR"

    bbox = draw.textbbox(
        (0, 0),
        label,
        font=font
    )

    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = (size - text_width) / 2 - bbox[0]
    y = (size - text_height) / 2 - bbox[1]

    draw.text(
        (x, y),
        label,
        font=font,
        fill=TEXT
    )

    output = ICON_DIR / f"icon-{size}.png"
    image.save(output)

    print(f"Created: {output.name}")

print("\nAcey yellow PWA icons generated!")
