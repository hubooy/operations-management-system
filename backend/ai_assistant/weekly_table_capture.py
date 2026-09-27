"""Capture the complete rendered weekly table, with an explicit date and completeness contract."""
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .policy import AiError

REPORT_TIMEOUT = 45
MAX_DIMENSION = 16384
MAX_PIXELS = 24_000_000


def previous_complete_week(now=None):
    local = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(ZoneInfo("Asia/Shanghai"))
    start = local.date() - timedelta(days=local.weekday() + 7)
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


def _evaluate(command, expression):
    result = command("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True})
    if result.get("exceptionDetails"):
        raise AiError("完整周报截图准备失败", "channel_unavailable", 503)
    return result.get("result", {}).get("value")


def capture_weekly_table(command, week_start, week_end):
    expected = json.dumps({"start": week_start, "end": week_end})
    probe = """(() => {
      const expected = EXPECTED;
      const view = document.querySelector('.new-product-followup-view');
      const root = view?.querySelector('[data-weekly-report-table]');
      if (view?.dataset.reportState === 'error') return {error:'load'};
      if (!root || view.dataset.reportState !== 'ready') return null;
      const rows = Number(root.dataset.reportRowCount), total = Number(root.dataset.reportTotal);
      const columns = Number(root.dataset.reportColumnCount);
      const table = root.querySelector('table');
      if (root.dataset.reportWeekStart !== expected.start || root.dataset.reportWeekEnd !== expected.end) return {error:'week'};
      if (!table || !Number.isInteger(rows) || rows < 0 || rows > 200 || total !== rows || columns < 5 || columns > 108) return {error:'incomplete'};
      const bodyRows = [...table.querySelectorAll('tbody tr')];
      if (bodyRows.length !== Math.max(1, rows) || table.querySelectorAll('thead th').length !== columns ||
          bodyRows.some(r => r.hidden || getComputedStyle(r).display === 'none' ||
            (rows > 0 && r.cells.length !== columns))) return {error:'incomplete'};
      return {ready:true};
    })()""".replace("EXPECTED", expected)
    deadline = time.monotonic() + REPORT_TIMEOUT
    while time.monotonic() < deadline:
        state = _evaluate(command, probe)
        if isinstance(state, dict):
            if state.get("error"):
                raise AiError("周报日期、行列或加载状态不完整，未发送截图", "channel_unavailable", 503)
            if state.get("ready"):
                break
        time.sleep(.25)
    else:
        raise AiError("最近完整周的周报表格尚未就绪", "channel_unavailable", 503)

    # Clone this one section so page navigation, fixed UI and overflow containers
    # cannot cover or crop any row. No API writes or page click handlers run.
    bounds = _evaluate(command, """(async () => {
      const source = document.querySelector('[data-weekly-report-table]');
      const root = source.cloneNode(true);
      root.removeAttribute('data-weekly-report-table');
      root.id = 'teruisi-weekly-capture';
      root.querySelectorAll('button,a,[data-column-filter-control]').forEach(el => el.remove());
      root.querySelector('table').setAttribute('data-column-filter-scope','none');
      Object.assign(root.style,{position:'absolute',left:'0',top:'0',margin:'0',padding:'0',
        width:Math.max(1440, source.querySelector('table').scrollWidth + 2)+'px',
        maxWidth:'none',height:'auto',maxHeight:'none',overflow:'visible',zIndex:'2147483647',background:'#fff'});
      const style = document.createElement('style');
      style.textContent = `html,body{overflow:visible!important;margin:0!important}
        body > *:not(#teruisi-weekly-capture){visibility:hidden!important}
        #teruisi-weekly-capture,#teruisi-weekly-capture *{visibility:visible!important;animation:none!important;transition:none!important}
        #teruisi-weekly-capture .launch-followup-matrix-scroll{overflow:visible!important;max-height:none!important}
        #teruisi-weekly-capture th,#teruisi-weekly-capture td{position:static!important}
        #teruisi-weekly-capture table{width:100%!important}`;
      document.head.appendChild(style);
      document.body.appendChild(root);
      const images = [...root.querySelectorAll('img')];
      images.forEach(img => {img.loading='eager';});
      let timer;
      try {
        await Promise.race([
          Promise.all([document.fonts.ready, ...images.map(img=>img.decode())]),
          new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('assets_timeout')),10000);})
        ]);
      } finally {clearTimeout(timer);}
      if (images.some(img => !img.complete || img.naturalWidth === 0)) throw new Error('image_missing');
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      const table = root.querySelector('table');
      root.style.width = Math.max(root.offsetWidth, table.scrollWidth + 2)+'px';
      const rect = root.getBoundingClientRect();
      const last = table.querySelector('tbody tr:last-child')?.getBoundingClientRect();
      if (!last || last.bottom > rect.bottom || table.getBoundingClientRect().right > rect.right + 1) throw new Error('clipped');
      return {x:rect.left+scrollX,y:rect.top+scrollY,width:Math.ceil(rect.width),height:Math.ceil(rect.height)};
    })()""")
    if not isinstance(bounds, dict) or any(type(bounds.get(key)) not in (int, float) for key in ("x", "y", "width", "height")):
        raise AiError("完整周报截图尺寸无效", "channel_unavailable", 503)
    if not (0 <= bounds["x"] <= MAX_DIMENSION and 0 <= bounds["y"] <= MAX_DIMENSION
            and 0 < bounds["width"] <= MAX_DIMENSION and 0 < bounds["height"] <= MAX_DIMENSION
            and bounds["width"] * bounds["height"] <= MAX_PIXELS):
        raise AiError("完整周报图片尺寸超出上限，未裁切发送", "payload_too_large", 413)
    return command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
        "clip": {**bounds, "scale": 1}}).get("data")
