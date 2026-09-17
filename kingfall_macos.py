# -*- coding: utf-8 -*-
"""Desktop-Einstieg: Königssturz oder VA Reader, ohne Browserfenster.

Wird von der nativen Hülle gestartet (macOS: WKWebView in macos/,
später Windows: z. B. WebView2). Die Fachlogik bleibt plattformneutral
in den kingfall_*.py-Modulen.
"""
from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ["KINGFALL_NO_BROWSER"] = "1"
    mode = "web"
    args = [a for a in sys.argv[1:] if a]
    if "--reader" in args or "reader" in args:
        mode = "reader"
    elif "--web" in args or "web" in args:
        mode = "web"
    env_mode = (os.environ.get("KINGFALL_PROGRAM") or "").strip().lower()
    if env_mode in ("reader", "web"):
        mode = env_mode

    if mode == "reader":
        import kingfall_va_reader as target

        if "--port" not in sys.argv:
            sys.argv.extend(["--port", os.environ.get("KINGFALL_PORT") or "18766"])
        target.serve()
        return

    import kingfall_web as target

    target.serve()


if __name__ == "__main__":
    main()
