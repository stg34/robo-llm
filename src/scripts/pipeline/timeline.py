"""
Layer 3a — Timeline builder.

Вход:  ParsedSession + list[Operation] (Layer 1 + 2)
Выход: Timeline — упорядоченный список сегментов с out_t

Два прохода по операциям:
  1. Cut  — дробим видео, выбрасываем паузы (или заменяем SynthSegment-ом)
  2. Insert(after_next_cut) — привязываем к ближайшему Cut, вставляем SynthSegment

Prefix sum → out_start для каждого сегмента.
MixAudio.src_t → out_t конвертируется здесь же.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .parser import ParsedSession
from .rules import Cut, Filter, Insert, MixAudio, Overlay, Operation

# Длительность zoom-анимации (4 фазы Ken Burns при 15 fps)
ZOOM_ANIM_DURATION = 3.2


# ── Сегменты ──────────────────────────────────────────────────────────────────

@dataclass
class VideoSegment:
    """Кусок оригинального видео."""
    src_start: float
    src_end:   float
    out_start: float = 0.0

    @property
    def duration(self) -> float:
        return self.src_end - self.src_start

    @property
    def out_end(self) -> float:
        return self.out_start + self.duration

    def __repr__(self) -> str:
        return (f"VideoSegment(src={self.src_start:.2f}–{self.src_end:.2f} "
                f"out={self.out_start:.2f}–{self.out_end:.2f})")


@dataclass
class SynthSegment:
    """Синтетический сегмент (zoom-анимация, слайд и т.п.)."""
    kind:      str
    duration:  float
    out_start: float       = 0.0
    meta:      dict        = field(default_factory=dict)
    src_t:     float | None = None  # позиция в src_t (для визуализации)

    @property
    def out_end(self) -> float:
        return self.out_start + self.duration

    def __repr__(self) -> str:
        return (f"SynthSegment({self.kind} "
                f"out={self.out_start:.2f}–{self.out_end:.2f} [{self.duration:.2f}s])")


Segment = VideoSegment | SynthSegment


# ── Timeline ──────────────────────────────────────────────────────────────────

@dataclass
class Timeline:
    """Результат Layer 3a."""
    segments:       list[Segment]
    total_duration: float

    # Операции без структурного эффекта — для рендера
    filters:     list[Filter]
    overlays:    list[Overlay]
    audio_mixes: list[tuple[Path, float, float]]   # (файл, src_t, out_t)

    # Фильтры, позиция которых определяется в out_t (не маппится из src_t)
    # Формат: (effect, out_start, out_end)
    out_filters: list[tuple[str, float, float]] = field(default_factory=list)

    # ── Маппинг времени ───────────────────────────────────────────────────────

    def src_to_out(self, src_t: float) -> float | None:
        """src_t → out_t. None если src_t попал в вырезанный интервал."""
        for seg in self.segments:
            if not isinstance(seg, VideoSegment):
                continue
            if seg.src_start <= src_t <= seg.src_end:
                return seg.out_start + (src_t - seg.src_start)
        return None

    def out_to_src(self, out_t: float) -> float | None:
        """out_t → src_t. None для SynthSegment-ов."""
        for seg in self.segments:
            if seg.out_start <= out_t <= seg.out_end:
                if isinstance(seg, VideoSegment):
                    return seg.src_start + (out_t - seg.out_start)
                return None  # SynthSegment
        return None

    def src_to_out_clamp(self, src_t: float) -> float:
        """src_t → out_t; если попал в cut — возвращает начало следующего сегмента."""
        out = self.src_to_out(src_t)
        if out is not None:
            return out
        # Ищем ближайший сегмент после src_t
        for seg in self.segments:
            if isinstance(seg, VideoSegment) and seg.src_start > src_t:
                return seg.out_start
        return self.total_duration


# ── Builder ───────────────────────────────────────────────────────────────────

def _synth_duration(kind: str, meta: dict) -> float:
    if kind == "zoom_animation":
        return ZOOM_ANIM_DURATION
    return meta.get("duration", 1.0)


def build_timeline(session: ParsedSession,
                   ops: list[Operation],
                   apply_cuts: bool = True) -> Timeline:
    """
    Главная функция Layer 3a.

    apply_cuts=True  (по умолчанию): Cut-ы вырезают паузы,
                     Insert(after_next_cut) привязывается к Cut.
    apply_cuts=False: Cut-ы игнорируются; все Insert-ы вставляются
                     по своему src_t ("at"). Удобно для проверки
                     фазы вставок перед финальным монтажом.
    """
    cuts    = sorted([o for o in ops if isinstance(o, Cut)],
                     key=lambda c: c.src_start)
    inserts = [o for o in ops if isinstance(o, Insert)]
    filters  = [o for o in ops if isinstance(o, Filter)]
    overlays = [o for o in ops if isinstance(o, Overlay)]
    mix_ops  = [o for o in ops if isinstance(o, MixAudio)]

    segments: list[Segment] = []
    synth_inserts: list[tuple[SynthSegment, Insert]] = []
    duration = session.duration

    if apply_cuts:
        # ── Привязать Insert(after_next_cut) к Cut ────────────────────────────
        bound: dict[int, Insert] = {}
        for ins in inserts:
            if ins.anchor != "after_next_cut":
                continue
            for i, cut in enumerate(cuts):
                if cut.src_start >= ins.src_t and i not in bound:
                    bound[i] = ins
                    break

        # ── Построить сегменты по Cut-ам ─────────────────────────────────────
        cursor = 0.0
        for i, cut in enumerate(cuts):
            if cut.src_start > cursor:
                segments.append(VideoSegment(cursor, cut.src_start))
            if i in bound:
                ins = bound[i]
                dur = _synth_duration(ins.kind, ins.meta)
                seg = SynthSegment(ins.kind, dur,
                                   meta=dict(ins.meta),
                                   src_t=cut.src_start)
                segments.append(seg)
                synth_inserts.append((seg, ins))
            cursor = cut.src_end
        if cursor < duration:
            segments.append(VideoSegment(cursor, duration))

        # Insert(at) — дробим VideoSegment
        for ins in sorted([i for i in inserts if i.anchor == "at"],
                          key=lambda i: i.src_t):
            segments, synth = _insert_at(segments, ins)
            if synth is not None:
                synth_inserts.append((synth, ins))

    else:
        # ── Без Cut: всё видео + все Insert-ы по src_t ────────────────────────
        segments = [VideoSegment(0.0, duration)]
        for ins in sorted(inserts, key=lambda i: i.src_t):
            segments, synth = _insert_at(segments, ins)
            if synth is not None:
                synth_inserts.append((synth, ins))

    # ── Шаг 3: prefix sum → out_start ─────────────────────────────────────────
    out_pos = 0.0
    for seg in segments:
        seg.out_start = out_pos
        out_pos += seg.duration
    total_duration = out_pos

    # ── Шаг 4: MixAudio src_t → out_t ─────────────────────────────────────────
    tl = Timeline(
        segments=segments,
        total_duration=total_duration,
        filters=filters,
        overlays=overlays,
        audio_mixes=[],
    )
    audio_mixes = [
        (op.path, op.src_t, tl.src_to_out_clamp(op.src_t))
        for op in mix_ops
    ]
    tl.audio_mixes = audio_mixes

    # ── Шаг 5: fade_hud_in в out_t — после каждой zoom_animation ──────────────
    # post_zoom в src_t предшествует Cut (= zoom_animation), поэтому его нельзя
    # маппить через src_to_out_clamp — он оказывается ДО анимации.
    # Правильная позиция: сразу после SynthSegment.out_end.
    # Матчинг: ins.src_t ≈ post_zoom.src_start (оба = конец zooming).
    post_zoom_ivs = [iv for iv in session.actors.get("robot", [])
                     if iv.state == "post_zoom"]
    out_filters: list[tuple[str, float, float]] = []
    for seg, ins in synth_inserts:
        if ins.kind != "zoom_animation":
            continue
        # fade_hud_in после анимации
        from .rules import FADE_IN_SEC
        for pz in post_zoom_ivs:
            if abs(pz.src_start - ins.src_t) < 0.1:
                out_filters.append(
                    ("fade_hud_in", seg.out_end, seg.out_end + FADE_IN_SEC)
                )
                break
        # fade_hud_out теперь в timeline.filters (src_t, 2s из pre_zoom_fade)
        # out_filters покрывает только fade_hud_in (нет src_t после SynthSegment)
    tl.out_filters = out_filters

    return tl


def _insert_at(segments: list[Segment],
               ins: Insert) -> tuple[list[Segment], SynthSegment | None]:
    """Вставить SynthSegment в точку ins.src_t, разрезав VideoSegment если нужно.

    Возвращает (новый список сегментов, созданный SynthSegment | None).
    """
    result: list[Segment] = []
    dur    = _synth_duration(ins.kind, ins.meta)
    synth  = SynthSegment(ins.kind, dur, meta=dict(ins.meta), src_t=ins.src_t)
    placed = False

    for seg in segments:
        if placed or not isinstance(seg, VideoSegment):
            result.append(seg)
            continue
        if seg.src_start <= ins.src_t <= seg.src_end:
            if ins.src_t > seg.src_start:
                result.append(VideoSegment(seg.src_start, ins.src_t))
            result.append(synth)
            if ins.src_t < seg.src_end:
                result.append(VideoSegment(ins.src_t, seg.src_end))
            placed = True
        else:
            result.append(seg)

    if not placed:
        result.append(synth)
    return result, synth if placed else None


# ── Отладочный вывод ──────────────────────────────────────────────────────────

def print_timeline(tl: Timeline) -> None:
    print(f"Timeline: {len(tl.segments)} сегментов, "
          f"total={tl.total_duration:.2f}s")
    for seg in tl.segments:
        print(f"  {seg}")
    if tl.audio_mixes:
        print(f"  Audio mixes: {len(tl.audio_mixes)}")
        for path, src_t, out_t in tl.audio_mixes:
            print(f"    {path.name}  src={src_t:.2f}s  out={out_t:.2f}s")
