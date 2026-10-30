"""Trusted native renderer. Only validated JSON enters this fixed program.

No model-generated Python, eval, shell, LaTeX compiler, network or arbitrary assets.
Animation dependencies are optional and are imported only in this child process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path


def probe(path: Path, decode: bool = False) -> dict:
    import av

    with av.open(str(path)) as container:
        if len(container.streams.video) != 1 or container.streams.audio:
            raise ValueError("必须恰有一条视频流且没有音频")
        stream = container.streams.video[0]
        duration = (
            float(stream.duration * stream.time_base)
            if stream.duration
            else float(container.duration / av.time_base)
        )
        metadata = {
            "width": stream.width,
            "height": stream.height,
            "fps": float(stream.average_rate),
            "codec": stream.codec_context.name,
            "pix_fmt": stream.codec_context.pix_fmt,
            "duration_seconds": duration,
            "audio_streams": 0,
        }
        if (
            metadata["width"] != 1280
            or metadata["height"] != 720
            or abs(metadata["fps"] - 30) > 0.01
        ):
            raise ValueError("视频规格必须为 1280×720 / 30 fps")
        if metadata["codec"] != "h264" or metadata["pix_fmt"] != "yuv420p" or duration <= 0:
            raise ValueError("视频编码或时长无效")
        if decode:
            count = 0
            last_time = -1
            for frame in container.decode(video=0):
                timestamp = float(frame.pts * frame.time_base)
                if timestamp <= last_time:
                    raise ValueError("解码时间戳不递增")
                last_time = timestamp
                count += 1
            if abs(count / 30 - duration) > 0.15:
                raise ValueError("完整解码帧数与时长不一致")
            metadata["decoded_frames"] = count
        return metadata


def extract(path: Path, times: list[float], directory: Path) -> list[dict]:
    import av

    frames = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for i, target in enumerate(times):
            container.seek(max(0, int(target / stream.time_base)), stream=stream)
            for frame in container.decode(video=0):
                actual = float(frame.pts * frame.time_base)
                if actual + 0.02 >= target:
                    dest = directory / f"frame_{i:03d}.png"
                    frame.to_image().save(dest)
                    frames.append(
                        {"path": dest.name, "requested_seconds": target, "actual_seconds": actual}
                    )
                    break
    if len(frames) != len(times):
        raise ValueError("关键节拍抽帧不完整")
    return frames


def font_name(requested: str) -> str:
    import manimpango

    available = set(manimpango.list_fonts())
    choices = (
        [requested]
        if requested != "auto"
        else [
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "Source Han Sans SC",
            "SimHei",
            "WenQuanYi Zen Hei",
        ]
    )
    for choice in choices:
        if choice in available:
            return choice
    raise ValueError("未找到中文字体，请安装 Noto Sans CJK SC 或配置 video_font")


def render(data: dict, directory: Path) -> dict:
    import numpy as np
    from manim import (
        LEFT,
        RIGHT,
        UP,
        Arrow,
        Axes,
        Circle,
        DashedLine,
        Dot,
        FadeIn,
        FadeOut,
        GrowArrow,
        Indicate,
        Line,
        Polygon,
        RoundedRectangle,
        Scene,
        SVGMobject,
        Text,
        VGroup,
        tempconfig,
    )
    from matplotlib import rc_context
    from matplotlib.font_manager import FontProperties
    from matplotlib.mathtext import math_to_image

    from manimagent.animation_models import PlannedScene

    spec = PlannedScene.model_validate(data["scene"])
    font = font_name(data.get("font", "auto"))
    palette = {
        "blue": "#69A9FF",
        "gold": "#F4CC77",
        "teal": "#62DCC4",
        "red": "#FC8E99",
        "white": "#E9F0FF",
        "muted": "#98ACC7",
    }
    evidence = []
    formulas_dir = directory / "formulas"
    formulas_dir.mkdir(exist_ok=True)

    def label(text, size=28, color="white"):
        return Text(
            text, font=font, font_size=size, color=palette.get(color, color), line_spacing=0.8
        )

    def fit(mob, width, height, minimum=0.78):
        scale = min(1, width / max(mob.width, 0.001), height / max(mob.height, 0.001))
        if scale < minimum:
            raise ValueError("layout: 文字或公式过密，缩小后无法保证可读性，请缩短当前节拍")
        mob.scale(scale)
        return mob

    def wrapped(text, width=42):
        # A CJK glyph uses one column; Latin text is grouped by spaces where possible.
        rows, row, weight = [], "", 0.0
        for char in text:
            delta = 1 if ord(char) > 127 else 0.53
            if char == "\n" or weight + delta > width:
                rows.append(row)
                row, weight = "", 0
            if char != "\n":
                row += char
                weight += delta
        if row:
            rows.append(row)
        return "\n".join(rows)

    def formula(text):
        target = formulas_dir / (
            hashlib.sha256(("transparent-2" + text).encode()).hexdigest() + ".svg"
        )
        if not target.exists():
            with rc_context(
                {
                    "savefig.transparent": True,
                    "savefig.facecolor": "none",
                    "figure.facecolor": "none",
                }
            ):
                math_to_image(
                    "$" + text + "$",
                    str(target),
                    prop=FontProperties(size=48),
                    format="svg",
                    color=palette["white"],
                )
            tree = ET.parse(target)
            for parent in tree.iter():
                for child in list(parent):
                    if child.get("id") == "patch_1":
                        parent.remove(child)
            tree.write(target, encoding="utf-8", xml_declaration=True)
        return SVGMobject(str(target), height=0.58)

    def graphic(diagram):
        group, objects, arrows = VGroup(), {}, []
        occupied = []
        if diagram.kind == "plane":
            scale = min(
                5.35 / (diagram.x_range[1] - diagram.x_range[0]),
                3.35 / (diagram.y_range[1] - diagram.y_range[0]),
            )
            axes = Axes(
                x_range=[*diagram.x_range, 1],
                y_range=[*diagram.y_range, 1],
                x_length=(diagram.x_range[1] - diagram.x_range[0]) * scale,
                y_length=(diagram.y_range[1] - diagram.y_range[0]) * scale,
                tips=False,
                axis_config={"color": "#61758D", "stroke_width": 1.6, "include_ticks": True},
            )
            axes.move_to(np.array([-3.45, 0.05, 0]))
            group.add(axes)
            for axis, axis_bounds in [("x", diagram.x_range), ("y", diagram.y_range)]:
                stride = max(1, math.ceil((axis_bounds[1] - axis_bounds[0]) / 7))
                for number in range(
                    math.ceil(axis_bounds[0]), math.floor(axis_bounds[1]) + 1, stride
                ):
                    if not number:
                        continue
                    tick = label(str(number), 18, "muted")
                    tick.move_to(
                        axes.c2p(number, 0) + np.array([0, -0.22, 0])
                        if axis == "x"
                        else axes.c2p(0, number) + np.array([-0.22, 0, 0])
                    )
                    group.add(tick)
                    occupied.append(bounds(tick))
            for obj in diagram.objects:
                points = [axes.c2p(*p) for p in obj.points]
                color = palette[obj.color]
                if obj.kind == "vector":
                    shape = Arrow(
                        points[0],
                        points[1],
                        buff=0,
                        color=color,
                        stroke_width=4,
                        max_tip_length_to_length_ratio=0.13,
                    )
                    arrows.append(shape)
                elif obj.kind == "point":
                    shape = Dot(points[0], radius=0.065, color=color)
                elif obj.kind in {"segment", "dashed"}:
                    shape = (DashedLine if obj.kind == "dashed" else Line)(
                        points[0], points[1], color=color, stroke_width=3
                    )
                elif obj.kind == "polygon":
                    shape = Polygon(*points, color=color, fill_opacity=0.12, stroke_width=3)
                else:
                    radius = abs(
                        axes.c2p(obj.points[0][0] + obj.radius, obj.points[0][1])[0] - points[0][0]
                    )
                    shape = Circle(radius=radius, color=color).move_to(points[0])
                objects[obj.object_id] = shape
                group.add(shape)
                if obj.label:
                    caption = label(obj.label, 22, obj.color)
                    placed = False
                    for delta in [0, 0.36, 0.72, -0.55, -0.9]:
                        caption.next_to(shape, UP, buff=0.12).shift([0, delta, 0])
                        caption.shift(
                            np.array(
                                [
                                    max(0, -6.25 - caption.get_left()[0])
                                    - max(0, caption.get_right()[0] + 0.65),
                                    max(0, -1.9 - caption.get_bottom()[1])
                                    - max(0, caption.get_top()[1] - 1.85),
                                    0,
                                ]
                            )
                        )
                        box = bounds(caption)
                        if not any(
                            box["left"] < b["right"] + 0.06
                            and box["right"] > b["left"] - 0.06
                            and box["bottom"] < b["top"] + 0.04
                            and box["top"] > b["bottom"] - 0.04
                            for b in occupied
                        ):
                            occupied.append(box)
                            placed = True
                            break
                    if not placed:
                        raise ValueError("layout: 图形标签重叠，请减少同屏对象或缩短标签")
                    group.add(caption)
        elif diagram.kind == "matrix":
            rows, cols = len(diagram.matrix), len(diagram.matrix[0])
            values = VGroup()
            for r, row in enumerate(diagram.matrix):
                for c, value in enumerate(row):
                    values.add(
                        label(f"{value:g}", 38).move_to(
                            np.array([(c - (cols - 1) / 2) * 0.95, ((rows - 1) / 2 - r) * 0.7, 0])
                        )
                    )
            brackets = VGroup(label("[", 100, "blue"), label("]", 100, "blue"))
            brackets[0].stretch_to_fit_height(max(1.3, rows * 0.7)).next_to(values, LEFT, buff=0.18)
            brackets[1].stretch_to_fit_height(max(1.3, rows * 0.7)).next_to(
                values, RIGHT, buff=0.18
            )
            group.add(values, brackets).move_to(np.array([-3.45, 0, 0]))
        return group, objects, arrows

    def bounds(mob):
        return {
            "left": float(mob.get_left()[0]),
            "right": float(mob.get_right()[0]),
            "bottom": float(mob.get_bottom()[1]),
            "top": float(mob.get_top()[1]),
        }

    class TeachingScene(Scene):
        def construct(self):
            header = fit(label(data["title"], 26), 10.5, 0.5).move_to(
                np.array([-6.5, 3.48, 0]), aligned_edge=LEFT
            )
            count = label(
                f"{data['scene_index'] + 1:02d} / {data['scene_count']:02d}", 22, "muted"
            ).move_to(np.array([5.85, 3.48, 0]))
            divider = Line([-6.65, 3.05, 0], [6.65, 3.05, 0], color="#33465F", stroke_width=1)
            scene_title = fit(label(spec.title, 24, "blue"), 12.5, 0.45).move_to(
                np.array([-6.5, 2.65, 0]), aligned_edge=LEFT
            )
            caption_card = RoundedRectangle(
                width=13.1,
                height=1.32,
                corner_radius=0.15,
                stroke_width=0,
                fill_color="#18283D",
                fill_opacity=1,
            ).move_to([0, -2.83, 0])
            self.add(header, count, divider, scene_title, caption_card)
            previous = VGroup()
            for index, beat in enumerate(spec.beats):
                start = float(self.time)
                if len(previous):
                    self.play(FadeOut(previous), run_time=0.25)
                else:
                    self.wait(0.25, frozen_frame=True)
                diagram, objects, arrows = graphic(beat.diagram)
                has_graphic = beat.diagram.kind != "none"
                center = np.array([3.35 if has_graphic else 0, 0.2, 0])
                width = 6.05 if has_graphic else 11.7
                title = fit(label(beat.headline, 29, "gold"), width, 0.55).move_to(
                    center + [0, 1.55, 0]
                )
                equations = VGroup(*(formula(s) for s in beat.formulas))
                if len(equations):
                    for equation in equations:
                        fit(equation, width, 0.72, minimum=0.68)
                    equations.arrange(-UP, buff=0.32)
                    fit(equations, width, 2.3, minimum=0.82).move_to(center)
                else:
                    equations = fit(
                        label(wrapped(beat.screen_text, 18 if has_graphic else 34), 30), width, 2.2
                    ).move_to(center)
                caption = fit(label(wrapped(beat.screen_text), 28), 12.4, 1.0).move_to(
                    [0, -2.83, 0]
                )
                page = label(f"{index + 1} / {len(spec.beats)}  ·  无声教学", 17, "muted").move_to(
                    [5.4, -3.76, 0]
                )
                content = VGroup(title, equations, caption, page, diagram)
                for part in [title, equations, caption, diagram]:
                    b = bounds(part)
                    if (
                        b["left"] < -6.7
                        or b["right"] > 6.7
                        or b["bottom"] < -3.53
                        or b["top"] > 2.25
                    ):
                        raise ValueError("layout: 内容越出安全边距")
                self.play(FadeIn(VGroup(title, equations, caption, page)), run_time=0.35)
                # Draw vectors; the rest of the diagram appears in the same fixed window.
                others = VGroup(*(part for part in diagram if all(part is not a for a in arrows)))
                animations = [FadeIn(others)] if len(others) else []
                animations.extend(GrowArrow(a) for a in arrows)
                if animations:
                    self.play(*animations, run_time=0.4)
                else:
                    self.wait(0.4, frozen_frame=True)
                stable = float(self.time)
                cue = max(stable, start + beat.cue_seconds - beat.start_seconds)
                if cue > self.time:
                    self.wait(cue - self.time, frozen_frame=True)
                highlight = (
                    VGroup(*(objects[identifier] for identifier in beat.cue_objects))
                    if beat.cue_objects
                    else equations
                )
                self.play(Indicate(highlight, scale_factor=1, color=palette["gold"]), run_time=0.5)
                duration = beat.end_seconds - beat.start_seconds
                if start + duration < self.time:
                    raise ValueError("timing: 动作超过分配时间")
                self.wait(start + duration - self.time, frozen_frame=True)
                evidence.append(
                    {
                        "beat_id": beat.beat_id,
                        "unit_ids": beat.unit_ids,
                        "start_seconds": start,
                        "stable_seconds": stable,
                        "cue_seconds": cue,
                        "end_seconds": float(self.time),
                        "screen_text": beat.screen_text,
                        "formulas": beat.formulas,
                        "objects": list(objects),
                        "caption_bounds": bounds(caption),
                        "content_bounds": bounds(VGroup(title, equations, diagram)),
                        "time_source": "estimated",
                        "frame_seconds": min(float(self.time) - 0.2, stable + 0.6),
                    }
                )
                previous = content
                self.add(content)

    with tempconfig(
        {
            "renderer": "cairo",
            "pixel_width": 1280,
            "pixel_height": 720,
            "frame_rate": 30,
            "background_color": "#0C1728",
            "media_dir": str(directory / "media"),
            "video_dir": str(directory),
            "output_file": "scene.mp4",
            "format": "mp4",
            "verbosity": "ERROR",
            "progress_bar": "none",
            "disable_caching": False,
            "max_files_cached": 200,
            "write_to_movie": True,
            "dry_run": data.get("layout_only", False),
            "skip_animations": data.get("layout_only", False),
        }
    ):
        scene = TeachingScene()
        scene.render()
    if data.get("layout_only"):
        return {"status": "success", "font": font, "events": evidence, "layout_only": True}
    path = directory / "scene.mp4"
    metadata = probe(path)
    frames = extract(path, [e["frame_seconds"] for e in evidence], directory)
    return {
        "status": "success",
        "video_path": path.name,
        "media": metadata,
        "events": evidence,
        "frames": frames,
        "font": font,
    }


def assemble(data: dict, directory: Path) -> dict:
    import imageio_ffmpeg

    videos = [directory / name for name in data["videos"]]
    if any(p.parent != directory or not p.is_file() for p in videos):
        raise ValueError("合成只接受本目录的场景文件")
    infos = [probe(p) for p in videos]
    (directory / "concat.txt").write_text(
        "".join(f"file '{p.name}'\n" for p in videos), encoding="utf-8"
    )
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            "concat.txt",
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            "complete.mp4",
        ],
        cwd=directory,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("视频拼接失败：" + result.stderr.decode(errors="replace")[-1200:])
    metadata = probe(directory / "complete.mp4", decode=True)
    boundaries, total = [], 0.0
    for info in infos[:-1]:
        total += info["duration_seconds"]
        boundaries.extend([max(0, total - 0.1), total + 0.1])
    frames = extract(
        directory / "complete.mp4",
        boundaries + [max(0, metadata["duration_seconds"] - 0.5)],
        directory,
    )
    return {"status": "success", "video_path": "complete.mp4", "media": metadata, "frames": frames}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["render", "assemble", "check", "layout"])
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    directory = args.input.resolve().parent
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if args.action == "check":
            import av
            import imageio_ffmpeg
            import manim

            subprocess.run(
                [imageio_ffmpeg.get_ffmpeg_exe(), "-version"],
                capture_output=True,
                check=True,
                timeout=10,
            )
            result = {
                "status": "success",
                "font": font_name(data.get("font", "auto")),
                "manim": manim.__version__,
                "av": av.__version__,
            }
        else:
            if args.action == "layout":
                data["layout_only"] = True
            result = (
                render(data, directory)
                if args.action in {"render", "layout"}
                else assemble(data, directory)
            )
    except Exception as exc:
        (directory / "error.txt").write_text(traceback.format_exc()[-12000:], encoding="utf-8")
        result = {"status": "error", "message": str(exc)[:1000]}
    (directory / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
