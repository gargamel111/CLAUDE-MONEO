"""Genera icono.ico (círculo rojo a rosado con una flecha de descarga).

Uso: python hacer_icono.py   (necesita Pillow)
"""

import os

from PIL import Image, ImageDraw, ImageFilter

N = 1024
HERE = os.path.dirname(os.path.abspath(__file__))


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main():
    red, pink = (167, 40, 57), (212, 107, 168)
    grad = Image.new("RGB", (N, N))
    px = grad.load()
    for y in range(N):
        for x in range(N):
            px[x, y] = lerp(red, pink, min(1.0, (x * 0.35 + y * 0.65) / N * 1.1))
    # Brillo suave arriba a la izquierda
    glow = Image.new("L", (N, N), 0)
    ImageDraw.Draw(glow).ellipse((90, 40, 620, 520), fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    grad = Image.composite(Image.new("RGB", (N, N), (245, 170, 210)), grad, glow)

    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).ellipse((24, 24, N - 24, N - 24), fill=255)
    icon = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    icon.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(icon)
    w, white = 64, (255, 255, 255, 255)
    cx = N // 2
    d.line((cx, 270, cx, 610), fill=white, width=w)                 # palo de la flecha
    d.line((cx - 165, 450, cx + 4, 616), fill=white, width=w)        # punta izquierda
    d.line((cx + 165, 450, cx - 4, 616), fill=white, width=w)        # punta derecha
    d.line((cx - 210, 730, cx + 210, 730), fill=white, width=w)      # base
    for x, y in ((cx, 270), (cx - 165, 450), (cx + 165, 450), (cx - 210, 730), (cx + 210, 730), (cx, 612)):
        d.ellipse((x - w // 2, y - w // 2, x + w // 2, y + w // 2), fill=white)

    icon.save(os.path.join(HERE, "icono.ico"),
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.resize((256, 256), Image.LANCZOS).save(os.path.join(HERE, "icono.png"))


if __name__ == "__main__":
    main()
