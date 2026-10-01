"""Run from the repository: python -m tools.songsterr_compatibility ..."""
import argparse
from pathlib import Path
import json
import sys
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from .audit import run, manifest_from_evidence, load_manifest
from .cases import cases
from .catalog import inventory

def main():
    p = argparse.ArgumentParser(description="Offline compatibility development checks; no audio download or publication.")
    p.add_argument("command", choices=["matrix", "manifest", "corpus", "catalog"])
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--worker", type=Path)
    p.add_argument("--node", default="node")
    p.add_argument("--manifest", type=Path)
    p.add_argument("--imports", type=Path)
    p.add_argument("--evidence", type=Path)
    p.add_argument("--trace", action="store_true")
    args = p.parse_args()
    if args.output.exists():
        p.error("Output already exists; choose a new report path.")
    if args.command == "manifest":
        if not args.imports or not args.evidence: p.error("--imports and --evidence are required")
        report = manifest_from_evidence(args.imports, args.evidence)
    elif args.command == "catalog":
        report = inventory()
    elif args.command == "matrix":
        report = run(list(cases()), worker=args.worker, node=args.node, trace=args.trace)
    else:
        if not args.manifest: p.error("--manifest is required")
        results = []; missing = []
        for identity, source in load_manifest(args.manifest):
            if source is None: missing.append(identity); continue
            checked = run([{"id": identity["id"], "source": source}], worker=args.worker, node=args.node, trace=args.trace)
            results.extend(checked["cases"])
            print(f"Assessed {len(results)} sources", flush=True)
        report = {"version": 1, "scope": checked["scope"] if results else "No sources tested",
                  "cases": results, "sourceUnavailable": missing,
                  "counts": dict(Counter(r["converter"]["status"] for r in results)),
                  "preparationCounts": dict(Counter(r["preparationStatus"] for r in results))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(str(args.output.resolve()))
    if any(r.get("preparationStatus") == "different" or r.get("authoredEventStatus") == "different" for r in report.get("cases", [])):
        return 3
    if args.command in {"matrix", "corpus"} and (not report.get("cases") or report.get("sourceUnavailable")
            or any(r.get("preparationStatus") == "not_tested" for r in report["cases"])):
        return 4  # Incomplete evidence is not a passing reference check.
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
