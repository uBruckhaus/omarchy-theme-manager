import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "I18n.js" as I18n

BarWidget {
  id: root

  moduleName: "ubruckhaus.theme-manager"

  readonly property string togglePath: Qt.resolvedUrl("theme-toggle").toString().replace(/^file:\/\//, "")
  readonly property string autoPath: Qt.resolvedUrl("theme_auto.py").toString().replace(/^file:\/\//, "")
  readonly property string syncPath: Qt.resolvedUrl("switcher_sync.py").toString().replace(/^file:\/\//, "")
  // Workspace presets (taken over from the Workspace Presets plugin): window
  // limits with overflow and App Preset autostart. The helper itself stays
  // idle while that plugin is still on the bar.
  readonly property string tilingPath: Qt.resolvedUrl("workspace_tiling.py").toString().replace(/^file:\/\//, "")
  readonly property string boundedPath: Qt.resolvedUrl("bounded_run.py").toString().replace(/^file:\/\//, "")
  property bool tilingActive: false
  property bool hasAutostart: false
  property bool hasWorkspacePresets: false
  property var tilingQueue: []
  property bool autostartFinished: false
  property int autostartAttempts: 0
  property int autostartPartialTries: 0

  // What settings.json switches on (all off by default).
  property bool workspaceWallpapers: false
  property bool workspaceThemes: false
  onWorkspaceThemesChanged: syncFocusedWorkspace(true)
  onWorkspaceWallpapersChanged: syncFocusedWorkspace()
  property bool timed: false
  // Interface language: the one picked in Theme Manager's settings, else the system's.
  property string languageSetting: "auto"
  readonly property string systemLang: I18n.systemLanguage(Quickshell.env("LC_ALL"), Quickshell.env("LC_MESSAGES"), Quickshell.env("LANG"))
  readonly property string lang: I18n.resolve(languageSetting, systemLang)
  function t(key, args) { return I18n.tr(lang, key, args) }

  // Every finite helper (menu, bar state, store setup, mode sync, autostart, the theme/wallpaper
  // helper, tiling) runs under bounded_run.py: in a process group of its own, ended as a whole
  // (SIGTERM, SIGKILL 5 s later to what is left) when the helper exits, when its deadline passes,
  // or when the shell stops the supervisor (Process running = false sends it SIGTERM, and it
  // passes that on to the whole group). So a stalled helper and anything it left running end,
  // its Process settles and the next try can run. Apps, notifications and `omarchy theme set`
  // are started in sessions of their own and are not part of that group. The popup, the event
  // listener and the user's apps are not run this way.
  function bounded(seconds, argv) {
    return ["/usr/bin/python3", "-I", root.boundedPath, String(seconds), "--"].concat(argv)
  }
  // The bar exists once per monitor: only the first monitor's widget
  // follows workspaces and runs the timer (the helper is idempotent anyway).
  readonly property bool primary: !root.QsWindow.window || !root.QsWindow.window.screen
    || Quickshell.screens.length === 0 || root.QsWindow.window.screen === Quickshell.screens[0]

  property bool menuOpen: false
  property var menuThemes: []
  property string menuSource: "favorites"
  property var pending: null
  property int focusedWorkspaceId: 0

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // Let the shell's built-in picker finish its first load, then refresh its
  // rows from Theme Manager's filtered cache. The helper checks the opt-in
  // setting, so a normal install leaves Omarchy's picker alone.
  Timer {
    interval: 2000
    running: root.primary
    repeat: false
    onTriggered: Quickshell.execDetached(root.bounded(120, ["/usr/bin/python3", "-I", root.syncPath, "startup"]))
  }

  function close() {
    menuOpen = false
  }

  // One helper run at a time; while one runs, only the latest request waits.
  // Each run is supervised: it ends (exit, failed start or the deadline) and
  // is settled exactly once, then the waiting request runs.
  function run(args) {
    if (helperBusy) { pending = args; return }
    helperBusy = true
    helper.command = root.bounded(helperLimit, ["/usr/bin/python3", root.autoPath].concat(args))
    helperDeadline.restart()
    helper.running = true
    if (!helper.running) Qt.callLater(root.helperSettled)   // it did not start
  }

  property bool helperBusy: false
  // The helper's own deadline (bounded_run); the outer one below adds the group's grace period.
  readonly property int helperLimit: 190

  function helperSettled() {
    if (!helperBusy || helper.running) return  // settled already, or still going
    helperBusy = false
    helperDeadline.stop()
    if (root.pending !== null) {
      var next = root.pending
      root.pending = null
      root.run(next)
    }
  }

  // Applying a theme takes seconds; the helper itself gives up a lock wait after 60 s and stops
  // a theme switch (with all it started) after 120 s, so it always ends on its own before this:
  // the lock is never released under a switch still going. Its supervisor ends the whole group at
  // 190 s; should it still run after this, the supervisor is stopped, which ends the group too.
  Timer {
    id: helperDeadline
    interval: 200000
    repeat: false
    onTriggered: {
      if (helper.running) helper.running = false   // its exit settles it
      else root.helperSettled()
    }
  }

  function syncFocusedWorkspace(force) {
    if (!primary || (!force && !(workspaceThemes || workspaceWallpapers))) return
    var ws = Hyprland.focusedWorkspace
    if (ws && ws.id > 0) run(["workspace", String(ws.id)])
  }

  Timer {
    id: workspaceDebounce
    interval: 500
    repeat: false
    onTriggered: {
      if (root.focusedWorkspaceId > 0 && (root.workspaceThemes || root.workspaceWallpapers))
        root.run(["workspace", String(root.focusedWorkspaceId)])
    }
  }

  Process {
    id: helper
    // Not running any more: exited, killed at the deadline, or never started.
    onRunningChanged: if (!running) Qt.callLater(root.helperSettled)
  }

  Process {
    id: menuLoader
    command: root.bounded(20, ["/usr/bin/python3", root.autoPath, "menu"])
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var data = JSON.parse(text)
          root.menuThemes = data.themes || []
          root.menuSource = data.source || "favorites"
        } catch (e) {
          root.menuThemes = []
        }
      }
    }
  }

  // settings.json and workspaces.json are read by the helper (bounded, regular
  // files only); the shell only parses its short answer.
  Process {
    id: barState
    command: root.bounded(20, ["/usr/bin/python3", root.autoPath, "bar-state"])
    stdout: StdioCollector {
      onStreamFinished: root.applyBarState(text)
    }
  }

  function refreshBarState() {
    if (!barState.running) barState.running = true
  }

  function applyBarState(text) {
    var data = {}
    try { data = JSON.parse(text.length <= 4096 ? text : "{}") || {} } catch (e) { data = {} }
    workspaceWallpapers = data.workspace_wallpapers === true
    workspaceThemes = data.workspace_themes === true
    timed = data.timed === true
    languageSetting = typeof data.language === "string" ? data.language : "auto"
    tilingActive = data.tiling_active === true
    hasAutostart = data.has_autostart === true
    hasWorkspacePresets = data.has_workspace_presets === true
  }

  // One window at a time, as the plugin did: the helper re-reads the
  // workspaces under its lock, so a burst of windows never overfills one.
  function nextTilingWindow() {
    if (tilingBusy || tilingQueue.length === 0) return
    if (!tilingActive) { tilingQueue = []; return }
    var queue = tilingQueue.slice()
    var address = queue.shift()
    tilingQueue = queue
    tilingBusy = true
    tilingCurrent = address
    tilingStarted = false
    tilingWorker.command = ["/usr/bin/python3", "-I", root.boundedPath, "--report-start", String(tilingLimit), "--", "/usr/bin/python3", "-I", root.tilingPath, "--window", address]
    tilingDeadline.restart()
    tilingWorker.running = true
    // `running` only says a QProcess exists: the window counts as handed over once the helper
    // has really started (bounded_run's start line). Not started at all → settled right here.
    if (!tilingWorker.running) Qt.callLater(root.tilingSettled)
  }

  property bool tilingBusy: false
  property string tilingCurrent: ""            // the window whose helper runs now
  property bool tilingStarted: false           // its helper really started (Process.started)
  readonly property int tilingLimit: 70        // the helper's own deadline (bounded_run)

  function tilingHelperStarted() {
    if (!tilingBusy || tilingStarted) return
    tilingStarted = true
    markTilingSeen(tilingCurrent)              // handed over (once; no retries)
  }

  function tilingSettled() {
    if (!tilingBusy || tilingWorker.running) return   // settled already, or still going
    tilingBusy = false
    tilingDeadline.stop()
    if (!tilingStarted) {                     // it never started (failed start or no process):
      tilingOverflow = true                   // looked for again by a rescan,
      tilingRetry.restart()                   // at most every 30 s
    }
    tilingCurrent = ""
    tilingStarted = false
    if (tilingQueue.length === 0 && tilingOverflow && !tilingRetry.running) rescanMissedWindows()
    tilingSettle.restart()                   // the next window, if any
  }

  // A window's helper waits at most 60 s for the shared lock; its supervisor ends its group at
  // 70 s. Should it still run after this, the supervisor is stopped (ending the group) and the
  // queue goes on.
  Timer {
    id: tilingDeadline
    interval: 80000
    repeat: false
    onTriggered: {
      if (tilingWorker.running) tilingWorker.running = false   // its exit settles it
      else root.tilingSettled()
    }
  }

  // At most this many windows wait; a window already waiting is not added again. Windows that
  // opened while the queue was full are not lost: once it has drained, the windows open now that
  // were never handed to the helper are queued (again at most this many; more → once more).
  // Each window is handed over once: one whose helper ran and failed is not retried (no retry
  // loops); one whose helper never started is looked for again by a rescan, at most every 30 s.
  readonly property int tilingQueueMax: 32
  readonly property int tilingSeenMax: 4096
  property bool tilingOverflow: false
  property var tilingSeen: ({})               // windows handed to the helper (or open at start)
  property var tilingSeenOrder: []

  function tilingKey(address) {
    var a = String(address || "").toLowerCase()
    return a.indexOf("0x") === 0 ? a.slice(2) : a
  }

  function markTilingSeen(address) {
    var key = tilingKey(address)
    if (!key || tilingSeen[key]) return
    tilingSeen[key] = true
    tilingSeenOrder.push(key)
    // Pruned to the windows still open (never an open one evicted): on every rescan and here.
    if (tilingSeenOrder.length > tilingSeenMax) pruneTilingSeen()
  }

  function openWindows() {
    var list = Hyprland.toplevels ? Hyprland.toplevels.values : []
    var found = []
    for (var i = 0; i < list.length; i++) {
      var key = tilingKey(list[i] ? list[i].address : "")
      if (/^[0-9a-f]{1,30}$/.test(key)) found.push(key)
    }
    return found
  }

  function pruneTilingSeen() {
    // The history keeps only windows still open (so it stays as small as the desktop itself).
    var windows = openWindows()
    var kept = {}, order = []
    for (var j = 0; j < windows.length; j++)
      if (tilingSeen[windows[j]]) { kept[windows[j]] = true; order.push(windows[j]) }
    tilingSeen = kept
    tilingSeenOrder = order
    return windows
  }

  function rescanMissedWindows() {
    tilingOverflow = false
    var queue = tilingQueue.slice()
    var windows = pruneTilingSeen()
    for (var i = 0; i < windows.length; i++) {
      if (tilingSeen[windows[i]] || queue.indexOf(windows[i]) >= 0) continue
      if (queue.length >= tilingQueueMax) { tilingOverflow = true; break }
      queue.push(windows[i])
    }
    tilingQueue = queue
    if (queue.length > 0) tilingSettle.restart()
  }

  // The windows already open when tiling starts are not new: never queued by a rescan.
  onTilingActiveChanged: if (tilingActive) {
    var windows = openWindows()
    for (var i = 0; i < windows.length; i++) markTilingSeen(windows[i])
  }

  Connections {
    target: Hyprland
    enabled: root.primary && root.tilingActive
    function onRawEvent(event) {
      if (event.name !== "openwindow") return
      var address = event.data.split(",")[0]
      if (!/^(0x)?[0-9a-fA-F]+$/.test(address) || address.length > 32) return
      if (root.tilingQueue.indexOf(address) >= 0) return
      if (root.tilingQueue.length >= root.tilingQueueMax) {
        root.tilingOverflow = true            // picked up again once the queue has drained
        return
      }
      root.tilingQueue = root.tilingQueue.concat([address])
      tilingSettle.restart()
    }
  }
  Timer { id: tilingSettle; interval: 150; onTriggered: root.nextTilingWindow() }
  // After a helper that could not start: the next rescan waits this long (no busy retry loop).
  Timer {
    id: tilingRetry
    interval: 30000
    repeat: false
    onTriggered: if (!root.tilingBusy && root.tilingQueue.length === 0 && root.tilingOverflow) root.rescanMissedWindows()
  }
  Process {
    id: tilingWorker
    // Process.started only means the supervisor runs; the helper itself has started once the
    // supervisor prints its line (bounded_run.py --report-start; no line = could not start).
    stdout: SplitParser {
      onRead: data => { if (data === "bounded_run: started") root.tilingHelperStarted() }
    }
    onRunningChanged: if (!running) Qt.callLater(root.tilingSettled)
  }

  // At shell start, while the Workspace Presets plugin may still be on the
  // bar: take its settings once (no workspaces.json yet), so removing it
  // from the bar later loses nothing.
  Timer {
    interval: 1500
    running: root.primary
    repeat: false
    onTriggered: Quickshell.execDetached(root.bounded(60, ["/usr/bin/python3", "-I", root.tilingPath, "--init-store"]))
  }

  // Once per shell start: Omarchy's workspace modes (Super + L) in step with the presets.
  Timer {
    interval: 2500
    running: root.primary && root.hasWorkspacePresets
    repeat: false
    onTriggered: Quickshell.execDetached(root.bounded(60, ["/usr/bin/python3", "-I", root.tilingPath, "--sync-modes", "start"]))
  }

  // App Presets with autostart open once per Hyprland session (the helper
  // keeps a per-session marker, shared with the plugin it replaces).
  Process {
    id: autostartWorker
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var res = JSON.parse(text)
          if (res.status === "ok" || res.status === "already_started" || res.status === "skipped")
            root.autostartFinished = true
          // Some apps did not start: the helper left its session marker unset,
          // so try twice more (each try starts only what is still missing).
          else if (res.status === "partial" && ++root.autostartPartialTries > 2)
            root.autostartFinished = true
        } catch (e) {}
      }
    }
  }
  Timer {
    interval: 2000
    running: root.primary && root.hasAutostart && !root.autostartFinished
    repeat: true
    onTriggered: {
      if (autostartWorker.running) return
      if (++root.autostartAttempts > 15) { root.autostartFinished = true; return }
      autostartWorker.command = root.bounded(120, ["/usr/bin/python3", "-I", root.tilingPath, "--autostart"])
      autostartWorker.running = true
    }
  }

  // The settings can change in the popup at any time: read them again every few seconds.
  Timer {
    interval: 5000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refreshBarState()
  }

  Connections {
    target: Hyprland
    enabled: (root.workspaceWallpapers || root.workspaceThemes) && root.primary
    function onFocusedWorkspaceChanged() {
      var ws = Hyprland.focusedWorkspace
      if (!ws || ws.id <= 0) return
      root.focusedWorkspaceId = ws.id
      workspaceDebounce.restart()
    }
  }

  Timer {
    interval: 60000
    running: root.timed && root.primary
    repeat: true
    triggeredOnStart: true
    onTriggered: root.run(["tick"])
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // U+F0E0C (Material Design palette outline): the icon Omarchy's own menu
    // uses for Style → Theme. Nerd Fonts carry it; FontAwesome-5 code points
    // such as U+F53F are missing from the bar font and render as tofu.
    text: "\u{F0E0C}"
    tooltipText: root.t("tip")

    onPressed: function(mouseButton) {
      if (!root.bar) return
      if (mouseButton === Qt.RightButton) {
        if (!root.menuOpen) menuLoader.running = true
        root.menuOpen = !root.menuOpen
        return
      }
      root.menuOpen = false
      // Detached so a click is never dropped while a previous toggle is still running.
      Quickshell.execDetached([root.togglePath])
    }

    onWheelMoved: function(delta) {
      if (delta !== 0) root.run(["bg", delta < 0 ? "next" : "prev"])
    }
  }

  PopupCard {
    id: menu
    anchorItem: root
    owner: root
    bar: root.bar
    open: root.menuOpen
    contentWidth: menu.fittedContentWidth(Style.space(260))
    contentHeight: menu.fittedContentHeight(menuColumn.implicitHeight)

    Column {
      id: menuColumn
      anchors.fill: parent
      spacing: Style.space(4)

      Text {
        textFormat: Text.PlainText
        text: root.menuSource === "favorites" ? root.t("favorites") : root.t("recent")
        color: Commons.Color.popups.text
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }

      Text {
        textFormat: Text.PlainText
        visible: root.menuThemes.length === 0
        width: parent.width
        wrapMode: Text.WordWrap
        text: root.t("noFavorites")
        color: Qt.darker(Commons.Color.popups.text, 1.4)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }

      Repeater {
        model: root.menuThemes
        delegate: Button {
          required property var modelData
          width: menuColumn.width
          leftAlign: true
          iconText: modelData.current ? "\uf00c" : "\u{F0E0C}"
          text: modelData.name
          foreground: modelData.current ? Commons.Color.accent : Commons.Color.popups.text
          fontSize: Style.font.bodySmall
          iconSize: Style.font.bodySmall
          onClicked: {
            root.menuOpen = false
            if (!modelData.current) root.run(["apply", modelData.slug])
          }
        }
      }

      Item { width: 1; height: Style.space(4) }

      Button {
        width: menuColumn.width
        leftAlign: true
        iconText: ""
        text: root.t("nextWallpaper")
        foreground: Commons.Color.popups.text
        fontSize: Style.font.bodySmall
        iconSize: Style.font.bodySmall
        onClicked: root.run(["bg", "next"])
      }

      Button {
        width: menuColumn.width
        leftAlign: true
        iconText: ""
        text: root.t("open")
        foreground: Commons.Color.popups.text
        fontSize: Style.font.bodySmall
        iconSize: Style.font.bodySmall
        onClicked: {
          root.menuOpen = false
          Quickshell.execDetached([root.togglePath])
        }
      }
    }
  }
}
