# SPDX-License-Identifier: GPL-3.0-or-later
"""Snap/dock the chat panel into the PCB editor window as a native AUI pane.

KiCad's PCB_EDIT_FRAME is laid out by a wxAuiManager (the same mechanism
behind the Appearance/Search panels). wxPython can look that manager up via
``wx.aui.AuiManager.GetManager(frame)`` and add our own pane — the chat then
docks/snaps to the editor edges, can be torn off, resized and re-docked like
a built-in panel. If anything in that chain fails (frame not found, manager
not exposed by this KiCad build), :func:`attach` returns ``None`` and the
caller falls back to the floating dialog.

wx is imported lazily inside the functions so the pure helpers (frame
detection, pane spec) stay headless-testable.
"""

from __future__ import annotations

from typing import Callable, Optional

PANE_NAME = "kicad_mcp_claude_chat"   # stable AUI id (perspective-safe)
PANE_MIN_SIZE = (320, 240)
PANE_BEST_SIZE = (420, 620)
PANE_FLOAT_SIZE = (520, 640)

# KiCad sets locale-independent window names (PCB_EDIT_FRAME_NAME); the title
# hints are a fallback for builds where the name lookup misses.
_PCB_FRAME_NAMES = ("PcbFrame",)
_PCB_TITLE_HINTS = ("pcb editor", "pcbnew", "leiterplatteneditor")


def looks_like_pcb_editor(name: str, title: str) -> bool:
    """True if a top-level window is the PCB editor (by wx name, else title)."""
    if name in _PCB_FRAME_NAMES:
        return True
    low = (title or "").lower()
    return any(hint in low for hint in _PCB_TITLE_HINTS)


def find_pcb_frame():
    """The PCB editor top-level frame, or None outside KiCad."""
    import wx  # local: only available inside KiCad
    win = wx.FindWindowByName(_PCB_FRAME_NAMES[0])
    if win is not None:
        return win
    for cand in wx.GetTopLevelWindows():
        if looks_like_pcb_editor(cand.GetName(), cand.GetTitle()):
            return cand
    return None


def get_aui_manager(frame):
    """The wxAuiManager laying out ``frame``, or None if not exposed."""
    import wx.aui  # local: only available inside KiCad
    try:
        return wx.aui.AuiManager.GetManager(frame)
    except Exception:
        return None


def attach(panel_factory: Callable, caption: str) -> Optional[object]:
    """Dock the chat panel into the PCB editor; returns the panel or None.

    ``panel_factory(frame)`` builds the wx.Panel with the editor frame as
    parent (AUI requirement). Re-attaching when the pane already exists just
    re-shows it and returns the EXISTING panel (the caller refreshes its run
    plan). Use this first; fall back to the floating dialog on ``None``.
    """
    import wx.aui  # local: only available inside KiCad
    frame = find_pcb_frame()
    if frame is None:
        return None
    mgr = get_aui_manager(frame)
    if mgr is None:
        return None
    pane = mgr.GetPane(PANE_NAME)
    if pane.IsOk():  # already docked once → just bring it back
        pane.Show()
        mgr.Update()
        return pane.window
    panel = panel_factory(frame)
    try:
        info = (
            wx.aui.AuiPaneInfo()
            .Name(PANE_NAME).Caption(caption)
            .Right().Layer(2)
            .CloseButton(True).MaximizeButton(False)
            .MinSize(*PANE_MIN_SIZE).BestSize(*PANE_BEST_SIZE)
            .FloatingSize(*PANE_FLOAT_SIZE)
            .Dockable(True)
        )
        mgr.AddPane(panel, info)
        mgr.Update()
        return panel
    except Exception:
        panel.Destroy()  # don't leak a parentless orphan into the frame
        return None
