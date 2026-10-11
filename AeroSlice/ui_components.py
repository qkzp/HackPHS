"""Small Tk layout helpers; no optimization or slicing logic lives here."""
import tkinter as tk
from tkinter import ttk


class ScrollPage(ttk.Frame):
    """One page scrollbar, displayed only when the content actually overflows."""
    def __init__(self, parent, background='#f1f5f9'):
        super().__init__(parent, style='Page.TFrame')
        self.fixed_viewport = False
        self.expand_content = False
        self.canvas = tk.Canvas(self, background=background, highlightthickness=0)
        self.bar = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.bar.grid(row=0, column=1, sticky='ns')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.body = ttk.Frame(self.canvas, style='Page.TFrame')
        self.window = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.body.bind('<Configure>', self.refresh)
        self.canvas.bind('<Configure>', self.resize)
        self.bind_all('<MouseWheel>', self.wheel, add='+')

    def resize(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)
        self.refresh()

    def refresh(self, event=None):
        if self.expand_content:
            self.canvas.configure(height=self.body.winfo_reqheight())
        height = self.canvas.winfo_height() if self.fixed_viewport else max(self.body.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.itemconfigure(self.window, height=height)
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), height))
        if height > self.canvas.winfo_height()+2:
            self.bar.grid()
        else:
            self.bar.grid_remove()
            self.canvas.yview_moveto(0)

    def wheel(self, event):
        if self.fixed_viewport or self.expand_content:return
        widget = event.widget
        # Leave text editing, tree scrolling and the 3D viewport to their owners.
        if isinstance(widget, (tk.Text, ttk.Treeview)) or getattr(widget, '_viewport', False):
            return
        while widget is not None and widget is not self.body:
            widget = getattr(widget, 'master', None)
        if widget is self.body and self.body.winfo_reqheight()>self.canvas.winfo_height():
            delta = int(-event.delta/120) if abs(event.delta)>=120 else int(-event.delta)
            self.canvas.yview_scroll(delta, 'units')


def responsive_form(frame, breakpoint=720):
    """Stack wide engineering forms without dropping any fields or actions."""
    specs = [(child, dict(child.grid_info())) for child in frame.grid_slaves()]
    max_column = max((int(info['column'])+int(info['columnspan']) for _, info in specs), default=2)
    state = [None]
    def arrange(event):
        narrow = event.width < breakpoint
        if narrow == state[0]:
            return
        state[0] = narrow
        for col in range(max_column):
            frame.columnconfigure(col, weight=0)
        frame.columnconfigure(0 if narrow else 1, weight=1)
        if not narrow and max_column>3:
            frame.columnconfigure(3, weight=1)
        for child, info in specs:
            if narrow:
                child.grid_configure(row=int(info['row'])*(max_column+1)+int(info['column']),
                                     column=0,columnspan=1,sticky='ew')
            else:
                child.grid_configure(**{k:v for k,v in info.items() if k!='in'})
    frame.bind('<Configure>', arrange, add='+')


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.window, self.timer = widget, text, None, None
        widget.bind('<Enter>', self.schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
    def schedule(self, event=None):
        self.timer=self.widget.after(500,self.show)
    def show(self):
        self.timer=None
        self.window=tk.Toplevel(self.widget)
        self.window.overrideredirect(True)
        self.window.geometry(f'+{self.widget.winfo_rootx()+8}+{self.widget.winfo_rooty()+self.widget.winfo_height()+4}')
        ttk.Label(self.window,text=self.text() if callable(self.text) else self.text,
                  padding=8,wraplength=480).pack()
    def hide(self,event=None):
        if self.timer:self.widget.after_cancel(self.timer);self.timer=None
        if self.window:self.window.destroy();self.window=None
