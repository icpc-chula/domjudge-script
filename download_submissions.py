#!/usr/bin/env python3
"""Download all submissions (source code + metadata) for a DOMjudge contest via the REST API."""

import argparse
import base64
import csv
import getpass
import sys
import zipfile
from pathlib import Path

import requests


def api_get(session, base_url, path, verify):
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    resp = session.get(url, verify=verify, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_lookup(session, base_url, cid, verify, endpoint, key="id"):
    try:
        items = api_get(session, base_url, f"contests/{cid}/{endpoint}", verify)
        return {item[key]: item for item in items}
    except requests.HTTPError as e:
        print(f"  (skipping {endpoint} lookup: {e})", file=sys.stderr)
        return {}


def safe_name(value):
    return str(value).replace("/", "_").replace(" ", "_")


def zip_directory(src_dir, zip_path):
    """Zip src_dir using LZMA (best available ratio in stdlib zipfile)."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_LZMA) as zf:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src_dir.parent))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Base API URL, e.g. https://domjudge.example.org/api/v4")
    parser.add_argument("--contest", required=True, help="Contest id (cid)")
    parser.add_argument("--username", required=True, help="DOMjudge account with jury/admin access")
    parser.add_argument("--password", help="Password (omit to be prompted securely)")
    parser.add_argument("--output", default=None, help="Output directory (default: ./submissions_<cid>)")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification")
    parser.add_argument("--no-zip", action="store_true", help="Skip zipping the output directory when done")
    args = parser.parse_args()

    password = args.password or getpass.getpass(f"Password for {args.username}: ")
    verify = not args.insecure
    if args.insecure:
        requests.packages.urllib3.disable_warnings()

    out_dir = Path(args.output or f"submissions_{safe_name(args.contest)}")
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.auth = (args.username, password)

    print(f"Fetching contest {args.contest} info...")
    contest = api_get(session, args.url, f"contests/{args.contest}", verify)
    print(f"  -> {contest.get('name', contest.get('id'))}")

    print("Fetching lookup tables (teams, problems, judgement types)...")
    teams = build_lookup(session, args.url, args.contest, verify, "teams")
    problems = build_lookup(session, args.url, args.contest, verify, "problems")
    judgement_types = build_lookup(session, args.url, args.contest, verify, "judgement-types")
    judgements = build_lookup(session, args.url, args.contest, verify, "judgements", key="submission_id")

    print("Fetching submissions list...")
    submissions = api_get(session, args.url, f"contests/{args.contest}/submissions", verify)
    print(f"  -> {len(submissions)} submissions found")

    id_width = max((len(str(sub["id"])) for sub in submissions), default=0)

    def padded_id(sid):
        s = str(sid)
        return s.zfill(id_width) if s.isdigit() else s

    teams_map_path = out_dir / "teams_mapping.csv"
    with open(teams_map_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["team_id", "name", "display_name"])
        for team_id, team_obj in teams.items():
            writer.writerow([team_id, team_obj.get("name"), team_obj.get("display_name")])

    index_path = out_dir / "submissions_index.csv"
    with open(index_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["submission_id", "team_id", "problem", "language_id", "time", "verdict", "files"])

        for i, sub in enumerate(submissions, 1):
            sid = sub["id"]
            team = sub.get("team_id")
            problem = problems.get(sub.get("problem_id"), {}).get("label", sub.get("problem_id"))
            lang = sub.get("language_id")
            time_ = sub.get("time")

            judgement = judgements.get(sid)
            verdict = "unknown"
            if judgement:
                jt = judgement.get("judgement_type_id")
                verdict = judgement_types.get(jt, {}).get("name", jt or "unknown")

            sub_dir = out_dir / safe_name(team) / safe_name(problem)
            sub_dir.mkdir(parents=True, exist_ok=True)

            try:
                files = api_get(
                    session, args.url, f"contests/{args.contest}/submissions/{sid}/source-code", verify
                )
            except requests.HTTPError as e:
                print(f"  [{i}/{len(submissions)}] submission {sid}: FAILED ({e})", file=sys.stderr)
                writer.writerow([sid, team, problem, lang, time_, verdict, "ERROR"])
                continue

            filenames = []
            for file_obj in files:
                out_filename = f"{padded_id(sid)}_{file_obj['filename']}"
                content = base64.b64decode(file_obj["source"])
                (sub_dir / out_filename).write_bytes(content)
                filenames.append(out_filename)

            writer.writerow([sid, team, problem, lang, time_, verdict, ";".join(filenames)])
            print(f"  [{i}/{len(submissions)}] submission {sid}: {team}/{problem} -> {len(filenames)} file(s), verdict={verdict}")

    print(f"\nDone. Files under: {out_dir}")
    print(f"Index: {index_path}")
    print(f"Team id -> name mapping: {teams_map_path}")

    if not args.no_zip:
        zip_path = out_dir.with_suffix(".zip")
        print(f"\nZipping (LZMA, max compression) -> {zip_path} ...")
        zip_directory(out_dir, zip_path)
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"Zip created: {zip_path} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
