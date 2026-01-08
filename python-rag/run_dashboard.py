#!/usr/bin/env python3
"""Start the TruLens Dashboard to view evaluations."""

from trulens.core import TruSession

def main():
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║   TruLens Dashboard                                       ║
    ║                                                           ║
    ║   View at: http://localhost:8501                          ║
    ╚═══════════════════════════════════════════════════════════╝
    """)

    session = TruSession()
    session.run_dashboard(port=8501, force=True)

if __name__ == "__main__":
    main()
