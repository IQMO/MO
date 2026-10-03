"""On-demand host for the parked workroom and existing profile Tk apps.

Never constructed by normal Desktop startup. The native resident owns the
thread and deadlines; this host only services an explicitly opened Tk app.
"""
from __future__ import annotations


class OptionalTkHost:
    def __init__(self, gui):
        import tkinter as tk

        self.gui = gui
        self.root = tk.Tk()
        self.root.withdraw()
        self._timer = gui.schedule(0, self._pump)

    def _pump(self):
        self._timer = None
        self.root.update()
        visible = any(child.winfo_viewable() for child in self.root.winfo_children())
        self._timer = self.gui.schedule(16 if visible else 500, self._pump)

    def close(self):
        self.gui.cancel(self._timer)
        self._timer = None
        root, self.root = self.root, None
        if root is not None:
            root.destroy()
