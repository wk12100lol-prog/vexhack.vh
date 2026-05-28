from PIL import Image, ImageDraw, ImageFont
import os

SIZE = 128
bg = (13, 15, 26)
pink = (255, 107, 157)
green = (0, 255, 163)
dim = (106, 111, 133)

img = Image.new("RGBA", (SIZE, SIZE), bg + (0,))
draw = ImageDraw.Draw(img)

# radial glow
cx = cy = SIZE // 2
for r in range(SIZE // 2, 0, -1):
    a = int(20 * (1 - r / (SIZE // 2)))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(pink[0], pink[1], pink[2], a))

# outer ring
draw.ellipse([8, 8, SIZE - 9, SIZE - 9], outline=pink, width=2)
draw.ellipse([12, 12, SIZE - 13, SIZE - 13], outline=green, width=1)

# V
pts_v = [(40, 95), (64, 30), (88, 95)]
draw.line(pts_v, fill=pink, width=6, joint="curve")
# A
draw.line([(66, 95), (80, 50), (94, 95)], fill=pink, width=5, joint="curve")
draw.line([(73, 78), (87, 78)], fill=pink, width=3)

# small dots decoration
for x, y in [(32, 32), (96, 32), (32, 96), (96, 96)]:
    draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=green)

path = os.path.join(os.path.dirname(__file__) or ".", "logo.png")
img.save(path)
print(f"Logo saved: {path} ({SIZE}x{SIZE})")
