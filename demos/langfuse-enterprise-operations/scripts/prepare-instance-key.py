#!/usr/bin/env python3
"""Generate an instance key privately without displaying or reading credential files."""
import argparse
import os
from pathlib import Path
import secrets
parser = argparse.ArgumentParser()
parser.add_argument('--output', default='private/instance.env')
args = parser.parse_args()
path = Path(args.output)
path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
    output.write('ADMIN_API_KEY=' + secrets.token_hex(32) + '\n')
print('Created private instance credential file. Load it into both Docker Compose and the portal adapter.')
