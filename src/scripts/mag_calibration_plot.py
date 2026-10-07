#!/usr/bin/env python3
"""Графики калибровки магнитометра для статьи.

Строит два PNG из сырого лога `drafts/magnetometer/mag-data-u.txt`:
  mag-raw.png  — сырое облако: эллипс, смещённый от нуля
  mag-fit.png  — после нормировки: единичная окружность вокруг нуля

Параметры эллипса (dx, dy, kx, ky) не подбираются руками, а считаются
методом наименьших квадратов: уравнение эллипса линейно по своим
коэффициентам, если взять признаки (x, y², y, 1) и мишень x².

    ((x-dx)/kx)² + ((y-dy)/ky)² = 1
        ⇓  раскрыть скобки, r = kx²/ky²
    x² = 2·dx·x  −  r·y²  +  2·r·dy·y  +  (kx² − dx² − r·dy²)
         └─ b1 ─┘  └─ b2 ─┘  └─── b3 ───┘  └────── b4 ──────┘

Запуск:  .venv/bin/python scripts/mag_calibration_plot.py
"""

from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "drafts" / "magnetometer" / "mag-data-u.csv"
OUT_DIR = ROOT / "article-3" / "images"

# Палитра: слот 1 (синий) — данные, слот 2 (оранжевый) — подогнанная геометрия.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
DATA_C = "#2a78d6"
FIT_C = "#eb6834"
GRID_C = "#e3e2de"

# Числа, подобранные на глаз в 2020-м (mag-data.plt).
HAND = dict(dx=0.13, dy=-0.25, kx=0.525, ky=0.485)


def fit_ellipse(x, y):
    """МНК-подгонка эллипса с осями по координатам. Возвращает dx, dy, kx, ky."""
    design = np.column_stack([x, y**2, y, np.ones_like(x)])
    b1, b2, b3, b4 = np.linalg.lstsq(design, x**2, rcond=None)[0]

    r = -b2  # (kx/ky)²
    dx = b1 / 2
    dy = b3 / (2 * r)
    kx = np.sqrt(b4 + dx**2 + r * dy**2)
    ky = kx / np.sqrt(r)
    return dx, dy, kx, ky


def style(ax, title, subtitle):
    ax.set_aspect("equal")
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID_C, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID_C)
    ax.tick_params(colors=INK_MUTED, labelsize=9, length=0)
    ax.axhline(0, color=GRID_C, linewidth=1.2, zorder=1)
    ax.axvline(0, color=GRID_C, linewidth=1.2, zorder=1)
    ax.set_title(title, color=INK, fontsize=13, fontweight="bold", loc="left", pad=16)
    ax.text(
        0, 1.015, subtitle, transform=ax.transAxes,
        color=INK_MUTED, fontsize=10, va="bottom", ha="left",
    )


def save(fig, name):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    fig.savefig(path, dpi=160, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print(f"  {path.relative_to(ROOT)}")


def plot_raw(x, y, dx, dy, kx, ky):
    fig, ax = plt.subplots(figsize=(6.2, 6.2), facecolor=SURFACE)
    style(ax, "Сырые данные",
          f"полный оборот тележки, {len(x)} точек")

    t = np.linspace(0, 2 * np.pi, 400)
    ax.plot(dx + kx * np.cos(t), dy + ky * np.sin(t),
            color=FIT_C, linewidth=2, zorder=3)
    ax.scatter(x, y, s=18, color=DATA_C, alpha=0.75,
               linewidths=0, zorder=4)

    # Центр облака и ноль координат — две разные точки, в этом вся проблема.
    # Подписи живут внутри кольца: там пусто, и выносные линии не нужны.
    ax.plot([0, dx], [0, dy], linestyle=(0, (4, 3)), color=INK_MUTED,
            linewidth=1.3, zorder=5)
    ax.plot([0], [0], marker="o", markersize=7, color=SURFACE,
            markeredgecolor=INK_MUTED, markeredgewidth=1.8, zorder=6)
    ax.plot([dx], [dy], marker="+", markersize=14, markeredgewidth=2.4,
            color=FIT_C, zorder=6)

    ax.text(-0.03, 0.04, "ноль", color=INK_MUTED, fontsize=10,
            ha="right", va="bottom", zorder=6)
    ax.text(dx, dy - 0.05, f"центр ({dx:+.2f}, {dy:+.2f})",
            color=FIT_C, fontsize=10, fontweight="bold",
            ha="center", va="top", zorder=6)
    ax.text(dx, dy - ky - 0.06, f"полуоси {kx:.3f} × {ky:.3f}",
            color=FIT_C, fontsize=10, ha="center", va="top", zorder=5)

    ax.set_xlabel("GX", color=INK_MUTED, fontsize=10)
    ax.set_ylabel("GY", color=INK_MUTED, fontsize=10)
    ax.set_xlim(-0.9, 0.9)
    ax.set_ylim(-0.9, 0.9)
    save(fig, "mag-raw.png")


def plot_fit(x, y, dx, dy, kx, ky):
    nx, ny = (x - dx) / kx, (y - dy) / ky
    radius = np.hypot(nx, ny)

    fig, ax = plt.subplots(figsize=(6.2, 6.2), facecolor=SURFACE)
    style(
        ax,
        "Нормированные данные",
        f"сдвиг на центр, деление на полуоси; радиус "
        f"{radius.mean():.3f} ± {radius.std():.3f}",
    )

    t = np.linspace(0, 2 * np.pi, 400)
    ax.plot(np.cos(t), np.sin(t), color=FIT_C, linewidth=2, zorder=3)
    ax.scatter(nx, ny, s=18, color=DATA_C, alpha=0.75, linewidths=0, zorder=4)

    ax.plot([0], [0], marker="o", markersize=7, color=SURFACE,
            markeredgecolor=INK_MUTED, markeredgewidth=1.8, zorder=6)
    ax.text(-0.06, 0.06, "ноль", color=INK_MUTED, fontsize=10,
            ha="right", va="bottom", zorder=6)
    ax.text(0, -0.30, "единичная окружность", color=FIT_C, fontsize=10,
            fontweight="bold", ha="center", va="center", zorder=6)

    ax.set_xlabel("GX нормированный", color=INK_MUTED, fontsize=10)
    ax.set_ylabel("GY нормированный", color=INK_MUTED, fontsize=10)
    ax.set_xlim(-1.75, 1.75)
    ax.set_ylim(-1.75, 1.75)
    save(fig, "mag-fit.png")


def main():
    x, y = np.loadtxt(DATA, unpack=True)
    dx, dy, kx, ky = fit_ellipse(x, y)

    print(f"МНК по {len(x)} точкам:")
    print(f"  dx={dx:+.4f}  dy={dy:+.4f}  kx={kx:.4f}  ky={ky:.4f}")
    print("на глаз в 2020-м:")
    print("  dx={dx:+.4f}  dy={dy:+.4f}  kx={kx:.4f}  ky={ky:.4f}".format(**HAND))

    radius = np.hypot((x - dx) / kx, (y - dy) / ky)
    print(f"радиус после нормировки: {radius.mean():.4f} ± {radius.std():.4f}")

    print("файлы:")
    plot_raw(x, y, dx, dy, kx, ky)
    plot_fit(x, y, dx, dy, kx, ky)


if __name__ == "__main__":
    main()
