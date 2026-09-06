import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI
import ClassWidgets.Plugins
import "components"

/*!
    天气插件设置页。

    版式沿用 Class Widgets 其它设置页：FluentPage + 分组标题（BodyStrong）
    + SettingCard 行。

    控件一律「读配置时用函数写值、改动时调后端槽」，不要用属性绑定做双向——
    绑定会在配置回流时把用户的选择顶掉。
*/
PluginPage {
    id: page
    pluginId: "com.helloswx.weather"
    title: qsTr("天气")

    property var info: ({})
    property bool currentProviderRequiresKey: false
    property bool syncingControls: false  // 防止程序化回写 currentIndex 时触发 setProvider

    // RinUI 的下拉菜单取文本用的是 model[textRole]，必须是带 role 的 ListModel，
    // 传普通数组或对象数组会得到一整列空白项。
    ListModel { id: providerModel }


    Component.onCompleted: {
        Qt.callLater(reload)
    }

    onBackendChanged: {
        if (backend) {
            Qt.callLater(reload)
        }
    }

    function reload() {
        if (!backend) {
            console.log("SettingsPage.reload(): backend is null")
            return
        }
        
        var newInfo = backend.settings()
        if (newInfo) {
            info = newInfo
        }

        if (providerModel.count === 0) {
            var list = backend.providers()
            console.log("SettingsPage.reload(): providers() returned", list.length, "items")
            for (var i = 0; i < list.length; i++) {
                providerModel.append({
                    "text": list[i].name,
                    "pid": list[i].id,
                    "requiresKey": list[i].requiresKey,
                    "supportsCoordinates": list[i].supportsCoordinates,
                    "onlineSearch": list[i].onlineSearch
                })
            }
            console.log("SettingsPage.reload(): providerModel.count =", providerModel.count)
        }
        syncControls()
    }

    /*! 把配置值写回控件 */
    function syncControls() {
        page.syncingControls = true
        for (var i = 0; i < providerModel.count; i++) {
            if (providerModel.get(i).pid === info.provider) {
                providerBox.currentIndex = i
                break
            }
        }
        page.syncingControls = false
        page.currentProviderRequiresKey = !!(info && info.requiresKey)
        if (!apiKeyField.activeFocus) {
            apiKeyField.text = info.apiKey || ""
        }
        refreshBox.value = info.refreshMinutes || 5
    }

    readonly property string locationSummary: {
        if (!info) return qsTr("尚未选择城市")
        if (info.locating) return qsTr("正在定位…")
        if (!info.cityName) return qsTr("尚未选择城市")
        if (info.locationMode === "coordinates" && info.latitude !== null && info.latitude !== undefined) {
            return qsTr("%1（%2, %3）").arg(info.cityName)
                                       .arg(Number(info.latitude).toFixed(2))
                                       .arg(Number(info.longitude).toFixed(2))
        }
        return info.cityName
    }

    Connections {
        target: backend
        function onConfigChanged() { page.reload() }
        function onDataChanged() {
            if (backend) {
                var newInfo = backend.settings()
                if (newInfo) page.info = newInfo
                page.syncControls()
            }
        }
        function onLocatingChanged() {
            if (backend) {
                var newInfo = backend.settings()
                if (newInfo) page.info = newInfo
                page.syncControls()
            }
        }
    }

    // ------------------------------------------------------------------ 状态
    InfoBar {
        Layout.fillWidth: true
        objectName: "statusBar"
        severity: (page.info && page.info.located) ? Severity.Warning : Severity.Info
        visible: !(page.info && page.info.located) || !!(page.info && page.info.error)
        title: (page.info && page.info.located) ? qsTr("暂时取不到天气") : qsTr("尚未选择城市")
        text: (page.info && page.info.located)
              ? (page.info.error || "")
              : qsTr("添加一个城市后即可显示天气。")
    }

    // ------------------------------------------------------------------ 数据源
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4

        Text {
            typography: Typography.BodyStrong
            text: qsTr("数据源")
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_cloud_20_regular"
            title: qsTr("天气数据源")
            description: qsTr("切换时会自动匹配已添加城市")

            ComboBox {
                id: providerBox
                objectName: "providerBox"
                Layout.preferredWidth: 200
                model: providerModel
                textRole: "text"
                onCurrentIndexChanged: {
                    if (page.syncingControls) return
                    var chosen = providerModel.get(currentIndex)
                    if (chosen && backend) backend.setProvider(chosen.pid)
                }
            }
        }

        SettingCard {
            id: apiKeyCard
            objectName: "apiKeyCard"
            Layout.fillWidth: true
            visible: page.currentProviderRequiresKey
            icon.name: "ic_fluent_key_20_regular"
            title: qsTr("API Key")
            description: qsTr("该数据源需要自行申请密钥，填写后按回车保存")

            TextField {
                id: apiKeyField
                objectName: "apiKeyField"
                Layout.preferredWidth: 240
                echoMode: TextInput.Password
                placeholderText: qsTr("填写 API Key")
                onEditingFinished: if (backend && page.info) backend.setApiKey(page.info.provider, text)
            }
        }
    }

    // ------------------------------------------------------------------ 城市
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 12

        RowLayout {
            id: cityCard
            objectName: "cityCard"
            Layout.fillWidth: true

            Text {
                typography: Typography.BodyStrong
                text: qsTr("城市")
            }

            Item { Layout.fillWidth: true }

            ToolButton {
                objectName: "locateButton"
                icon.name: "ic_fluent_location_ripple_20_regular"
                enabled: page.info && !page.info.locating
                onClicked: backend.locateAutomatically()

                ToolTip.visible: hovered
                ToolTip.text: qsTr("添加当前位置")
            }

            ToolButton {
                objectName: "chooseButton"
                icon.name: "ic_fluent_add_20_regular"
                onClicked: {
                    cityDialog.info = page.info
                    cityDialog.open()
                }

                ToolTip.visible: hovered
                ToolTip.text: qsTr("添加城市")
            }
        }

        Grid {
            id: cityGrid
            Layout.fillWidth: true
            rowSpacing: 12
            columnSpacing: 12
            columns: Math.max(1, Math.floor(width / 222))

            Repeater {
                model: page.info && page.info.cities ? page.info.cities : []

                delegate: CityClip {
                    required property var modelData
                    width: (cityGrid.width - cityGrid.columnSpacing * (cityGrid.columns - 1)) / cityGrid.columns
                    city: modelData
                    onMakeDefaultRequested: function(cityId) { backend.setDefaultCity(cityId) }
                    onRemoveRequested: function(cityId) { backend.removeCity(cityId) }
                }
            }
        }
    }

    // ------------------------------------------------------------------ 更新
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4

        Text {
            typography: Typography.BodyStrong
            text: qsTr("更新")
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_timer_20_regular"
            title: qsTr("自动刷新间隔")
            description: qsTr("单位为分钟，最短 5 分钟")

            SpinBox {
                id: refreshBox
                objectName: "refreshBox"
                Layout.preferredWidth: 160
                from: 5
                to: 720
                stepSize: 5
                onValueModified: if (backend) backend.setRefreshMinutes(value)
            }
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_arrow_sync_20_regular"
            title: qsTr("立即刷新")
            description: qsTr("重新获取一次天气数据")

            Button {
                objectName: "refreshButton"
                text: qsTr("刷新")
                onClicked: backend.refresh()
            }
        }
    }

    CityDialog {
        id: cityDialog
        objectName: "cityDialog"
        backend: page.backend
        info: page.info
    }
}
