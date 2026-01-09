"""Start the TruLens dashboard."""

from trulens.core import TruSession

if __name__ == "__main__":
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║   TruLens Dashboard                                       ║
    ║                                                           ║
    ║   View at: http://localhost:8502                          ║
    ╚═══════════════════════════════════════════════════════════╝
    """)

    session = TruSession()
    session.run_dashboard(port=8502, force=True)
