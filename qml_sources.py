"""
qml_sources.py — QML UI source code embedded as Python strings.

Embedding QML as strings (instead of .qml files) means PyInstaller --onefile
works without any datas entries or Qt resource compilation steps.
Each component is loaded via QQmlComponent.setData(src.encode(), QUrl()).

Design language: Fluent-inspired dark glass-morphism
  • Deep navy background layers with subtle transparency
  • Rounded cards with faint border glow
  • Accent: soft violet-blue  #7C6EF4
  • Green:  #4ADE80   Red: #F87171   Orange: #FB923C
  • Smooth spring animations on state transitions
  • Typography hierarchy: mono for live values, sans for labels
"""

# ── Shared theme constants (injected into every QML file) ─────────────────
THEME = """
pragma Singleton
import QtQuick 2.15

QtObject {
    // Background layers
    readonly property color bg0:     "#0a0a14"
    readonly property color bg1:     "#0f0f1e"
    readonly property color bg2:     "#161628"
    readonly property color surface: "#1c1c30"
    readonly property color surfaceHigh: "#22223a"
    readonly property color border:  "#2e2e50"
    readonly property color borderGlow: "#3d3d6b"

    // Accent palette
    readonly property color accent:  "#7c6ef4"
    readonly property color accentDim: "#4a3fa8"
    readonly property color green:   "#4ade80"
    readonly property color red:     "#f87171"
    readonly property color orange:  "#fb923c"
    readonly property color yellow:  "#fbbf24"
    readonly property color cyan:    "#22d3ee"

    // Text
    readonly property color textPrimary:   "#e2e8f0"
    readonly property color textSecondary: "#94a3b8"
    readonly property color textMuted:     "#4a5568"

    // Fonts
    readonly property int fontSizeXS:  10
    readonly property int fontSizeS:   12
    readonly property int fontSizeM:   13
    readonly property int fontSizeL:   16
    readonly property int fontSizeXL:  22
    readonly property int fontSizeXXL: 32

    readonly property int radiusS: 6
    readonly property int radiusM: 10
    readonly property int radiusL: 14

    function colorForClass(cls) {
        if (cls === "green")  return green
        if (cls === "red")    return red
        if (cls === "orange") return orange
        if (cls === "blue")   return accent
        if (cls === "cyan")   return cyan
        return textMuted
    }
}
"""

# ── Main window ────────────────────────────────────────────────────────────
MAIN_QML = """
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Controls.Material

ApplicationWindow {
    id: root
    visible: true
    width: 1320
    height: 880
    title: "EN 14055 Cistern Analytics — ifm PI1789"
    color: bgDeep

    // ── Theme properties (all UI colors derived from isDark) ──────────
    property bool isDark: true

    readonly property color bgDeep:    isDark ? "#0a0a14" : "#dde1f0"
    readonly property color bgPanel:   isDark ? "#0f0f1e" : "#e4e8f5"
    readonly property color bgMid:     isDark ? "#161628" : "#eaeef8"
    readonly property color bgCard:    isDark ? "#1c1c30" : "#f2f4fb"
    readonly property color bgInput:   isDark ? "#22223a" : "#ffffff"
    readonly property color borderCol: isDark ? "#2e2e50" : "#c2c8dc"
    readonly property color textPri:   isDark ? "#e2e8f0" : "#1e2140"
    readonly property color textSec:   isDark ? "#94a3b8" : "#4a5468"
    readonly property color textMute:  isDark ? "#4a5568" : "#8892b0"
    readonly property color accentCol: isDark ? "#7c6ef4" : "#5b50d6"
    readonly property color accentDim: isDark ? "#4a3fa8" : "#3d35b8"
    readonly property color btnDef:    isDark ? "#22234a" : "#c8ccec"
    readonly property color chartBg:   isDark ? "#111122" : "#eaeef8"
    readonly property color chartPlot: isDark ? "#0d0d1f" : "#dde1f0"
    readonly property color chartGrid: isDark ? "#1e1e38" : "#c2c8dc"
    readonly property color chartAxis: isDark ? "#2e2e50" : "#a0a8c8"
    readonly property color chartLbl:  isDark ? "#4a5568" : "#3a4060"

    Component.onCompleted: { isDark = (bridge.currentTheme !== "Light") }

    // ── Fonts
    FontLoader { id: fontMono;  source: "qrc:/fonts/JetBrainsMono.ttf";  onStatusChanged: if(status===FontLoader.Error) console.log("mono font not found, using fallback") }
    FontLoader { id: fontSans;  source: "qrc:/fonts/Inter.ttf";           onStatusChanged: if(status===FontLoader.Error) console.log("sans font not found") }

    // ── Connections to bridge
    Connections {
        target: bridge
        function onConnectionChanged(text, cls, connected) {
            connLabel.text = text
            connDot.color  = root.colorForClass(cls)
            btnConnect.text = connected ? "Disconnect" : "Connect Sensor"
            btnConnect.connected = connected
        }
        function onToastMessage(msg) { toast.show(msg) }
        function onRwlStateChanged(text, cls)     { rwlState.text = text;    rwlState.color = root.colorForClass(cls) }
        function onCwlAutoStateChanged(text, cls) { cwlAutoState.text = text; cwlAutoState.color = root.colorForClass(cls) }
        function onChartDataReady(pts)  { sensorChart.updateSeries(pts)  }
        function onLimitsChanged()      { limitsCard.refresh()            }
        function onFlushChanged()       { flushCard.refreshState()        }
        function onFlushRowsChanged(rows) { flushCard.refreshRows(rows)   }
        function onThemeChanged(theme)  { root.isDark = (theme !== "Light") }
    }

    // Helper exposed to QML components
    function colorForClass(cls) {
        if (cls === "green")  return "#4ade80"
        if (cls === "red")    return "#f87171"
        if (cls === "orange") return "#fb923c"
        if (cls === "blue")   return root.accentCol
        if (cls === "cyan")   return "#22d3ee"
        return root.textMute
    }

    // ── Menu bar
    menuBar: MenuBar {
        background: Rectangle { color: root.bgPanel; border.color: root.borderCol; border.width: 0 }
        delegate: MenuBarItem {
            contentItem: Text {
                text: parent.text; color: root.textSec; font.pixelSize: 13
                font.family: fontSans.name
                leftPadding: 12; rightPadding: 12
            }
            background: Rectangle {
                color: parent.highlighted ? root.bgInput : "transparent"
                radius: 4
            }
        }
        Menu {
            title: "File"
            MenuItem { text: "Load Profile…";        onTriggered: bridge.loadProfile()   }
            MenuItem { text: "Save Profile As…";     onTriggered: bridge.saveProfile()   }
            MenuSeparator {}
            MenuItem { text: "Set as Default";       onTriggered: bridge.saveAsDefault() }
            MenuItem { text: "Clear Default";        onTriggered: bridge.clearDefault()  }
            MenuSeparator {}
            MenuItem { text: "Export Screenshot";    onTriggered: bridge.exportScreenshot() }
            MenuSeparator {}
            MenuItem { text: "Exit";                 onTriggered: Qt.quit()              }
        }
        Menu {
            title: "Settings"
            MenuItem { text: "Hardware Connection…"; onTriggered: bridge.openConnectionDlg()  }
            MenuItem { text: "Calibration Profile…"; onTriggered: bridge.openCalibrationDlg() }
            MenuItem { text: "Program Settings…";    onTriggered: bridge.openProgramDlg()     }
            MenuItem { text: "Chart Line Colors…";   onTriggered: bridge.openColorsDlg()      }
        }
        Menu {
            title: "Test"
            MenuItem { text: "EN 14055 Compliance Check"; onTriggered: bridge.checkCompliance() }
        }
    }

    // ── Root layout
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 6
        spacing: 6

        // ── Top bar
        Rectangle {
            Layout.fillWidth: true
            height: 44
            color: root.bgMid
            radius: 10
            border.color: root.borderCol; border.width: 1

            RowLayout {
                anchors { fill: parent; leftMargin: 12; rightMargin: 12 }
                spacing: 12

                // Collapse left panel
                RoundButton {
                    id: btnCollapse
                    text: leftPanel.width > 10 ? "◀" : "▶"
                    flat: true; implicitWidth: 32; implicitHeight: 32
                    contentItem: Text {
                        text: parent.text; color: root.textSec; font.pixelSize: 14
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle { color: parent.hovered ? root.bgInput : "transparent"; radius: 6 }
                    onClicked: leftPanel.width = leftPanel.width > 10 ? 0 : 350
                }

                // Profile name
                Text {
                    text: "Profile: " + bridge.profileName
                    color: root.textSec; font.pixelSize: 13; font.family: fontSans.name
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                // Connection indicator
                Row {
                    spacing: 6
                    Rectangle {
                        id: connDot
                        width: 9; height: 9; radius: 5
                        color: root.textMute
                        anchors.verticalCenter: parent.verticalCenter
                        Behavior on color { ColorAnimation { duration: 400 } }
                    }
                    Text {
                        id: connLabel
                        text: "Disconnected"
                        color: root.textSec; font.pixelSize: 13
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                // Connect button
                Button {
                    id: btnConnect
                    text: "Connect Sensor"
                    property bool connected: false
                    contentItem: Text {
                        text: parent.text; color: root.textPri
                        font.pixelSize: 13; font.family: fontSans.name
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 7
                        color: btnConnect.connected
                            ? (btnConnect.hovered ? "#8c3a3a" : "#7a2e2e")
                            : (btnConnect.hovered ? "#3d3fa8" : "#312f8a")
                        Behavior on color { ColorAnimation { duration: 200 } }
                    }
                    onClicked: bridge.toggleConnect()
                    implicitWidth: 150; implicitHeight: 32
                }
            }
        }

        // ── Main area: left panel + chart
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            // ── Left panel (collapsible)
            ColumnLayout {
                id: leftPanel
                width: 350
                height: parent.height
                spacing: 6
                clip: true

                property bool collapsed: false
                Behavior on width { NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }

                // Live data card
                LiveDataCard { id: liveCard; Layout.fillWidth: true }

                // EN14055 limits card
                LimitsCard { id: limitsCard; Layout.fillWidth: true }

                // Flush test card
                FlushCard { id: flushCard; Layout.fillWidth: true; Layout.fillHeight: true }

                // Data log card
                LogCard { Layout.fillWidth: true }
            }

            // ── Right panel: chart
            ColumnLayout {
                anchors {
                    left: leftPanel.right; leftMargin: 6
                    right: parent.right
                    top: parent.top; bottom: parent.bottom
                }
                spacing: 4

                // Chart toolbar
                Rectangle {
                    Layout.fillWidth: true
                    height: 42
                    color: root.bgMid; radius: 10
                    border.color: root.borderCol; border.width: 1

                    RowLayout {
                        anchors { fill: parent; leftMargin: 10; rightMargin: 10 }
                        spacing: 8

                        Text { text: "Axis:"; color: root.textSec; font.pixelSize: 12 }
                        ComboBox {
                            id: comboAxis
                            model: ["Height (mm)", "Volume (L)", "Flow Rate (L/s)"]
                            implicitWidth: 140; implicitHeight: 30
                            onCurrentTextChanged: bridge.setPlotMode(currentText)
                            contentItem: Text {
                                text: parent.displayText; color: root.textPri
                                font.pixelSize: 12; leftPadding: 8
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle { color: root.bgInput; radius: 6; border.color: root.borderCol; border.width: 1 }
                        }

                        Text { text: "Window:"; color: root.textSec; font.pixelSize: 12 }
                        ComboBox {
                            id: comboWindow
                            model: ["10 s","30 s","60 s","5 min","All"]
                            currentIndex: 1
                            implicitWidth: 76; implicitHeight: 30
                            onCurrentTextChanged: sensorChart.setWindow(currentText)
                            contentItem: Text { text: parent.displayText; color: root.textPri; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { color: root.bgInput; radius: 6; border.color: root.borderCol; border.width: 1 }
                        }

                        Text { text: "Smooth:"; color: root.textSec; font.pixelSize: 12 }
                        ComboBox {
                            id: comboSmooth
                            model: ["None","SMA-5","SMA-20","EMA-Fast","EMA-Slow"]
                            implicitWidth: 106; implicitHeight: 30
                            onCurrentTextChanged: bridge.setSmoothAlg(currentText)
                            contentItem: Text { text: parent.displayText; color: root.textPri; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { color: root.bgInput; radius: 6; border.color: root.borderCol; border.width: 1 }
                        }

                        CheckBox {
                            id: chkScroll
                            text: "Auto-scroll"
                            checked: true
                            onCheckedChanged: sensorChart.autoScroll = checked
                            contentItem: Text { text: parent.text; color: root.textSec; font.pixelSize: 12; leftPadding: parent.indicator.width + 4; verticalAlignment: Text.AlignVCenter }
                        }

                        Button {
                            id: btnPause
                            property bool paused: false
                            text: paused ? "Resume" : "Pause"
                            implicitWidth: 80; implicitHeight: 30
                            contentItem: Text { text: parent.text; color: root.textPri; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle {
                                radius: 6
                                color: btnPause.paused
                                    ? (btnPause.hovered ? "#256638" : "#1d5230")
                                    : (btnPause.hovered ? "#2e2f5c" : root.btnDef)
                            }
                            onClicked: {
                                paused = !paused
                                sensorChart.paused = paused
                            }
                        }

                        Button {
                            text: "Screenshot"
                            implicitWidth: 100; implicitHeight: 30
                            contentItem: Text { text: parent.text; color: root.textPri; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { color: parent.hovered ? "#2e2f5c" : root.btnDef; radius: 6 }
                            onClicked: bridge.exportScreenshot()
                        }

                        Item { Layout.fillWidth: true }

                        Text {
                            id: deltaLabel
                            text: "---"
                            color: root.accentCol; font.pixelSize: 13; font.family: fontSans.name
                        }
                        Button {
                            text: "Clear"
                            implicitWidth: 52; implicitHeight: 30
                            contentItem: Text { text: parent.text; color: root.textPri; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { color: parent.hovered ? "#3a1f1f" : "#2c1818"; radius: 6 }
                            onClicked: deltaLabel.text = "---"
                        }
                    }
                }

                // Chart
                SensorChart {
                    id: sensorChart
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    onDeltaText: deltaLabel.text = text
                }
            }
        }
    }

    // ── Toast overlay
    Rectangle {
        id: toast
        anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter; bottomMargin: 24 }
        width: toastText.implicitWidth + 40; height: 40
        radius: 20
        color: root.bgInput; border.color: root.accentCol; border.width: 1
        opacity: 0
        z: 100

        Text {
            id: toastText; anchors.centerIn: parent
            color: root.textPri; font.pixelSize: 13
        }

        SequentialAnimation {
            id: toastAnim
            NumberAnimation { target: toast; property: "opacity"; to: 0.95; duration: 200 }
            PauseAnimation  { duration: 2800 }
            NumberAnimation { target: toast; property: "opacity"; to: 0;    duration: 300 }
        }

        function show(msg) {
            toastText.text = msg
            toastAnim.restart()
        }
    }

    // Status rows exposed for LimitsCard
    property alias rwlStateText: rwlState
    property alias cwlAutoStateText: cwlAutoState

    Text { id: rwlState;    visible: false; text: "RWL: IDLE"; color: "#4a5568" }
    Text { id: cwlAutoState; visible: false; text: "CWL: IDLE"; color: "#4a5568" }
}
"""

# ── Reusable card base (injected into all components) ─────────────────────
CARD_BASE = """
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    color: "#1c1c30"
    radius: 12
    border.color: "#2e2e50"; border.width: 1
    layer.enabled: true
    layer.effect: null
}
"""

# ── LiveDataCard ──────────────────────────────────────────────────────────
LIVE_DATA_CARD = """
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    color: root.bgCard; radius: 12
    border.color: root.borderCol; border.width: 1
    implicitHeight: cardContent.implicitHeight + 24

    ColumnLayout {
        id: cardContent
        anchors { fill: parent; margins: 14 }
        spacing: 10

        // Header
        RowLayout {
            Text { text: "LIVE DATA"; color: root.accentCol; font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 1.5 }
            Item { Layout.fillWidth: true }
            Rectangle { width: 8; height: 8; radius: 4; color: bridge.isConnected ? "#4ade80" : root.textMute
                Behavior on color { ColorAnimation { duration: 400 } }
            }
        }

        // Primary values row
        RowLayout {
            Layout.fillWidth: true; spacing: 0

            // Height — big accent
            ColumnLayout {
                spacing: 2
                Text {
                    text: bridge.height.toFixed(1) + " mm"
                    color: root.accentCol; font.pixelSize: 30; font.weight: Font.Bold
                    font.family: "monospace"
                    Behavior on text { }
                }
                Text { text: "HEIGHT"; color: root.textMute; font.pixelSize: 10; font.letterSpacing: 1.2 }
            }

            Item { Layout.fillWidth: true }

            // Volume
            ColumnLayout {
                spacing: 2
                Text {
                    Layout.alignment: Qt.AlignRight
                    text: bridge.volume.toFixed(2) + " L"
                    color: "#4ade80"; font.pixelSize: 20; font.weight: Font.Medium
                    font.family: "monospace"
                }
                Text { Layout.alignment: Qt.AlignRight; text: "VOLUME"; color: root.textMute; font.pixelSize: 10; font.letterSpacing: 1.2 }
            }
        }

        // Divider
        Rectangle { Layout.fillWidth: true; height: 1; color: root.borderCol }

        // Secondary row: pressure / flow / temperature
        GridLayout {
            Layout.fillWidth: true
            columns: 3; rowSpacing: 4; columnSpacing: 8

            Text { text: "Pressure"; color: root.textMute; font.pixelSize: 11 }
            Text { text: "Flow";     color: root.textMute; font.pixelSize: 11 }
            Text { text: "Temp";     color: root.textMute; font.pixelSize: 11 }

            Text { text: bridge.pressureStr;    color: root.textSec; font.pixelSize: 13; font.family: "monospace" }
            Text {
                text: bridge.flow.toFixed(3) + " L/s"
                color: "#fb923c"; font.pixelSize: 13; font.family: "monospace"
            }
            Text { text: bridge.temperatureStr; color: root.textSec; font.pixelSize: 13; font.family: "monospace" }
        }

        // Live headroom bar
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            Text { text: "Headroom"; color: root.textMute; font.pixelSize: 11 }
            Rectangle {
                Layout.fillWidth: true; height: 6; radius: 3; color: root.bgInput
                Rectangle {
                    id: headroomFill
                    width: parent.width * Math.min(1, Math.max(0,
                        bridge.overflow > 0 ? (bridge.overflow - bridge.height) / bridge.overflow : 0))
                    height: parent.height; radius: parent.radius
                    color: bridge.headroomClass === "green" ? "#4ade80"
                         : bridge.headroomClass === "orange" ? "#fb923c" : "#f87171"
                    Behavior on width { NumberAnimation { duration: 300 } }
                    Behavior on color { ColorAnimation { duration: 400 } }
                }
            }
            Text {
                text: bridge.headroom
                color: root.colorForClass(bridge.headroomClass)
                font.pixelSize: 12; font.family: "monospace"
                Behavior on color { ColorAnimation { duration: 400 } }
            }
        }
    }
}
"""

# ── LimitsCard ────────────────────────────────────────────────────────────
LIMITS_CARD = """
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    color: root.bgCard; radius: 12
    border.color: root.borderCol; border.width: 1
    implicitHeight: limitsContent.implicitHeight + 24

    function refresh() { limitsContent.forceLayout() }

    ColumnLayout {
        id: limitsContent
        anchors { fill: parent; margins: 14 }
        spacing: 8

        // Header
        Text { text: "EN 14055 LIMITS"; color: root.accentCol; font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 1.5 }

        // Action buttons — 2 columns
        GridLayout {
            Layout.fillWidth: true; columns: 2; rowSpacing: 6; columnSpacing: 6

            ActionButton { text: "Set NWL (" + bridge.avgWindowLabel + "s)"; onClicked: bridge.setNwl() }
            ActionButton { text: "Set Meniscus";                               onClicked: bridge.setMeniscus() }
            ActionButton { text: "Auto-detect MWL/CWL"; Layout.columnSpan: 2; Layout.fillWidth: true; onClicked: bridge.armCwlAuto() }
            ActionButton {
                id: btnManualMwlCwl
                text: "Manual MWL/CWL"; Layout.columnSpan: 2; Layout.fillWidth: true
                property bool pending: false
                color: pending ? "#7a2e2e" : "#22234a"
                onClicked: {
                    pending = !pending
                    text = pending ? "Cancel Manual MWL/CWL" : "Manual MWL/CWL"
                    // Signal QML chart to enter click-select mode
                    if (pending) sensorChart.enterManualSelect("MWL")
                    else         sensorChart.exitManualSelect()
                }
                Connections {
                    target: bridge
                    function onLimitsChanged() { if (btnManualMwlCwl.pending) { btnManualMwlCwl.pending = false; btnManualMwlCwl.text = "Manual MWL/CWL" } }
                }
            }
            ActionButton {
                id: btnManualRwl; visible: bridge.showManualRwlBtn
                text: "Start RWL 2s Timer"; Layout.columnSpan: 2; Layout.fillWidth: true
                onClicked: bridge.manualRwl()
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: root.borderCol }

        // Limits grid
        GridLayout {
            Layout.fillWidth: true; columns: 4; rowSpacing: 4; columnSpacing: 8

            // Labels row
            LimitLabel { text: "NWL" }
            LimitValue  { text: bridge.nwlStr }
            LimitLabel { text: "Meniscus" }
            LimitValue  { text: bridge.meniscusStr }

            LimitLabel { text: "MWL fault" }
            LimitValue  { text: bridge.mwlFaultStr }
            LimitLabel { text: "Overflow" }
            LimitValue  { text: bridge.overflowStr }

            LimitLabel { text: "CWL (2s)" }
            LimitValue  { text: bridge.cwlStr }
            LimitLabel { text: "Safety c" }
            LimitValue  { text: bridge.safetyMarginStr; color: root.colorForClass(bridge.safetyMarginClass) }

            LimitLabel { text: "Residual" }
            LimitValue  { text: bridge.residualStr }
            LimitLabel { text: "" }
            LimitValue  { text: "" }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: root.borderCol }

        // Status labels (driven by bridge signals via main window)
        Text {
            id: cwlStatus
            Layout.fillWidth: true
            text: bridge.cwlStatusStr
            color: root.colorForClass(bridge.cwlStatusClass)
            font.pixelSize: 12; wrapMode: Text.WordWrap
            Behavior on color { ColorAnimation { duration: 300 } }
        }
        Text {
            id: cwlAutoStateDisplay
            Layout.fillWidth: true
            text: root.cwlAutoStateText.text
            color: root.cwlAutoStateText.color
            font.pixelSize: 12; wrapMode: Text.WordWrap
        }
        Text {
            id: rwlStateDisplay
            Layout.fillWidth: true
            text: root.rwlStateText.text
            color: root.rwlStateText.color
            font.pixelSize: 12; wrapMode: Text.WordWrap
        }
    }
}
"""

# ── Reusable micro-components ─────────────────────────────────────────────
ACTION_BUTTON = """
import QtQuick 2.15
import QtQuick.Controls 2.15

Button {
    property color color: root.btnDef
    implicitHeight: 32; implicitWidth: 100
    contentItem: Text {
        text: parent.text; color: root.textPri; font.pixelSize: 12
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        color: parent.down ? Qt.darker(parent.color, 1.2)
             : parent.hovered ? Qt.lighter(parent.color, 1.15) : parent.color
        radius: 7
        Behavior on color { ColorAnimation { duration: 150 } }
    }
}
"""

LIMIT_LABEL = """
import QtQuick 2.15
Text { color: root.textMute; font.pixelSize: 11 }
"""

LIMIT_VALUE = """
import QtQuick 2.15
Text {
    property alias color: self.color
    id: self
    color: root.textSec; font.pixelSize: 12; font.family: "monospace"
    elide: Text.ElideRight
}
"""

# ── LogCard ───────────────────────────────────────────────────────────────
LOG_CARD = """
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    color: root.bgCard; radius: 12
    border.color: root.borderCol; border.width: 1
    implicitHeight: logContent.implicitHeight + 24

    ColumnLayout {
        id: logContent
        anchors { fill: parent; margins: 14 }
        spacing: 8

        Text { text: "DATA LOG"; color: root.accentCol; font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 1.5 }

        Button {
            Layout.fillWidth: true; implicitHeight: 34
            text: bridge.isLogging ? "Stop Data Log" : "Start Data Log (CSV)"
            contentItem: Text {
                text: parent.text; color: root.textPri; font.pixelSize: 13
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 8
                color: bridge.isLogging
                    ? (parent.hovered ? "#8c3a3a" : "#6d2b2b")
                    : (parent.hovered ? "#256638" : "#1d5230")
                Behavior on color { ColorAnimation { duration: 200 } }
            }
            onClicked: bridge.toggleLog()
        }
    }
}
"""

# ── FlushCard ─────────────────────────────────────────────────────────────
FLUSH_CARD = """
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    color: root.bgCard; radius: 12
    border.color: root.borderCol; border.width: 1
    implicitHeight: flushContent.implicitHeight + 24

    property var flushRows: []

    function refreshState() { flushContent.forceLayout() }
    function refreshRows(rows) { flushRows = rows; flushList.model = rows }

    ColumnLayout {
        id: flushContent
        anchors { fill: parent; margins: 14 }
        spacing: 8

        RowLayout {
            Text { text: "FLUSH TEST  (EN 14055)"; color: root.accentCol; font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 1.5 }
        }

        RowLayout {
            Layout.fillWidth: true; spacing: 8
            Text { text: "Type:"; color: root.textMute; font.pixelSize: 12 }
            ComboBox {
                id: flushTypeCombo
                model: ["Full Flush", "Part Flush"]
                Layout.fillWidth: true; implicitHeight: 30
                contentItem: Text { text: parent.displayText; color: root.textPri; font.pixelSize: 12; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: root.bgInput; radius: 6; border.color: root.borderCol; border.width: 1 }
            }
        }

        Button {
            Layout.fillWidth: true; implicitHeight: 34
            text: bridge.isFlushMeasuring ? "Stop Flush Measurement" : "Start Flush Measurement"
            contentItem: Text {
                text: parent.text; color: root.textPri; font.pixelSize: 13
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 8
                color: bridge.isFlushMeasuring
                    ? (parent.hovered ? "#8c3a3a" : "#6d2b2b")
                    : (parent.hovered ? "#256638" : "#1d5230")
                Behavior on color { ColorAnimation { duration: 200 } }
            }
            onClicked: {
                if (bridge.isFlushMeasuring) bridge.stopFlush(flushTypeCombo.currentText)
                else                         bridge.startFlush(flushTypeCombo.currentText)
            }
        }

        Text {
            text: "* EN col = rate ignoring first 1L and last 2L"
            color: root.textMute; font.pixelSize: 11; wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        // Flush results list
        Rectangle {
            Layout.fillWidth: true; height: 130
            color: root.bgMid; radius: 8; border.color: root.borderCol; border.width: 1
            clip: true

            ListView {
                id: flushList
                anchors { fill: parent; margins: 8 }
                spacing: 4
                model: []
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: Text {
                    text: modelData
                    color: root.textSec; font.pixelSize: 11; font.family: "monospace"
                    width: flushList.width; wrapMode: Text.NoWrap
                    elide: Text.ElideRight
                }

                Text {
                    anchors.centerIn: parent
                    text: "No measurements yet."
                    color: root.textMute; font.pixelSize: 12
                    visible: flushList.count === 0
                }
            }
        }

        // Bottom buttons
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            Button {
                text: "Clear All"; implicitHeight: 30; implicitWidth: 90
                contentItem: Text { text: parent.text; color: root.textPri; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: parent.hovered ? "#8c3a3a" : "#6d2b2b"; radius: 7 }
                onClicked: bridge.clearFlush()
            }
            Button {
                Layout.fillWidth: true; implicitHeight: 30
                text: "Compliance Check"
                contentItem: Text { text: parent.text; color: root.textPri; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: parent.hovered ? "#2e2f5c" : root.btnDef; radius: 7 }
                onClicked: bridge.checkCompliance()
            }
        }
    }
}
"""

# ── SensorChart ───────────────────────────────────────────────────────────
SENSOR_CHART = """
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtCharts

Item {
    id: chartRoot
    property bool paused: false
    property bool autoScroll: true
    property string windowKey: "30 s"
    property string manualSelectMode: ""  // "MWL" or "CWL" or ""

    signal deltaText(string text)

    // Window sizes in seconds (null = All)
    readonly property var windowSecs: ({ "10 s":10, "30 s":30, "60 s":60, "5 min":300, "All":0 })

    function setWindow(key) { windowKey = key }
    function enterManualSelect(mode) { manualSelectMode = mode; cursorShape = Qt.CrossCursor }
    function exitManualSelect() { manualSelectMode = ""; cursorShape = Qt.ArrowCursor }

    function updateSeries(pts) {
        if (paused || pts.length === 0) return
        mainSeries.clear()
        for (var i = 0; i < pts.length; i++) {
            mainSeries.append(pts[i][0], pts[i][1])
        }
        _applyScroll(pts)
        _updateLimitLines()
    }

    function _applyScroll(pts) {
        if (!autoScroll || pts.length === 0) return
        var secs = windowSecs[windowKey] || 0
        var xMax = pts[pts.length-1][0]
        var xMin = secs > 0 ? Math.max(pts[0][0], xMax - secs) : pts[0][0]
        if (xMax - xMin < 1) xMax = xMin + 1
        xAxis.min = xMin; xAxis.max = xMax

        // Y range for visible window
        var yLo = 1e18, yHi = -1e18
        for (var i = 0; i < pts.length; i++) {
            if (pts[i][0] >= xMin) {
                if (pts[i][1] < yLo) yLo = pts[i][1]
                if (pts[i][1] > yHi) yHi = pts[i][1]
            }
        }
        if (yHi > yLo) {
            var margin = Math.max((yHi-yLo)*0.1, 0.5)
            yAxis.min = yLo - margin; yAxis.max = yHi + margin
        }
    }

    function _updateLimitLines() {
        var lines = bridge.getLimitLines()
        limitRepeater.model = lines
    }

    ChartView {
        id: chart
        anchors.fill: parent
        antialiasing: true
        legend.visible: true
        legend.alignment: Qt.AlignTop
        backgroundColor: root.chartBg
        plotAreaColor: root.chartPlot

        // Style axes
        ValueAxis {
            id: xAxis
            min: 0; max: 60
            gridVisible: true
            gridLineColor: root.chartGrid
            labelsColor: root.chartLbl
            labelsFont.pixelSize: 11
            titleText: "Time (s)"
            titleFont.pixelSize: 11
            color: root.chartAxis
        }
        ValueAxis {
            id: yAxis
            min: 0; max: 1000
            gridVisible: true
            gridLineColor: root.chartGrid
            labelsColor: root.chartLbl
            labelsFont.pixelSize: 11
            titleText: "Height (mm)"
            titleFont.pixelSize: 11
            color: root.chartAxis
        }

        // Main sensor series
        LineSeries {
            id: mainSeries
            name: "Sensor"
            axisX: xAxis; axisY: yAxis
            color: root.accentCol; width: 2
            pointsVisible: false
        }

        // Limit line series (one per active limit)
        Repeater {
            id: limitRepeater
            model: []
            LineSeries {
                name: modelData.label
                axisX: xAxis; axisY: yAxis
                color: modelData.color; width: 1
                style: Qt.DashLine
                Component.onCompleted: {
                    var xMin = xAxis.min, xMax = xAxis.max
                    append(xMin, modelData.value)
                    append(xMax, modelData.value)
                }
            }
        }

        // Hover crosshair
        MouseArea {
            id: chartMouse
            anchors.fill: parent
            hoverEnabled: true

            onPositionChanged: function(mouse) {
                var pt = chart.mapToValue(Qt.point(mouse.x, mouse.y), mainSeries)
                crosshairX.value = pt.x
                crosshairY.value = pt.y
                hoverLabel.text = "t=" + pt.x.toFixed(1) + "s  y=" + pt.y.toFixed(1)
                hoverGroup.visible = true
            }
            onExited: hoverGroup.visible = false

            onClicked: function(mouse) {
                if (chartRoot.manualSelectMode === "") return
                var pt = chart.mapToValue(Qt.point(mouse.x, mouse.y), mainSeries)
                // Average ±0.5s around click
                var sum = 0, cnt = 0
                for (var i = 0; i < mainSeries.count; i++) {
                    var p = mainSeries.at(i)
                    if (Math.abs(p.x - pt.x) <= 0.5) { sum += p.y; cnt++ }
                }
                var val = cnt > 0 ? sum/cnt : pt.y
                if (chartRoot.manualSelectMode === "MWL") {
                    var cwlVal = val  // fallback
                    for (var j = 0; j < mainSeries.count; j++) {
                        if (mainSeries.at(j).x >= pt.x + 2.0) { cwlVal = mainSeries.at(j).y; break }
                    }
                    bridge.applyManualMwlCwl(val, cwlVal)
                    chartRoot.deltaText("MWL=" + val.toFixed(1) + " CWL=" + cwlVal.toFixed(1))
                }
                chartRoot.exitManualSelect()
            }
        }
    }

    // Crosshair + tooltip
    Item {
        id: hoverGroup; visible: false; anchors.fill: parent

        Rectangle {
            id: crosshairVLine
            x: chart.plotArea.x + (crosshairX.value - xAxis.min) / (xAxis.max - xAxis.min) * chart.plotArea.width
            y: chart.plotArea.y; width: 1; height: chart.plotArea.height
            color: root.textSec; opacity: 0.4
            property real value: 0; onValueChanged: x = chart.plotArea.x + (value - xAxis.min) / Math.max(1, xAxis.max - xAxis.min) * chart.plotArea.width
        }

        Rectangle {
            id: hoverBubble
            x: Math.min(crosshairVLine.x + 8, chartRoot.width - width - 4)
            y: chart.plotArea.y + 8
            width: hoverLabel.implicitWidth + 16; height: 26; radius: 8
            color: root.bgInput; border.color: root.accentCol; border.width: 1

            Text {
                id: hoverLabel
                anchors.centerIn: parent
                color: root.textPri; font.pixelSize: 11; font.family: "monospace"
            }
        }
    }

    // Hidden properties for crosshair calculation
    QtObject { id: crosshairX; property real value: 0 }
    QtObject { id: crosshairY; property real value: 0 }
}
"""
