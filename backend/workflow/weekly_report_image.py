from __future__ import annotations

import hashlib
import html
import os
import subprocess
from pathlib import Path
from typing import Any

from django.core.management.base import CommandError


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_DIMENSION = 16_384
MAX_IMAGE_ROWS = 200


def _number(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _sparkline(values: list[int]) -> str:
    width, height, padding = 150, 42, 4
    if not values:
        values = [0]
    low, high = min(values), max(values)
    span = max(1, high - low)
    step = (width - padding * 2) / max(1, len(values) - 1)
    points = []
    for index, value in enumerate(values):
        x = padding + index * step
        y = height - padding - ((value - low) / span) * (height - padding * 2)
        points.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="周销量趋势">'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="#4777b5" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )


def render_weekly_report_html(report: dict[str, object]) -> tuple[str, int, int]:
    weeks = report.get("weeks")
    items = report.get("items")
    if not isinstance(weeks, list) or not isinstance(items, list) or not weeks:
        raise CommandError("新品周报图片数据不完整")
    if len(items) > MAX_IMAGE_ROWS:
        raise CommandError(f"新品周报图片最多支持 {MAX_IMAGE_ROWS} 条产品线")
    width = max(1_280, 516 + 116 * len(weeks))
    height = max(220, 136 + 76 * max(1, len(items)))
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise CommandError("新品周报图片尺寸超出安全上限")

    header_cells = []
    for week in weeks:
        if not isinstance(week, dict):
            raise CommandError("新品周报周维度无效")
        label = html.escape(str(week.get("label", "")))
        date_range = html.escape(str(week.get("dateRange", "")))
        completeness = "" if week.get("dataComplete") is True else '<span class="partial">数据未完整</span>'
        header_cells.append(f'<th><strong>{label}</strong><span>({date_range})</span>{completeness}</th>')

    body_rows = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise CommandError("新品周报产品线数据无效")
        values_raw = item.get("weeklyNetQuantities")
        values = [_number(value) for value in values_raw] if isinstance(values_raw, list) else []
        if len(values) != len(weeks):
            raise CommandError("新品周报产品线与周维度不一致")
        product_image_url = html.escape(str(item.get("productImageUrl") or ""), quote=True)
        product_image = (
            f'<img src="{product_image_url}" alt="" referrerpolicy="no-referrer">'
            if product_image_url else '<span class="no-image">暂无</span>'
        )
        metric_cells = "".join(f"<td>{value:,}</td>" for value in values)
        body_rows.append(
            f'<tr class="{"alternate" if index % 2 == 0 else ""}">'
            f'<td class="brand">{html.escape(str(item.get("brand", "—")))}</td>'
            f'<td class="image">{product_image}</td>'
            f'<td class="name">{html.escape(str(item.get("name", "—")))}</td>'
            f'<td class="trend">{_sparkline(values)}</td>{metric_cells}</tr>'
        )
    if not body_rows:
        body_rows.append(f'<tr><td class="empty" colspan="{4 + len(weeks)}">尚未建立新品产品线</td></tr>')

    cutoff = html.escape(str(report.get("dataCutoffDate") or "暂无"))
    timeline = html.escape(str(report.get("timelineStart") or ""))
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: data:; style-src 'unsafe-inline'"><style>
*{{box-sizing:border-box}}html,body{{margin:0;background:#fff;color:#17233c;font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif}}
.report{{width:{width}px;padding:0 1px 14px}}table{{width:100%;border-collapse:collapse;table-layout:fixed}}
th,td{{height:76px;border:1px solid #b7c9e7;text-align:center;font-size:15px}}thead th{{height:76px;background:#4477c8;color:#fff;font-weight:700}}
th strong,th span{{display:block}}th span{{margin-top:4px;font-size:12px}}th .partial{{color:#ffe5a3;font-size:10px}}
th:nth-child(1),td:nth-child(1){{width:90px}}th:nth-child(2),td:nth-child(2){{width:96px}}th:nth-child(3),td:nth-child(3){{width:180px}}th:nth-child(4),td:nth-child(4){{width:150px}}
td.brand{{font-weight:700}}td.image{{padding:6px}}td.image img{{display:block;width:58px;height:58px;margin:auto;padding:3px;object-fit:contain;object-position:center;background:#fff;border-radius:6px}}td.image .no-image{{color:#8793a6;font-size:12px}}td.name{{padding:0 10px;text-align:left;font-weight:700}}td.trend{{padding:5px 8px}}td.trend svg{{display:block;width:100%;height:44px}}
tbody tr.alternate td{{background:#dbe5f5}}tbody td{{background:#fff}}.empty{{height:82px;color:#6c7890}}
.footnote{{display:flex;justify-content:space-between;gap:24px;padding:12px 6px 0;color:#68748a;font-size:12px}}
</style></head><body><main class="report"><table><thead><tr><th>品牌</th><th>产品图</th><th>产品名称</th><th>趋势</th>{''.join(header_cells)}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>
<div class="footnote"><span>周维度自 {timeline} 起持续累积 · 数值口径：吉客云货品代码净销量</span><span>销售数据截至：{cutoff}</span></div></main></body></html>"""
    return document, width, height


def _chrome_executable() -> Path:
    configured = os.environ.get("TERUISI_WEEKLY_REPORT_CHROME", "").strip()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(local_app_data) / "Chromium" / "Application" / "chrome.exe" if local_app_data else None,
        Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe" if local_app_data else None,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and not candidate.is_symlink():
            return candidate.resolve()
    raise CommandError("未找到用于生成新品周报 PNG 的受控 Chrome")


def render_weekly_report_png(report: dict[str, object], output_path: Path) -> dict[str, object]:
    output_path = output_path.resolve()
    if output_path.exists() or not output_path.parent.is_dir():
        raise CommandError("新品周报 PNG 输出路径无效")
    document, width, height = render_weekly_report_html(report)
    html_path = output_path.with_suffix(".html")
    html_path.write_text(document, encoding="utf-8", newline="\n")
    command = [
        str(_chrome_executable()), "--headless=new", "--disable-gpu", "--disable-extensions",
        "--disable-background-networking", "--disable-sync", "--no-first-run", "--hide-scrollbars",
        f"--window-size={width},{height}", f"--screenshot={output_path}", html_path.resolve().as_uri(),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, check=False, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CommandError("新品周报 PNG 生成失败或超时") from error
    if completed.returncode != 0 or not output_path.is_file():
        raise CommandError("新品周报 PNG 生成失败")
    image = output_path.read_bytes()
    if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) < 1_024 or len(image) > MAX_IMAGE_BYTES:
        raise CommandError("新品周报 PNG 内容校验失败")
    return {
        "path": str(output_path),
        "width": width,
        "height": height,
        "sizeBytes": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
    }
