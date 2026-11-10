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
    onTriggered: Quickshell.execDetached(["/usr/bin/python3", "-I", root.syncPath, "startup"])
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
    helper.command = ["/usr/bin/python3", root.autoPath].concat(args)
    helperDeadline.restart()
    helper.running = true
    if (!helper.running) Qt.callLater(root.helperSettled)   // it did not start
  }

  property bool helperBusy: false

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

  // Applying a theme takes seconds; the helper itself gives up a lock wait
  // after 60 s. A run still going after this is stopped.
  Timer {
    id: helperDeadline
    interval: 90000
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
    command: ["/usr/bin/python3", root.autoPath, "menu"]
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
    command: ["/usr/bin/python3", root.autoPath, "bar-state"]
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
    tilingWorker.command = ["/usr/bin/python3", "-I", root.tilingPath, "--window", address]
    tilingDeadline.restart()
    tilingWorker.running = true
    if (!tilingWorker.running) Qt.callLater(root.tilingSettled)   // it did not start
  }

  property bool tilingBusy: false

  function tilingSettled() {
    if (!tilingBusy || tilingWorker.running) return   // settled already, or still going
    tilingBusy = false
    tilingDeadline.stop()
    tilingSettle.restart()                   // the next window, if any
  }

  // A window's helper waits at most 60 s for the shared lock; one still going
  // after this is stopped and the queue goes on.
  Timer {
    id: tilingDeadline
    interval: 75000
    repeat: false
    onTriggered: {
      if (tilingWorker.running) tilingWorker.running = false   // its exit settles it
      else root.tilingSettled()
    }
  }

  // At most this many windows wait; a window already waiting is not added again.
  readonly property int tilingQueueMax: 32

  Connections {
    target: Hyprland
    enabled: root.primary && root.tilingActive
    function onRawEvent(event) {
      if (event.name !== "openwindow") return
      var address = event.data.split(",")[0]
      if (!/^(0x)?[0-9a-fA-F]+$/.test(address) || address.length > 32) return
      if (root.tilingQueue.indexOf(address) >= 0 || root.tilingQueue.length >= root.tilingQueueMax) return
      root.tilingQueue = root.tilingQueue.concat([address])
      tilingSettle.restart()
    }
  }
  Timer { id: tilingSettle; interval: 150; onTriggered: root.nextTilingWindow() }
  Process {
    id: tilingWorker
    onRunningChanged: if (!running) Qt.callLater(root.tilingSettled)
  }

  // At shell start, while the Workspace Presets plugin may still be on the
  // bar: take its settings once (no workspaces.json yet), so removing it
  // from the bar later loses nothing.
  Timer {
    interval: 1500
    running: root.primary
    repeat: false
    onTriggered: Quickshell.execDetached(["/usr/bin/python3", "-I", root.tilingPath, "--init-store"])
  }

  // Once per shell start: Omarchy's workspace modes (Super + L) in step with the presets.
  Timer {
    interval: 2500
    running: root.primary && root.hasWorkspacePresets
    repeat: false
    onTriggered: Quickshell.execDetached(["/usr/bin/python3", "-I", root.tilingPath, "--sync-modes", "start"])
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
      autostartWorker.command = ["/usr/bin/python3", "-I", root.tilingPath, "--autostart"]
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
