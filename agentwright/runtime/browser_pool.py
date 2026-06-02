"""Service 13: Browser Pool (Layer 0).

Production uses Playwright + Chromium per-agent contexts. That needs a Chromium
download and a display stack, so the offline default here is ``StubBrowserPool``
which returns canned, deterministic results implementing the ``BrowserPool``
protocol. A real ``PlaywrightBrowserPool`` (documented at the bottom) swaps in
behind the same protocol.

Per the design, screenshots and downloads are written to the File Store and
referenced by path — never inlined into the prompt.
"""

from __future__ import annotations

from .file_store import FileStore


class StubBrowserPool:
    """Deterministic offline browser stub.

    ``browse`` returns a fixed page record; ``screenshot`` writes a placeholder
    file to the agent's workspace and returns its path (matching the real
    contract where artifacts go to the File Store, not the prompt).
    """

    def __init__(self, file_store: FileStore) -> None:
        self._fs = file_store
        self._counter = 0

    def browse(self, agent_id: str, url: str) -> dict:
        return {
            "url": url,
            "status": 200,
            "title": f"stub page for {url}",
            "text": f"(stubbed content of {url})",
        }

    def screenshot(self, agent_id: str) -> str:
        self._counter += 1
        path = f"workspace/screenshot_{self._counter}.txt"
        self._fs.write(agent_id, path, "(stub screenshot)")
        return path


# --- Real adapter (documented; not imported unless used) ---------------------
#
# class PlaywrightBrowserPool:
#     """Production browser pool. Requires `playwright` + `playwright install chromium`.
#
#     Manages one persistent browser context per agent under
#     browser-profiles/{agent_id}/, writes screenshots/downloads to the File
#     Store, and implements browse/screenshot (plus click/type/download) with the
#     same return contract as the stub.
#     """
