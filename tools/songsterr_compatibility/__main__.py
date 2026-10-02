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
from .reports import compare_reports, implementation_identity, reference_identity
from .audit import REFERENCE
from .reduce import reduce_case

def main():
    p = argparse.ArgumentParser(description="Offline compatibility development checks; no audio download or publication.")
    p.add_argument("command", choices=["matrix", "manifest", "corpus", "catalog", "compare"])
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--worker", type=Path)
    p.add_argument("--node", default="node")
    p.add_argument("--manifest", type=Path)
    p.add_argument("--imports", type=Path)
    p.add_argument("--evidence", type=Path)
    p.add_argument("--trace", action="store_true")
    p.add_argument("--event-details", action="store_true",
                   help="Capture native generated events; diagnostic evidence, not game equivalence")
    p.add_argument("--before", type=Path)
    p.add_argument("--after", type=Path)
    p.add_argument("--reduce-failures", action="store_true",
                   help="Bounded reduction of unexpected synthetic matrix differences only")
    args = p.parse_args()
    if args.output.exists():
        p.error("Output already exists; choose a new report path.")
    if args.command == "compare":
        if not args.before or not args.after: p.error("--before and --after are required")
        report = compare_reports(json.loads(args.before.read_text(encoding="utf-8-sig")),
                                 json.loads(args.after.read_text(encoding="utf-8-sig")))
    elif args.command == "manifest":
        if not args.imports or not args.evidence: p.error("--imports and --evidence are required")
        report = manifest_from_evidence(args.imports, args.evidence)
    elif args.command == "catalog":
        report = inventory()
    elif args.command == "matrix":
        generated = list(cases())
        report = run(generated, worker=args.worker, node=args.node, trace=args.trace, event_details=args.event_details)
        if args.reduce_failures:
            if not args.worker: p.error("--reduce-failures requires --worker")
            by_id = {c["id"]: c for c in generated}
            report["reductions"] = {r["id"]: reduce_case(by_id[r["id"]], worker=args.worker, node=args.node)
                                    for r in report["cases"] if r.get("preparationStatus") == "different"
                                    or r.get("authoredEventStatus") == "different"}
    else:
        if not args.manifest: p.error("--manifest is required")
        results = []; missing = []; node_versions = set()
        for identity, source in load_manifest(args.manifest):
            if source is None: missing.append(identity); continue
            checked = run([{"id": identity["id"], "source": source}], worker=args.worker, node=args.node, trace=args.trace,
                          event_details=args.event_details)
            results.extend(checked["cases"])
            node_versions.update((checked.get("referenceIdentity") or {}).get("nodeVersions", []))
            print(f"Assessed {len(results)} sources", flush=True)
        report = {"version": 2, "scope": checked["scope"] if results else "No sources tested",
                  "implementation": implementation_identity(),
                  "referenceIdentity": {**reference_identity(REFERENCE), "nodeVersions": sorted(node_versions)} if args.worker else None,
                  "cases": results, "sourceUnavailable": missing,
                  "counts": dict(Counter(r["converter"]["status"] for r in results)),
                  "preparationCounts": dict(Counter(r["preparationStatus"] for r in results))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, allow_nan=False)
    print(str(args.output.resolve()))
    if args.command == "compare":
        return {"unchanged": 0, "changed": 3, "incomplete": 4}[report["status"]]
    if any(r.get("preparationStatus") == "different" or r.get("authoredEventStatus") == "different" for r in report.get("cases", [])):
        return 3
    if args.command in {"matrix", "corpus"} and (not report.get("cases") or report.get("sourceUnavailable")
            or any(r.get("preparationStatus") == "not_tested" for r in report["cases"])):
        return 4  # Incomplete evidence is not a passing reference check.
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
