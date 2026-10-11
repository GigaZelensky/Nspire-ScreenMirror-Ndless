"""Native desktop settings and stream lifecycle; no USB work on the UI thread."""

import argparse
import json
import os
import queue
import threading
import sys
from clock_options import CLOCKS, clock_label, parse_clock


def gui(defaults, stream, validate, remember=False, settings_path=None):
    from settings import Settings

    preferences = Settings(settings_path) if remember else None
    if preferences:
        defaults = preferences.merge(defaults, sys.argv[1:])
    from dpi import initialize

    initialize()
    import tkinter as tk
    from tkinter import ttk
    from virtual_display import PRESETS, validate_size

    window = tk.Tk()
    window.title("ND Screen Mirror")
    window.resizable(False, False)
    pane = ttk.Frame(window, padding=16)
    pane.grid(sticky="nsew")
    notebook = ttk.Notebook(pane)
    notebook.grid(row=0, column=0, sticky="nsew")
    notebook.enable_traversal()
    picture = ttk.Frame(notebook, padding=16)
    controls = ttk.Frame(notebook, padding=16)
    advanced = ttk.Frame(notebook, padding=16)
    for page, title in ((picture, "Picture"), (controls, "Controls"), (advanced, "Advanced")):
        page.columnconfigure(1, weight=1)
        notebook.add(page, text=title)

    def field(parent, label, row, widget):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 24), pady=6)
        widget.grid(row=row, column=1, sticky="w", pady=6)
        return widget

    def settings_widgets(parent):
        for child in parent.winfo_children():
            yield child
            yield from settings_widgets(child)

    monitor = tk.IntVar(value=defaults.monitor)
    virtual_choice = tk.BooleanVar(
        value=os.name == "nt" and getattr(defaults, "virtual_display", False)
    )
    display_row = ttk.Frame(picture)
    display_row.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
    monitor_box = ttk.Spinbox(display_row, from_=0, to=16, textvariable=monitor, width=5)
    ttk.Label(display_row, text="Monitor").grid(row=0, column=0, padx=(0, 12))
    monitor_box.grid(row=0, column=1)
    own_display_box = ttk.Checkbutton(
        display_row,
        text="Own Windows display",
        variable=virtual_choice,
        state="normal" if os.name == "nt" else "disabled",
        command=lambda: virtual_mode(),
    )
    own_display_box.grid(row=0, column=2, padx=(24, 0))

    desktop_frame = ttk.Frame(picture)
    desktop_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
    initial_size = tuple(getattr(defaults, "desktop_size", (480, 360)))
    desktop_preset = tk.StringVar(
        value=next((label for label, size in PRESETS.items() if size == initial_size), "Custom")
    )
    desktop_box = field(
        desktop_frame,
        "Desktop size",
        0,
        ttk.Combobox(
            desktop_frame,
            values=[*PRESETS, "Custom"],
            textvariable=desktop_preset,
            state="readonly",
            width=24,
        ),
    )
    desktop_width = tk.IntVar(value=initial_size[0])
    desktop_height = tk.IntVar(value=initial_size[1])
    custom_frame = ttk.Frame(desktop_frame)
    custom_frame.grid(row=1, column=1, sticky="w", pady=(4, 0))
    width_box = ttk.Entry(custom_frame, textvariable=desktop_width, width=7)
    width_box.grid(row=0, column=0)
    ttk.Label(custom_frame, text="×").grid(row=0, column=1, padx=8)
    height_box = ttk.Entry(custom_frame, textvariable=desktop_height, width=7)
    height_box.grid(row=0, column=2)
    setup_button = ttk.Button(
        desktop_frame, text="Set up display driver", command=lambda: setup_virtual()
    )

    def desktop_size():
        return validate_size(
            PRESETS[desktop_preset.get()]
            if desktop_preset.get() in PRESETS
            else (desktop_width.get(), desktop_height.get())
        )

    def desktop_mode(unused=None):
        if desktop_preset.get() in PRESETS:
            desktop_width.set(PRESETS[desktop_preset.get()][0])
            desktop_height.set(PRESETS[desktop_preset.get()][1])
            custom_frame.grid_remove()
        else:
            custom_frame.grid()

    desktop_box.bind("<<ComboboxSelected>>", desktop_mode)
    desktop_mode()
    ttk.Separator(picture).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    modes = {"H.264": "h264", "MPEG-4": "mpeg4", "Lossless desktop": "lossless"}
    if getattr(defaults, "mode", "h264") == "raw":
        modes["Raw (diagnostic)"] = "raw"
    mode = tk.StringVar(
        value=next(
            label for label, value in modes.items() if value == getattr(defaults, "mode", "h264")
        )
    )
    mode_box = field(
        picture,
        "Picture mode",
        3,
        ttk.Combobox(picture, values=list(modes), textvariable=mode, state="readonly", width=24),
    )
    fps = tk.IntVar(value=defaults.fps)
    fps_row = ttk.Frame(picture)
    field(picture, "Frame rate", 4, fps_row)
    ttk.Spinbox(fps_row, from_=1, to=60, textvariable=fps, width=7).grid(row=0, column=0)
    ttk.Label(fps_row, text="FPS target").grid(row=0, column=1, padx=8)
    preset = tk.StringVar(value=getattr(defaults, "preset", "medium"))
    preset_box = field(
        picture,
        "Encoding preset",
        5,
        ttk.Combobox(
            picture,
            values=["ultrafast", "veryfast", "fast", "medium"],
            textvariable=preset,
            state="readonly",
            width=24,
        ),
    )
    auto = tk.BooleanVar(value=getattr(defaults, "auto_quality", True))
    auto_box = ttk.Checkbutton(
        picture,
        text="Adjust quality to maintain target FPS",
        variable=auto,
        command=lambda: quality_mode(),
    )
    auto_box.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 4))
    manual_quality = ttk.Frame(picture)
    manual_quality.grid(row=7, column=0, columnspan=2, sticky="w")
    quality_label = tk.StringVar(value="Quality (CRF)")
    ttk.Label(manual_quality, textvariable=quality_label).grid(
        row=0, column=0, sticky="w", padx=(0, 24)
    )
    crf = tk.IntVar(value=defaults.crf)
    quality_box = ttk.Spinbox(manual_quality, from_=8, to=35, textvariable=crf, width=7)
    quality_box.grid(row=0, column=1, sticky="w")
    ttk.Label(manual_quality, text="Lower = clearer").grid(row=0, column=2, padx=12)
    quality_values = {"h264": defaults.crf, "mpeg4": getattr(defaults, "mpeg4_q", 1)}
    previous_mode = "h264"

    def quality_mode(unused=None):
        nonlocal previous_mode
        if previous_mode in quality_values:
            quality_values[previous_mode] = crf.get()
        selected = modes[mode.get()]
        previous_mode = selected
        mpeg = selected == "mpeg4"
        quality_label.set("Quality (Q)" if mpeg else "Quality (CRF)")
        quality_box.configure(from_=1 if mpeg else 8, to=8 if mpeg else 35)
        crf.set(quality_values.get(selected, quality_values["h264"]))
        auto_box.configure(state="normal" if selected in ("h264", "mpeg4") else "disabled")
        preset_box.configure(state="readonly" if selected in ("h264", "mpeg4") else "disabled")
        if selected in ("h264", "mpeg4") and not auto.get():
            manual_quality.grid()
        else:
            manual_quality.grid_remove()

    mode_box.bind("<<ComboboxSelected>>", quality_mode)
    quality_mode()
    inputs = tk.BooleanVar(value=getattr(defaults, "input", True))
    ttk.Checkbutton(
        controls,
        text="Control this PC with the calculator",
        variable=inputs,
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
    pointer_gain = tk.DoubleVar(value=getattr(defaults, "pointer_gain", 1.0))
    field(
        controls,
        "Pointer sensitivity",
        1,
        ttk.Spinbox(controls, from_=0.1, to=4, increment=0.1, textvariable=pointer_gain, width=7),
    )
    confine = tk.BooleanVar(value=os.name == "nt" and getattr(defaults, "confine_pointer", False))
    confine_box = ttk.Checkbutton(
        controls, text="Keep pointer on mirrored display", variable=confine
    )
    confine_box.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 16))
    if os.name != "nt":
        confine_box.configure(state="disabled")
    ttk.Separator(controls).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 12))
    ttk.Label(controls, text="Hold Scratchpad and swipe vertically to scroll.").grid(
        row=4, column=0, columnspan=2, sticky="w", pady=(0, 12)
    )
    reverse_scroll = tk.BooleanVar(value=getattr(defaults, "reverse_scroll", False))
    ttk.Checkbutton(controls, text="Reverse scrolling", variable=reverse_scroll).grid(
        row=5, column=0, columnspan=2, sticky="w", pady=(0, 8)
    )
    scroll_gain = tk.DoubleVar(value=getattr(defaults, "scroll_gain", 1.0))
    field(
        controls,
        "Scroll sensitivity",
        6,
        ttk.Spinbox(controls, from_=0.1, to=4, increment=0.1, textvariable=scroll_gain, width=7),
    )
    ttk.Label(controls, text="1 = normal speed · 0.5 = half · 2 = double").grid(
        row=7, column=0, columnspan=2, sticky="w", pady=(8, 0)
    )
    ttk.Label(controls, text="Doc = Alt · Flag = calculator stats").grid(
        row=8, column=0, columnspan=2, sticky="w", pady=(16, 0)
    )
    layout_labels = {"auto": "Auto", "us": "US (QWERTY)", "fr": "French (AZERTY)"}
    layout_values = {label: value for value, label in layout_labels.items()}
    keyboard_layout = tk.StringVar(
        value=layout_labels[getattr(defaults, "keyboard_layout", "auto")]
    )
    if sys.platform == "linux":
        field(
            controls,
            "Host keyboard layout",
            9,
            ttk.Combobox(
                controls,
                values=list(layout_values),
                textvariable=keyboard_layout,
                state="readonly",
                width=24,
            ),
        )

    selected_clock = getattr(defaults, "clock", 0)
    clock = tk.StringVar(value=clock_label(selected_clock))
    clock_box = field(
        advanced,
        "Calculator clock",
        0,
        ttk.Combobox(
            advanced,
            values=[clock_label(value) for value in CLOCKS],
            textvariable=clock,
            state="normal",
            width=24,
        ),
    )
    ttk.Label(advanced, text="CX II: 396–492 · CX: 132–240 MHz · steps of 12").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(4, 8)
    )
    capture_choice = tk.StringVar(value=(getattr(defaults, "capture", None) or "auto").upper())
    capture_box = field(
        advanced,
        "Capture method",
        2,
        ttk.Combobox(
            advanced,
            values=["AUTO", "DXGI", "GDI", "MSS"]
            if os.name == "nt"
            else ["AUTO", "WAYLAND", "MSS"],
            textvariable=capture_choice,
            state="readonly",
            width=24,
        ),
    )
    diagnostic_log = tk.BooleanVar(value=getattr(defaults, "diagnostic_log", False))
    ttk.Checkbutton(advanced, text="Save diagnostic logs", variable=diagnostic_log).grid(
        row=3, column=0, columnspan=2, sticky="w", pady=(8, 0)
    )
    ttk.Separator(advanced).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 16))
    test = tk.BooleanVar(value=defaults.test_pattern)
    ttk.Checkbutton(advanced, text="Send a colour and motion test pattern", variable=test).grid(
        row=5, column=0, columnspan=2, sticky="w", pady=(0, 12)
    )
    ttk.Label(advanced, text="Use the pattern to check the USB connection.").grid(
        row=6, column=0, columnspan=2, sticky="w"
    )
    ttk.Label(pane, text="Open ndmirror on the calculator; close other linking apps.").grid(
        row=1, column=0, sticky="w", pady=(16, 0)
    )
    status = tk.StringVar(value=(preferences.notice if preferences else None) or "Ready")
    ttk.Label(pane, textvariable=status, wraplength=430).grid(
        row=2, column=0, sticky="w", pady=(8, 0)
    )
    messages = queue.Queue()
    stop = threading.Event()
    thread = None
    exiting = False
    setup_thread = None
    desktop = None
    start_after_setup = False
    setup_results = queue.Queue()
    buttons = ttk.Frame(pane)
    buttons.grid(row=3, column=0, sticky="e", pady=(12, 0))
    notebook.select(getattr(defaults, "selected_tab", 0))
    save_job = None
    tick_job = None

    def destroy_window():
        nonlocal save_job, tick_job
        for job in (save_job, tick_job):
            if job is not None:
                window.after_cancel(job)
        save_job = tick_job = None
        window.destroy()

    def persist():
        nonlocal save_job
        if save_job is not None:
            window.after_cancel(save_job)
            save_job = None
        if preferences is None:
            return
        values = {}
        variables = {
            "monitor": monitor,
            "fps": fps,
            "preset": preset,
            "capture": capture_choice,
            "virtual_display": virtual_choice,
            "input": inputs,
            "auto_quality": auto,
            "confine_pointer": confine,
            "reverse_scroll": reverse_scroll,
            "pointer_gain": pointer_gain,
            "scroll_gain": scroll_gain,
            "keyboard_layout": keyboard_layout,
            "test_pattern": test,
            "diagnostic_log": diagnostic_log,
        }
        for key, variable in variables.items():
            try:
                value = variable.get()
                values[key] = (
                    layout_values[value]
                    if key == "keyboard_layout"
                    else value.lower()
                    if key == "capture"
                    else value
                )
            except (tk.TclError, ValueError, OverflowError):
                pass
        values.update(
            mode=modes[mode.get()],
            crf=quality_values["h264"],
            mpeg4_q=quality_values["mpeg4"],
            selected_tab=notebook.index(notebook.select()),
        )
        try:
            values["clock"] = parse_clock(clock.get())
        except ValueError:
            # Preserve the last valid preference while a number is being typed.
            pass
        try:
            if values["mode"] in ("h264", "mpeg4"):
                values["crf" if values["mode"] == "h264" else "mpeg4_q"] = crf.get()
        except (tk.TclError, ValueError, OverflowError):
            pass
        try:
            values["desktop_size"] = desktop_size()
        except (ValueError, OverflowError, tk.TclError):
            pass
        try:
            preferences.save(values)
        except OSError as error:
            status.set("Could not save settings: " + str(error))

    def preferences_changed(*unused):
        nonlocal save_job
        if preferences is not None:
            if save_job is not None:
                window.after_cancel(save_job)
            save_job = window.after(300, persist)

    for variable in (
        monitor,
        fps,
        preset,
        capture_choice,
        virtual_choice,
        inputs,
        auto,
        confine,
        reverse_scroll,
        pointer_gain,
        scroll_gain,
        keyboard_layout,
        test,
        diagnostic_log,
        mode,
        clock,
        crf,
        desktop_preset,
        desktop_width,
        desktop_height,
    ):
        variable.trace_add("write", preferences_changed)
    notebook.bind("<<NotebookTabChanged>>", preferences_changed)

    def lock_settings(locked):
        for widget in settings_widgets(notebook):
            if isinstance(widget, (ttk.Spinbox, ttk.Combobox, ttk.Checkbutton, ttk.Entry)):
                widget.configure(
                    state="disabled"
                    if locked
                    else ("readonly" if isinstance(widget, ttk.Combobox) else "normal")
                )
        if not locked:
            clock_box.configure(state="normal")
            quality_mode()
        if os.name != "nt":
            confine_box.configure(state="disabled")
            own_display_box.configure(state="disabled")
        if virtual_choice.get():
            monitor_box.configure(state="disabled")
            capture_box.configure(state="disabled")
        setup_button.configure(state="disabled" if locked else "normal")
        desktop_box.configure(state="disabled" if locked else "readonly")
        width_box.configure(state="disabled" if locked else "normal")
        height_box.configure(state="disabled" if locked else "normal")

    def release_desktop():
        nonlocal desktop
        if desktop:
            try:
                desktop.close()
            except OSError as error:
                status.set("Could not remove the virtual desktop: " + str(error))
                return False
            desktop = None
        return True

    def virtual_mode():
        from virtual_display import installed

        setup_button.grid_remove()
        if virtual_choice.get():
            desktop_frame.grid()
            capture_choice.set("GDI")
            capture_box.configure(state="disabled")
            monitor_box.configure(state="disabled")
            setup_button.configure(text="Display setup" if installed() else "Set up display driver")
            setup_button.grid(row=2, column=1, sticky="w", pady=(8, 0))
        else:
            release_desktop()
            desktop_frame.grid_remove()
            capture_box.configure(state="readonly")
            monitor_box.configure(state="normal")

    def setup_virtual():
        nonlocal setup_thread
        if setup_thread and setup_thread.is_alive():
            return
        try:
            size = desktop_size()
        except (ValueError, OverflowError, tk.TclError) as error:
            status.set(str(error))
            return
        if not release_desktop():
            return

        def install():
            try:
                from virtual_display import install_driver

                install_driver(messages.put, size)
                setup_results.put(None)
            except Exception as error:
                setup_results.put(str(error))

        lock_settings(True)
        start_button.configure(state="disabled")
        status.set("Preparing virtual display setup...")
        setup_thread = threading.Thread(target=install, daemon=True)
        setup_thread.start()

    def start():
        nonlocal thread, desktop, start_after_setup
        if thread and thread.is_alive() or setup_thread and setup_thread.is_alive():
            return
        try:
            options = argparse.Namespace(**vars(defaults))
            from diagnostics import log_path

            options.diagnostic_log = diagnostic_log.get()
            options.connection_log = (
                getattr(defaults, "connection_log", None) or log_path()
                if options.diagnostic_log
                or any(
                    getattr(options, name, None)
                    for name in ("metrics", "frame_metrics", "capture_failure_stream", "profile")
                )
                else None
            )
            options.fps = fps.get()
            options.crf = crf.get()
            options.monitor = monitor.get()
            options.test_pattern = test.get()
            options.mode = modes[mode.get()]
            options.clock = parse_clock(clock.get())
            options.capture = capture_choice.get().lower()
            options.input = inputs.get()
            options.confine_pointer = confine.get()
            options.reverse_scroll = reverse_scroll.get()
            options.scroll_gain = scroll_gain.get()
            options.keyboard_layout = layout_values[keyboard_layout.get()]
            options.pointer_gain = pointer_gain.get()
            options.preset = preset.get()
            options.auto_quality = auto.get()
            options.virtual_display = virtual_choice.get()
            options.desktop_size = desktop_size() if options.virtual_display else (320, 240)
            if options.mode == "mpeg4":
                options.mpeg4_q = crf.get()
                options.crf = quality_values["h264"]
            validate(options)
            persist()
            if options.virtual_display:
                from virtual_display import VirtualDisplay, resolution_available

                if not resolution_available(options.desktop_size):
                    start_after_setup = True
                    setup_virtual()
                    return
                if desktop:
                    desktop.size = options.desktop_size
                    desktop.open()
                else:
                    desktop = VirtualDisplay(size=options.desktop_size).open()
                options.display_lease = desktop
            elif not release_desktop():
                return
        except (ValueError, OverflowError, OSError, tk.TclError) as e:
            status.set(str(e))
            return

        def record_stats(data):
            path = getattr(options, "metrics", None)
            if path:
                with path.open("a") as f:
                    f.write(json.dumps(data) + "\n")

        stop.clear()
        thread = threading.Thread(
            target=stream,
            args=(options, stop, messages.put),
            kwargs={"stats_report": record_stats if getattr(options, "metrics", None) else None},
            daemon=True,
        )
        lock_settings(True)
        status.set("Connecting to the calculator...")
        start_button.configure(state="disabled")
        stop_button.configure(state="normal")
        thread.start()

    def stop_stream():
        stop.set()
        status.set("Stopping...")

    start_button = ttk.Button(buttons, text="Start mirroring", command=start)
    start_button.grid(row=0, column=0)
    stop_button = ttk.Button(buttons, text="Stop", command=stop_stream, state="disabled")
    stop_button.grid(row=0, column=1, padx=8)

    def close():
        nonlocal exiting
        exiting = True
        persist()
        stop_stream()
        if setup_thread and setup_thread.is_alive():
            status.set("Waiting for display setup to finish...")
            return
        if not thread or not thread.is_alive():
            release_desktop()
            destroy_window()

    def tick():
        nonlocal start_after_setup, thread, tick_job
        try:
            result = setup_results.get_nowait()
            lock_settings(False)
            virtual_mode()
            start_button.configure(state="normal")
            if exiting:
                release_desktop()
                destroy_window()
                return
            if result:
                status.set(result)
            else:
                status.set("Calculator display is ready.")
                if start_after_setup or getattr(defaults, "start", False):
                    start_after_setup = False
                    start()
        except queue.Empty:
            pass
        try:
            while True:
                message = messages.get_nowait()
                if "FPS displayed" in message:
                    parts = message.split("/")
                    status.set(
                        parts[0].strip().replace("FPS displayed", "FPS")
                        + " · "
                        + parts[1].strip()
                        + (
                            " · " + parts[2].strip()
                            if len(parts) > 2 and parts[2].strip().startswith("auto ")
                            else ""
                        )
                    )
                else:
                    status.set(message)
        except queue.Empty:
            pass
        if thread and not thread.is_alive() and not (setup_thread and setup_thread.is_alive()):
            if exiting:
                release_desktop()
                destroy_window()
                return
            start_button.configure(state="normal")
            stop_button.configure(state="disabled")
            lock_settings(False)
            thread = None
        tick_job = window.after(50, tick)

    virtual_mode()
    window.protocol("WM_DELETE_WINDOW", close)
    tick_job = window.after(50, tick)
    if getattr(defaults, "setup_virtual_display", False):
        window.after(150, setup_virtual)
    elif getattr(defaults, "start", False):
        window.after(150, start)
    window.mainloop()
