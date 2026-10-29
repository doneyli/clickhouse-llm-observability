#!/usr/bin/env python3
"""Private credential format adapter. Does not call Cloud or print key values.

Requires explicit approval to read a credential file outside the sidekick's
allowlisted CLI loader. The output is for that loader's --env-file option.
"""
import argparse
import os
from pathlib import Path
from dotenv import dotenv_values

PAIRS = [
    ("LANGFUSE_ORG_PUBLIC_KEY", "LANGFUSE_ORG_SECRET_KEY"),
    ("LANGFUSE_ORG_API_PUBLIC_KEY", "LANGFUSE_ORG_API_SECRET_KEY"),
    ("LANGFUSE_CLOUD_ORG_PUBLIC_KEY", "LANGFUSE_CLOUD_ORG_SECRET_KEY"),
    ("LF_ORG_PUBLIC_KEY", "LF_ORG_SECRET_KEY"),
    ("ORG_PUBLIC_KEY", "ORG_SECRET_KEY"),
    ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"),
]


def normalize(values):
    for public_name, secret_name in PAIRS:
        public, secret = values.get(public_name), values.get(secret_name)
        if public and secret:
            if not public.startswith("pk-lf-") or not secret.startswith("sk-lf-"):
                raise ValueError("The selected credential pair has unexpected key prefixes.")
            if any(char in public + secret for char in "\n\r\x00"):
                raise ValueError("Credential values contain invalid characters.")
            return (f"LANGFUSE_PUBLIC_KEY={public}\nLANGFUSE_SECRET_KEY={secret}\n"
                    "LANGFUSE_BASE_URL=https://us.cloud.langfuse.com\n")
    raise ValueError("No supported organization credential pair was found.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.destination.exists():
        raise SystemExit("Destination exists; refusing to replace private settings.")
    # Keep the values within this process and a mode-0600 private settings file.
    contents = normalize(dotenv_values(args.source))
    descriptor = os.open(args.destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as target:
        target.write(contents)
    print("Private CLI settings created. No keys printed and no Cloud action performed.")


if __name__ == "__main__":
    main()
