#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日全量历史快照 v2 (2026-08-20 16:20 升级)

用户要求: 历史统计应包含现有界面所有内容 — 产量对比/UPH趋势/达成率趋势/未达成原因详情/出勤,
按年/月/日推移存储, 支持回看任意历史日期的完整看板数据, 便于分析判断。

存储:
  1. history/YYYY-MM-DD.json   — 当日全量快照 (数据库式归档, 按日期文件)
  2. live-data.js __HISTORY__  — 兼容旧趋势图 (达成率 lines, 保留旧逻辑)

cron: 0 17 * * * python3 save_daily_history.py
"""
import os, sys, json, re, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_data_lib import load_config, log

BASE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = os.path.dirname(BASE)  # PDTIII_Dashboard/
DATA_JSON = os.path.join(DASHBOARD, "data.json")
LIVE_JS = os.path.join(DASHBOARD, "live-data.js")
HIST_DIR = os.path.join(DASHBOARD, "history")   # 全量历史归档目录 (数据库式)

# ── 2026-08-21: 同步部署副本 (pdtiii-live) + 自动 push ──
EXTRA_HIST_DIR = None
EXTRA_GIT_DIR = None
try:
    cfg = load_config()
    extra = (cfg.get("live_data_extra") or "").strip()
    if extra:
        EXTRA_GIT_DIR = os.path.dirname(extra)          # pdtiii-live/
        EXTRA_HIST_DIR = os.path.join(EXTRA_GIT_DIR, "history")
        log(f"同步副本: {EXTRA_GIT_DIR}")
except Exception as e:
    log(f"读取配置失败: {e}")

def read_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ {path} 解析失败: {e}")
        return None

def read_live_js_text():
    if not os.path.exists(LIVE_JS):
        return None
    return open(LIVE_JS, "r", encoding="utf-8").read()

def rebuild_hist_from_archives():
    """从 history/ 目录扫描重建 __HISTORY__ 列表 (权威数据源, 2026-08-21 修复).

    旧实现用 extract_json_block 解析 live-data.js 里的数组, 但该函数只认 `{}` 对象,
    对数组只取到第一个元素 → isinstance(list) 失败 → hist 被清空 → 每天运行只保留当天,
    导致 __HISTORY__ 历史逐天丢失。现在直接从历史归档文件重建, 永不丢数据。
    """
    hist = []
    if not os.path.isdir(HIST_DIR):
        return hist
    try:
        files = sorted(f for f in os.listdir(HIST_DIR) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f))
        for fname in files:
            snap = read_json(os.path.join(HIST_DIR, fname))
            if snap and snap.get("eff"):
                hist.append({"date": fname[:-5], "lines": snap["eff"]})
        hist.sort(key=lambda h: str(h.get("date", "")))
    except Exception as e:
        log(f"⚠️ 重建 __HISTORY__ 失败: {e}")
    return hist

def extract_json_block(text, var):
    """从 JS 文本提取 window.XXX = {...} 的 JSON 对象 (找最后一次赋值)"""
    start = text.rfind(f"window.{var} =")
    if start == -1:
        return None
    start = text.find("{", start)
    depth = 0; end = -1
    for i in range(start, len(text)):
        if text[i] == "{": depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return None
    try:
        return json.loads(text[start:end])
    except Exception:
        return None

def _auto_push(git_dir, date):
    """把同步后的 history 提交并推送到 pdtiii-live 仓库 (GitHub Pages 数据源)."""
    if not git_dir or not os.path.isdir(os.path.join(git_dir, ".git")):
        log("⚠️ 跳过自动 push: 非 git 仓库")
        return
    try:
        import subprocess
        cmds = [
            ["git", "-C", git_dir, "add", "history/", "live-data.js"],
            ["git", "-C", git_dir, "commit", "-m", f"auto: {date} 历史归档同步", "--no-verify"],
            ["git", "-C", git_dir, "push", "origin", "main"],
        ]
        for c in cmds:
            r = subprocess.run(c, capture_output=True, text=True, timeout=60)
            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            if r.returncode != 0 and "nothing to commit" not in err and "Everything up-to-date" not in out and "up to date" not in out.lower():
                log(f"⚠️ git {c[3]} 返回 {r.returncode}: {err[:200]}")
            elif r.returncode != 0:
                log(f"git {c[3]}: 无新变更或已最新")
    except Exception as e:
        log(f"⚠️ 自动 push 失败: {e}")


def main():
    data = read_json(DATA_JSON)
    if not data or not data.get("lines"):
        log("⚠️ 无 lines 数据, 跳过快照")
        return 1

    today = datetime.date.today()
    date = today.strftime("%Y-%m-%d")

    # ── 2026-08-24: 系统运作时间 = 周一至周五, 周末不生成历史快照/趋势 ──
    if today.weekday() >= 5:  # 周六=5, 周日=6
        log(f"⏭️ 周末({date})不生成历史快照 (系统运作周一至周五)")
        return 0

    # ── ① 全量历史归档: history/YYYY-MM-DD.json ──
    text = read_live_js_text()
    live = {}
    # 2026-08-21 修复: __HISTORY__ 改为从 history/ 归档目录重建 (extract_json_block 对数组解析有 bug, 会丢历史)
    hist = rebuild_hist_from_archives()
    if text:
        live = extract_json_block(text, "__LIVE_DATA__") or {}

    # 达成率 lines (旧趋势图用)
    eff_map = {}
    for l in data["lines"]:
        if l and l.get("name") and l.get("eff") is not None:
            try:
                eff_map[l["name"]] = round(float(l["eff"]), 1)
            except (TypeError, ValueError):
                pass

    # ── 2026-08-25: 首小时达成率 (8-9点, extract每10分钟更新) → 并入 __HISTORY__ 当天记录 ──
    fh_map = {}
    for e in (live.get("first_hour") or []):
        if e and e.get("line") and e.get("rate") is not None:
            try:
                fh_map[e["line"]] = round(float(e["rate"]), 1)
            except (TypeError, ValueError):
                pass

    # 快照 = 界面所有内容的完整数据
    snapshot = {
        "date": date,
        "snapAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "save_daily_history v2",
        "lines": data.get("lines"),            # 主要线体产量对比 (全部字段: target/plan/actual/cb/cum/eff/status/stalled)
        "hourly": data.get("hourly"),          # 每小时产量/效率 (UPH趋势数据源)
        "eff": eff_map,                        # 达成率趋势 (线体: %)
        "problems": live.get("problems", []),  # 未达成原因详情 (全量, 含中文翻译)
        "first_hour": live.get("first_hour", []),  # 2026-08-25: 首小时达成 (8-9点, 全部线体 target/actual/rate)
        "attendance": live.get("attendance"),  # 出勤全表
        "updatedAt": data.get("updatedAt"),
    }
    os.makedirs(HIST_DIR, exist_ok=True)
    hist_path = os.path.join(HIST_DIR, date + ".json")
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=HIST_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    os.replace(tmp, hist_path)
    # 更新索引 history/index.json (降序日期列表)
    index_path = os.path.join(HIST_DIR, "index.json")
    dates = sorted([f[:-5] for f in os.listdir(HIST_DIR) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f)], reverse=True)
    fd, tmp = tempfile.mkstemp(dir=HIST_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dates, f)
    os.replace(tmp, index_path)
    log(f"📦 全量历史归档: {date}.json ({len(snapshot['lines'])}线体 / {len(snapshot['problems'])}问题 / hourly {len(snapshot.get('hourly') or {})}线) 索引{len(dates)}天")

    # ── 2026-08-21: 同步归档副本到部署目录 + 自动 push (保证线上日期回看可拉取) ──
    if EXTRA_HIST_DIR:
        try:
            os.makedirs(EXTRA_HIST_DIR, exist_ok=True)
            for fname in [date + ".json", "index.json"]:
                src = os.path.join(HIST_DIR, fname)
                dst = os.path.join(EXTRA_HIST_DIR, fname)
                if os.path.exists(src):
                    with open(src, "r", encoding="utf-8") as f:
                        content = f.read()
                    fd2, tmp2 = tempfile.mkstemp(dir=EXTRA_HIST_DIR, suffix=".tmp")
                    with os.fdopen(fd2, "w", encoding="utf-8") as f:
                        f.write(content)
                    os.replace(tmp2, dst)
            log(f"✅ 已同步历史归档 → {EXTRA_HIST_DIR}")
            _auto_push(EXTRA_GIT_DIR, date)
        except Exception as e:
            log(f"⚠️ 同步副本失败: {e}")

    # ── ② 兼容: 更新 live-data.js __HISTORY__ (达成率趋势折线用) ──
    if eff_map:
        rec = {"date": date, "lines": eff_map}
        if fh_map:
            rec["first_hour"] = fh_map
        found = False
        for i, h in enumerate(hist):
            if h.get("date") == date:
                hist[i] = dict(h)
                hist[i]["lines"] = eff_map
                if fh_map:
                    hist[i]["first_hour"] = fh_map
                found = True
                break
        if not found:
            hist.append(rec)
        hist.sort(key=lambda h: str(h.get("date", "")))
        hist = hist[-120:]
        body_live = json.dumps(live, ensure_ascii=False, indent=2)
        body_hist = json.dumps(hist, ensure_ascii=False, indent=2)
        content = (
            "/* ═══════════════════════════════════════════════════════════\n"
            "   live-data.js — 由 tools/ 下脚本定时写入（不要手改，会被覆盖）\n"
            "   - attendance: 每日各车间出勤（来源: 美的云盘 GAT Attendance）\n"
            "   - problems:   线体问题点（来源: 桌面Excel, 每2小时同步）\n"
            "   - __HISTORY__: 每日17:00达成率快照 (来源: data.json, 趋势分析用)\n"
            "   全量历史归档: history/YYYY-MM-DD.json (产量/UPH/问题点/出勤/达成率)\n"
            "   最后写入: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n"
            "   ═══════════════════════════════════════════════════════════ */\n"
            "window.__LIVE_DATA__ = " + body_live + ";\n"
            "window.__HISTORY__ = " + body_hist + ";\n"
        )
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(LIVE_JS), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, LIVE_JS)
        # 2026-08-21: 同步 live-data.js 到部署副本 (旧版只同步 history, 副本 __HISTORY__ 会滞后)
        if EXTRA_GIT_DIR:
            try:
                extra_js = os.path.join(EXTRA_GIT_DIR, "live-data.js")
                fd2, tmp2 = tempfile.mkstemp(dir=EXTRA_GIT_DIR, suffix=".tmp")
                with os.fdopen(fd2, "w", encoding="utf-8") as f2:
                    f2.write(content)
                os.replace(tmp2, extra_js)
                log(f"✅ 已同步 live-data.js → {extra_js}")
                _auto_push(EXTRA_GIT_DIR, date)
            except Exception as e:
                log(f"⚠️ 同步 live-data.js 副本失败: {e}")
        else:
            _auto_push(EXTRA_GIT_DIR, date)
        log(f"✅ 历史快照完成 {date}: {len(eff_map)} 线体达成率, 累计 {len(hist)} 天 (__HISTORY__)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
