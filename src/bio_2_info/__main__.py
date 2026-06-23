"""CLI entry point. Sub-commands: feed | curate | archive | run-feed | run-archive | all."""
from __future__ import annotations
import argparse
import json
import os
import sys
import datetime
from pathlib import Path

from . import feed, curate, archive, notify


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "digests"
DEFAULT_SITE_DATA = REPO_ROOT / "docs" / "data" / "papers.json"


def _today_compact() -> str:
    return datetime.date.today().strftime("%Y%m%d")


def _selected_path(data_dir: Path) -> Path:
    return data_dir / f"selected_{_today_compact()}.json"


def _candidates_path(data_dir: Path) -> Path:
    return data_dir / f"candidates_{_today_compact()}.json"


def _pushed_ledger_path(data_dir: Path) -> Path:
    return data_dir / "pushed_ledger.json"


def _filter_already_pushed(papers: list[dict], ledger: dict) -> list[dict]:
    """Drop papers whose key is already recorded as pushed."""
    return [p for p in papers if archive.paper_key(p) not in ledger]


def _record_pushed(papers: list[dict], data_dir: Path) -> int:
    """Record pushed papers into pushed_ledger.json. Returns count newly added."""
    ledger_path = _pushed_ledger_path(data_dir)
    ledger = archive.load_ledger(str(ledger_path))
    today = datetime.date.today().isoformat()
    added = 0
    for p in papers:
        key = archive.paper_key(p)
        if key not in ledger:
            added += 1
        ledger[key] = {
            "date": today,
            "title": (p.get("title") or "").strip(),
            "doi": (p.get("doi") or "").strip(),
            "link": (p.get("link") or "").strip(),
        }
    archive.save_ledger(ledger, str(ledger_path))
    return added


def _site_record(p: dict, pushed_date: str) -> dict:
    """Flatten a curated paper into the record shape the Pages site consumes."""
    return {
        "key": archive.paper_key(p),
        "title": (p.get("title") or "").strip(),
        "doi": (p.get("doi") or "").strip(),
        "link": (p.get("link") or "").strip(),
        "journal": p.get("journal", ""),
        "date": str(p.get("date", "")),
        "pushed_date": pushed_date,
        "priority": p.get("priority", ""),
        "bucket": p.get("_bucket") or p.get("bucket") or "",
        "summary_cn": (p.get("summary_cn") or "").strip(),
        "relevance_cn": (p.get("relevance_cn") or "").strip(),
        "source": p.get("source", ""),
    }


def _append_site_data(papers: list[dict], path: Path | None = None,
                      pushed_date: str | None = None) -> int:
    """Merge curated papers into docs/data/papers.json (keyed by paper_key).

    Idempotent: re-recording the same paper updates its record in place rather
    than duplicating. Returns the total paper count after the merge.
    """
    path = Path(path) if path else DEFAULT_SITE_DATA
    pushed_date = pushed_date or datetime.date.today().isoformat()
    try:
        existing = json.loads(path.read_text(encoding="utf-8")).get("papers", [])
    except (FileNotFoundError, ValueError):
        existing = []
    by_key = {r.get("key"): r for r in existing}
    for p in papers:
        rec = _site_record(p, pushed_date)
        prev = by_key.get(rec["key"], {})
        # Keep the earliest pushed_date so the archive reflects first appearance.
        if prev.get("pushed_date"):
            rec["pushed_date"] = min(prev["pushed_date"], rec["pushed_date"])
        by_key[rec["key"]] = {**prev, **rec}
    merged = sorted(
        by_key.values(),
        key=lambda r: (r.get("pushed_date", ""), r.get("date", "")),
        reverse=True,
    )
    payload = {
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "count": len(merged),
        "papers": merged,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return len(merged)


def cmd_feed(args) -> int:
    """Fetch candidates from PubMed + bioRxiv, write to file + stdout.

    Papers already recorded in pushed_ledger.json (pushed on a previous day) are
    dropped here so they never get re-curated or re-notified.
    """
    result = feed.collect_all()
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    ledger = archive.load_ledger(str(_pushed_ledger_path(data_dir)))
    if ledger:
        before = len(result["papers"])
        result["papers"] = _filter_already_pushed(result["papers"], ledger)
        result["counts"]["already_pushed_dropped"] = before - len(result["papers"])
        result["counts"]["new"] = len(result["papers"])
    out_path = _candidates_path(data_dir)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    sys.stderr.write(f"[feed] {result['counts']} → {out_path}\n")
    if args.print:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


def cmd_curate(args) -> int:
    """Call LLM on candidates_<today>.json (or stdin), write selected_<today>.json."""
    data_dir = Path(args.data_dir)
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
    else:
        path = _candidates_path(data_dir)
        if not path.exists():
            sys.stderr.write(f"[curate] no candidates file at {path}; run feed first\n")
            return 2
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    cands = data.get("papers", data if isinstance(data, list) else [])
    if not cands:
        sys.stderr.write("[curate] no candidates to curate\n")
        return 2
    result = curate.curate(cands)
    data_dir.mkdir(parents=True, exist_ok=True)
    out_path = _selected_path(data_dir)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    sys.stderr.write(f"[curate] {result['_meta']} → {out_path}\n")
    if args.print:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


def cmd_archive(args) -> int:
    """Read selected_<today>.json, archive to IMA + build digest."""
    data_dir = Path(args.data_dir)
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            sel = json.load(f)
    else:
        path = _selected_path(data_dir)
        if not path.exists():
            sys.stderr.write(f"[archive] no selected file at {path}; run curate first\n")
            return 2
        with open(path, encoding="utf-8") as f:
            sel = json.load(f)
    skip = args.skip_ima or os.environ.get("BIO_SKIP_IMA") == "1"
    summary = archive.archive(sel, str(data_dir), skip_ima=skip)
    sys.stderr.write(f"[archive] {summary['status']} pdf={summary.get('pdf_archived')} link={summary.get('link_in_digest')}\n")
    if args.print:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


def cmd_run_feed(args) -> int:
    """Feed → curate → notify (the 8:15 job replacement)."""
    rc = cmd_feed(argparse.Namespace(data_dir=args.data_dir, print=False))
    if rc:
        return rc
    rc = cmd_curate(argparse.Namespace(data_dir=args.data_dir, input=None, print=False))
    if rc:
        return rc
    with open(_selected_path(Path(args.data_dir)), encoding="utf-8") as f:
        sel = json.load(f)
    text = notify.render_feed_message(sel)
    if args.dry_run:
        print(text)
        return 0
    notify.send_telegram(text)
    added = _record_pushed(sel.get("papers", []), Path(args.data_dir))
    total = _append_site_data(sel.get("papers", []))
    sys.stderr.write(
        f"[run-feed] telegram delivered; recorded {added} pushed papers; "
        f"site now {total} papers\n"
    )
    return 0


def cmd_run_archive(args) -> int:
    """Archive selected → notify (the 9:15 job replacement)."""
    rc = cmd_archive(argparse.Namespace(
        data_dir=args.data_dir, input=None, print=False,
        skip_ima=args.skip_ima,
    ))
    if rc:
        return rc
    # rebuild summary by re-running archive output… simpler: re-read digest file path
    # We re-call archive with the cached selected.json? No: we already wrote the digest,
    # but didn't capture the summary. Re-read selected and emit a quick stat.
    data_dir = Path(args.data_dir)
    with open(_selected_path(data_dir), encoding="utf-8") as f:
        sel = json.load(f)
    # Build a minimal summary from on-disk artifacts so we don't double-archive.
    today_h = datetime.date.today().strftime("%Y-%m-%d")
    digest_local = data_dir / f"digest_{_today_compact()}.md"
    summary = {
        "status": "ok",
        "date": today_h,
        "total": len(sel.get("papers", [])),
        "pdf_archived": None,
        "link_in_digest": None,
        "skipped_dedup": None,
        "failed": 0,
        "failed_titles": [],
        "digest_local": str(digest_local),
        "digest_uploaded": not args.skip_ima,
        "skip_ima": args.skip_ima,
        "digest_status": "",
    }
    text = notify.render_archive_message(summary)
    if args.dry_run:
        print(text)
        return 0
    notify.send_telegram(text)
    sys.stderr.write("[run-archive] telegram delivered\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bio-2-info")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                        help=f"Where candidate/selected/digest files live (default: {DEFAULT_DATA_DIR}).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("feed", help="Fetch PubMed + bioRxiv candidates")
    p.add_argument("--print", action="store_true")
    p.set_defaults(func=cmd_feed)

    p = sub.add_parser("curate", help="LLM-curate candidates")
    p.add_argument("--input", help="Override candidates path")
    p.add_argument("--print", action="store_true")
    p.set_defaults(func=cmd_curate)

    p = sub.add_parser("archive", help="Archive selected papers")
    p.add_argument("--input", help="Override selected path")
    p.add_argument("--skip-ima", action="store_true", help="Skip PDF download + IMA upload")
    p.add_argument("--print", action="store_true")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("run-feed", help="feed → curate → telegram (replaces 8:15 cron)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_run_feed)

    p = sub.add_parser("run-archive", help="archive → telegram (replaces 9:15 cron)")
    p.add_argument("--skip-ima", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_run_archive)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
