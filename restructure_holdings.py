"""
重组持仓研究目录：每个标的独立文件夹
- README.md — 原买入卖出卡片 / 供应链分析 / Serenity 分析
- YYYY-MM-DD.md — TradingAgents 分析报告（按日期）
用法: PYTHONIOENCODING=utf-8 .venv/Scripts/python restructure_holdings.py
"""
import os
import re
import shutil
from pathlib import Path

SRC = Path(r"E:\note\taihusha knowledge base\20 Areas\投资理财\03 持仓研究")
BACKUP = Path(r"E:\note\taihusha knowledge base\20 Areas\投资理财\03 持仓研究\_restructure_backup")

# ── 标的映射：文件名关键词 → (文件夹名, ticker) ──
STOCK_MAP = [
    # A-shares (文件名含公司名)
    ("神火股份", "神火股份", "000933.SZ"),
    ("昊华科技", "昊华科技", "600378.SS"),
    ("双环传动", "双环传动", "002472.SZ"),
    ("通鼎互联", "通鼎互联", "002491.SZ"),
    ("北方稀土", "北方稀土", "600111.SS"),
    ("京东方A", "京东方A", "000725.SZ"),
    ("京东方", "京东方A", "000725.SZ"),
    ("红星发展", "红星发展", "600367.SS"),
    ("江钨装备", "江钨装备", "600397.SS"),
    ("华天科技", "华天科技", "002185.SZ"),
    ("沃格光电", "沃格光电", "603773.SS"),
    ("中天科技", "中天科技", "600522.SS"),
    ("亨通光电", "亨通光电", "600487.SS"),
    ("凯美特气", "凯美特气", "002549.SZ"),
    ("002549", "凯美特气", "002549.SZ"),
    # US stocks
    ("NOW", "NOW", "NOW"),
    ("MRVL", "MRVL", "MRVL"),
    ("NOK", "NOK", "NOK"),
    ("IREN", "IREN", "IREN"),
    ("DRAM", "DRAM", "DRAM"),
    ("TEAM", "TEAM", "TEAM"),
    ("PATH", "PATH", "PATH"),
    ("NVDA", "NVDA", "NVDA"),
    ("TSM", "TSM", "TSM"),
    ("AVGX", "AVGX", "AVGX"),
    ("RDW", "RDW", "RDW"),
]


def find_stock(filename):
    """Match a filename to a stock entry."""
    name = filename.replace(".md", "")
    for keyword, folder, ticker in STOCK_MAP:
        if keyword in name:
            return folder, ticker
    return None, None


def extract_ta_date(filename):
    """Extract date from a TradingAgents report filename like '神火股份_2026-06-13.md'."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    return m.group(1) if m else None


def is_ta_report(content):
    """Check if a file contains TradingAgents analysis."""
    return "TradingAgents" in content


def main():
    # 1. Create backup
    print("=" * 60)
    print("Step 1: Create backup")
    if BACKUP.exists():
        shutil.rmtree(BACKUP)
    shutil.copytree(SRC, BACKUP)
    print(f"  Backup: {BACKUP}")

    # 2. Build file inventory
    print()
    print("=" * 60)
    print("Step 2: Inventory files")

    meta_files = []       # files to keep at root (持仓卡片, etc.)
    stock_files = {}      # folder_name -> {ta: [(date, content)], readme: content}
    unmatched = []        # can't classify

    for f in sorted(SRC.glob("*.md")):
        fname = f.name

        # Skip already-processed folders
        if f.is_dir():
            continue

        folder, ticker = find_stock(fname)
        if folder is None:
            # ETFs, meta files — keep at root
            if any(kw in fname for kw in ["上证科创", "创AI富国", "持仓卡片"]):
                meta_files.append(f)
                print(f"  [META] {fname} — keep at root")
            else:
                unmatched.append(f)
                print(f"  [UNKNOWN] {fname}")
            continue

        with open(f, "r", encoding="utf-8") as fh:
            content = fh.read()

        date = extract_ta_date(fname)
        has_ta = is_ta_report(content)

        if folder not in stock_files:
            stock_files[folder] = {"ticker": ticker, "ta_reports": [], "readme_content": ""}

        if date and has_ta and len(content) > 5000:
            # This is a standalone TA report
            stock_files[folder]["ta_reports"].append((date, content))
            print(f"  [TA] {fname} → {folder}/{date}.md")

            # Check if there's also original content in this file
            # (before the "TradingAgents" section)
            ta_idx = content.find("TradingAgents")
            if ta_idx > 500:
                orig = content[:ta_idx].strip()
                if not stock_files[folder]["readme_content"]:
                    stock_files[folder]["readme_content"] = orig
        else:
            # This is the original analysis page
            # Check if it has appended TA (brief or full report format)
            ta_idx = -1
            for marker in [
                "## TradingAgents 分析",
                "# TradingAgents",
                "TradingAgents 完整分析报告",
            ]:
                ta_idx = content.find(marker)
                if ta_idx > 100:
                    # Found TA appended to original content
                    break

            if ta_idx > 100:
                # Split: original content → README, TA → dated file
                orig = content[:ta_idx].strip()
                ta = content[ta_idx:].strip()
                stock_files[folder]["readme_content"] = orig
                stock_files[folder]["ta_reports"].append(("2026-06-13", ta))
                print(f"  [SPLIT] {fname} → {folder}/README.md + {folder}/2026-06-13.md")
            else:
                # Pure original content
                stock_files[folder]["readme_content"] = content
                print(f"  [README] {fname} → {folder}/README.md")

    # 3. Create folder structure
    print()
    print("=" * 60)
    print("Step 3: Create folders and write files")

    for folder, info in sorted(stock_files.items()):
        folder_path = SRC / folder
        folder_path.mkdir(exist_ok=True)
        ticker = info["ticker"]

        # Write README.md
        readme_content = info.get("readme_content", "")
        if readme_content:
            # Add ticker metadata if not present
            if "ticker:" not in readme_content[:200].lower():
                readme_header = f"---\nticker: {ticker}\n---\n\n"
                readme_content = readme_header + readme_content.lstrip("-")

            readme_path = folder_path / "README.md"
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(readme_content)
            print(f"  {folder}/README.md ({len(readme_content)} chars)")

        # Write TA reports
        for date, content in info.get("ta_reports", []):
            ta_path = folder_path / f"{date}.md"
            with open(ta_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  {folder}/{date}.md ({len(content)} chars)")

    # 4. Clean up original files
    print()
    print("=" * 60)
    print("Step 4: Clean up original files")

    for f in SRC.glob("*.md"):
        fname = f.name
        folder, _ = find_stock(fname)
        if folder is not None and fname not in ["持仓卡片.md"]:
            print(f"  Removing: {fname}")
            f.unlink()

    # 5. Summary
    print()
    print("=" * 60)
    print("RESTRUCTURE COMPLETE")
    print(f"  Folders created: {len(stock_files)}")
    print(f"  Meta files kept at root: {len(meta_files)}")
    print(f"  Unmatched: {len(unmatched)}")
    if unmatched:
        for f in unmatched:
            print(f"    - {f.name}")
    print(f"  Backup at: {BACKUP}")
    print("=" * 60)


if __name__ == "__main__":
    main()
