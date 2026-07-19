#!/usr/bin/env python3
"""Export the DOMjudge CCS event feed for a contest to a local NDJSON file (for archival)."""

import argparse
import getpass
import json
import sys

import requests


def last_event_id(path):
    """Read the last non-empty line of an existing NDJSON file and return its event id."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 4096
            data = b""
            while f.tell() > 0 and b"\n" not in data.strip():
                step = min(block, f.tell())
                f.seek(-step, 1)
                data = f.read(step) + data
                f.seek(-step, 1)
        lines = [l for l in data.decode("utf-8", "ignore").splitlines() if l.strip()]
        if not lines:
            return None
        return json.loads(lines[-1]).get("id")
    except FileNotFoundError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Base API URL, e.g. https://domjudge.example.org/api/v4")
    parser.add_argument("--contest", required=True, help="Contest id (cid)")
    parser.add_argument("--username", required=True, help="DOMjudge account with API reader access")
    parser.add_argument("--password", help="Password (omit to be prompted securely)")
    parser.add_argument("--output", default=None, help="Output NDJSON file (default: event_feed_<cid>.ndjson)")
    parser.add_argument("--since-id", default=None, help="Only fetch events after this event id")
    parser.add_argument("--resume", action="store_true",
                         help="Continue an existing output file: read its last event id and append from there")
    parser.add_argument("--types", default=None, help="Comma-separated event types to filter (e.g. submissions,judgements)")
    parser.add_argument("--live", action="store_true",
                         help="Keep the connection open and stream new events as they happen (default: one-shot snapshot up to now, then exit)")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification")
    args = parser.parse_args()

    password = args.password or getpass.getpass(f"Password for {args.username}: ")
    verify = not args.insecure
    if args.insecure:
        requests.packages.urllib3.disable_warnings()

    output = args.output or f"event_feed_{args.contest}.ndjson"

    since_id = args.since_id
    mode = "w"
    if args.resume:
        found = last_event_id(output)
        if found is not None:
            since_id = found
            mode = "a"
            print(f"Resuming from event id {since_id} (appending to {output})")
        else:
            print(f"No existing events found in {output}, starting from the beginning")

    params = {"stream": "true" if args.live else "false"}
    if since_id is not None:
        params["since_id"] = since_id
    if args.types:
        params["types"] = args.types

    url = f"{args.url.rstrip('/')}/contests/{args.contest}/event-feed"
    print(f"Fetching event feed ({'live stream' if args.live else 'one-shot snapshot'})...")
    if args.live:
        print("Live mode: this will keep running. Press Ctrl+C to stop.")

    count = 0
    last_id = since_id
    try:
        with requests.get(url, params=params, auth=(args.username, password),
                          stream=True, verify=verify, timeout=(10, None)) as resp:
            resp.raise_for_status()
            with open(output, mode, newline="\n") as f:
                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue  # keep-alive blank line
                    f.write(line + "\n")
                    f.flush()
                    count += 1
                    try:
                        event = json.loads(line)
                        last_id = event.get("id", last_id)
                        if count % 500 == 0:
                            print(f"  ... {count} events written (last id: {last_id})")
                    except json.JSONDecodeError:
                        pass
    except KeyboardInterrupt:
        print(f"\nInterrupted. {count} events written this run (last id: {last_id}).")
        print(f"Re-run with --resume to continue from where you left off.")
        sys.exit(0)
    except requests.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nDone. {count} events written this run.")
    print(f"Output: {output}")
    print(f"Last event id: {last_id}")


if __name__ == "__main__":
    main()
