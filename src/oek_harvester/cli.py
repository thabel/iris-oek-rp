"""Command line interface.

    oek-harvester discover --config config.toml [--fixtures DIR]
    oek-harvester init-db  --config config.toml
    oek-harvester harvest  --config config.toml [--fixtures DIR] [--no-db] [--output DIR]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import db
from .config import Config, load_config
from .pipeline import discover, run_harvest
from .report import write_outputs
from .wfs import FixtureTransport, HttpTransport, WfsClient, WfsError


def make_transport(cfg: Config, fixtures: str | None):
    if fixtures:
        return FixtureTransport(fixtures, endpoint=f"fixture://{Path(fixtures).as_posix()}"), "fixture"
    s = cfg.source
    return HttpTransport(s.endpoint, timeout_s=s.timeout_s, max_retries=s.max_retries,
                         backoff_s=s.backoff_s, request_delay_s=s.request_delay_s,
                         user_agent=s.user_agent), "live"


def cmd_discover(cfg: Config, args) -> int:
    transport, mode = make_transport(cfg, args.fixtures)
    try:
        d = discover(WfsClient(transport, cfg.source.version), cfg)
    except WfsError as exc:
        print(json.dumps({"mode": mode, "endpoint": transport.endpoint, "error": str(exc),
                          "requests": [vars(r) for r in transport.requests_made]},
                         indent=2, default=str))
        return 2
    ft = d.capabilities.find_feature_type(cfg.source.type_name)
    print(json.dumps({
        "mode": mode, "endpoint": transport.endpoint,
        "service": d.capabilities.title, "provider": d.capabilities.provider,
        "fees": d.capabilities.fees, "access_constraints": d.capabilities.access_constraints,
        "implements_result_paging": d.capabilities.implements_paging,
        "count_default": d.capabilities.count_default,
        "feature_types": sorted(d.capabilities.feature_types),
        "layer": vars(ft) if ft else None,
        "number_matched": d.expected_count,
        "schema": d.schema.attributes if d.schema else None,
        "attribute_mapping": d.mapping_report,
        "issues": [vars(i) for i in d.issues],
    }, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_init_db(cfg: Config, args) -> int:
    with db.connect(cfg.database.url()) as conn:
        db.apply_schema(conn, cfg.database.schema)
    print(f"Schema {cfg.database.schema} ready")
    return 0


def cmd_harvest(cfg: Config, args) -> int:
    transport, mode = make_transport(cfg, args.fixtures)
    out_dir = Path(args.output) if args.output else cfg.output_dir
    if args.no_db:
        result = run_harvest(cfg, transport, mode=mode, load=False)
    else:
        with db.connect(cfg.database.url()) as conn:
            result = run_harvest(cfg, transport, mode=mode, load=True, conn=conn)
    paths = write_outputs(result, cfg, out_dir)
    print(json.dumps({"status": result.status, "run_id": result.run_id, "error": result.error,
                      "counts": result.counts,
                      "outputs": {k: str(v) for k, v in paths.items()}}, indent=2, default=str))
    return 0 if result.status == "succeeded" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oek-harvester", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("discover", "init-db", "harvest"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        if name in ("discover", "harvest"):
            p.add_argument("--fixtures", help="Serve WFS responses from this directory "
                                              "instead of the live endpoint")
        if name == "harvest":
            p.add_argument("--no-db", action="store_true", help="Build and report only")
            p.add_argument("--output", help="Output directory (default: harvest.output_dir)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    handler = {"discover": cmd_discover, "init-db": cmd_init_db, "harvest": cmd_harvest}
    return handler[args.command](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
