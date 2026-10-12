#!/usr/bin/env python3
"""One entry point for the banking workshop. All services bind to loopback."""
import argparse
import asyncio
from pathlib import Path
import os
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def listening(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def preflight(offline=False):
    import importlib.metadata
    from northwind import config, tracing
    for package in ("langfuse", "langgraph", "langchain-core", "mcp", "fastapi"):
        print(f"{package}: {importlib.metadata.version(package)}")
    print(f"Tracing: {'enabled' if tracing.ENABLED else 'disabled'}; workshop: {config.WORKSHOP_ID}")
    if offline:
        print("Offline preflight passed. No credentials or network were used.")
        return
    key = "OPENAI_API_KEY" if config.AGENT_MODEL.startswith(("gpt", "o1", "o3", "o4")) else "ANTHROPIC_API_KEY"
    if not os.getenv(key):
        raise RuntimeError(f"Set {key} in the workshop .env.")
    if tracing.ENABLED and not config.require_langfuse().auth_check():
        raise RuntimeError("Langfuse authentication failed. Check keys and region in .env.")
    print("Preflight passed. Model credentials are present; no paid model call was made.")
    if tracing.ENABLED:
        print("Langfuse authentication succeeded.")


def serve():
    from northwind import config
    ports = [config.MCP_PORT, config.PORT]
    if len(set(ports)) != 2 or any(listening(port) for port in ports):
        raise RuntimeError("Workshop ports must be distinct and free. Stop another workshop or change .env ports.")
    children = []
    try:
        for command in ("mcp", "portal"):
            children.append(subprocess.Popen([sys.executable, str(ROOT / "workshop.py"), command], cwd=ROOT))
        deadline = time.monotonic() + 25
        while not all(listening(port) for port in ports):
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A workshop service exited during startup. Read the error above.")
            if time.monotonic() >= deadline:
                raise RuntimeError("Workshop services did not become ready within 25 seconds.")
            time.sleep(.2)
        print(f"Bank workshop: http://127.0.0.1:{config.PORT} | Ctrl-C stops both services.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(.2)
        raise RuntimeError("A workshop service exited. Both services have been stopped.")
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            child.terminate()
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Use the same private file across checkpoints; never copies it.")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("preflight"); p.add_argument("--offline", action="store_true")
    commands.add_parser("serve")
    commands.add_parser("mcp")
    commands.add_parser("portal")
    p = commands.add_parser("chat"); p.add_argument("question"); p.add_argument("--customer", default="C-1001")
    p = commands.add_parser("prompt"); p.add_argument("--variant", choices=["baseline", "candidate"], default="baseline")
    p.add_argument("--label", default="workshop-baseline")
    p = commands.add_parser("dataset")
    review = p.add_mutually_exclusive_group()
    review.add_argument("--reviewed", action="store_true")
    review.add_argument("--allow-draft", action="store_true",
                        help="Explicitly authorize synthetic rehearsal data; keep human-review markers pending.")
    p = commands.add_parser("experiment"); p.add_argument("--prompt-label", default="workshop-baseline")
    p.add_argument("--model"); p.add_argument("--no-judge", action="store_true")
    p.add_argument("--dataset-version", help="Historical dataset version timestamp for a repeatable comparison.")
    p.add_argument("--run-name"); p.add_argument("--max-concurrency", type=int, default=1)
    args = parser.parse_args()
    if args.env_file:
        if not args.env_file.is_file():
            parser.error("--env-file must name an existing file.")
        os.environ["WORKSHOP_ENV_FILE"] = str(args.env_file.resolve())
    # Load settings before importing any instrumented application components.
    from northwind import config
    try:
        if args.command == "preflight":
            preflight(args.offline)
        elif args.command == "serve":
            serve()
        elif args.command == "mcp":
            from northwind.mcp_server import mcp
            mcp.run(transport="streamable-http")
        elif args.command == "portal":
            import uvicorn
            uvicorn.run("northwind.web:app", host="127.0.0.1", port=config.PORT)
        elif args.command == "chat":
            import json
            from northwind.agent import run_turn
            print(json.dumps(asyncio.run(run_turn(args.question, customer_id=args.customer)), indent=2))
        else:
            from helpers import publish_prompt, seed_dataset, run_experiment
            {"prompt": publish_prompt, "dataset": seed_dataset, "experiment": run_experiment}[args.command](args)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        config.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
