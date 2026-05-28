from PIL import Image, ImageDraw, ImageFont
import os

SIZE = 128
outline = 4
inner = SIZE - outline * 2

img = Image.new("RGBA", (SIZE, SIZE), (13, 15, 26, 255))
draw = ImageDraw.Draw(img)

# rounded dark card
draw.rounded_rectangle([2, 2, SIZE - 3, SIZE - 3], radius=18, fill=(20, 22, 36, 255), outline=(255, 107, 157), width=2)

# inner border (green)
draw.rounded_rectangle([6, 6, SIZE - 7, SIZE - 7], radius=15, outline=(0, 255, 163), width=1)

# V - thick pink
pts = [(36, 96), (64, 28), (92, 96)]
draw.line(pts, fill=(255, 107, 157), width=8, joint="curve")

# A
draw.line([(62, 96), (78, 44), (94, 96)], fill=(255, 107, 157), width=7, joint="curve")
draw.line([(70, 74), (86, 74)], fill=(255, 107, 157), width=5)

# X dots (green)
for x, y in [(30, 28), (98, 28), (30, 100), (98, 100)]:
    draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(0, 255, 163))

path = os.path.join(os.path.dirname(__file__) or ".", "logo.png")
img.save(path)
print(f"Logo saved: {path} ({SIZE}x{SIZE})")
