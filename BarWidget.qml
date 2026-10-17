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
  readonly property string settingsPath: Quickshell.env("HOME") + "/.config/omarchy/theme-manager/settings.json"

  // What settings.json switches on (all off by default).
  property bool workspaceWallpapers: false
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

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function close() {
    menuOpen = false
  }

  function parseSettings(text) {
    var data = {}
    try { data = JSON.parse(text) || {} } catch (e) { data = {} }
    workspaceWallpapers = data.workspace_wallpapers === true
    timed = (typeof data.slideshow_minutes === "number" && data.slideshow_minutes > 0)
      || (!!data.schedule && data.schedule.enabled === true)
    languageSetting = typeof data.language === "string" ? data.language : "auto"
  }

  // One helper run at a time; while one runs, only the latest request waits.
  function run(args) {
    if (helper.running) { pending = args; return }
    helper.command = ["/usr/bin/python3", root.autoPath].concat(args)
    helper.running = true
  }

  Process {
    id: helper
    onExited: {
      if (root.pending !== null) {
        var next = root.pending
        root.pending = null
        root.run(next)
      }
    }
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

  FileView {
    id: settingsFile
    path: root.settingsPath
    watchChanges: true
    onFileChanged: reload()
    onLoaded: root.parseSettings(text())
    onLoadFailed: root.parseSettings("{}")
  }

  // settings.json is replaced atomically, which a file watch can miss.
  Timer {
    interval: 15000
    running: true
    repeat: true
    onTriggered: settingsFile.reload()
  }

  Connections {
    target: Hyprland
    enabled: root.workspaceWallpapers && root.primary
    function onFocusedWorkspaceChanged() {
      var ws = Hyprland.focusedWorkspace
      if (ws && ws.id > 0) root.run(["workspace", String(ws.id)])
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
