import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI
import ClassWidgets.Plugins

SettingsLayout {
    id: layout

    // WidgetSettings is loaded inside the widgets window, whose context does
    // not expose PluginBackendBridge. Read the persisted plugin model through
    // the standard CW2 Configs context instead.
    property var backend: null
    ListModel { id: cityModel }

    Component.onCompleted: reloadCities()

    function reloadCities() {
        cityModel.clear()
        cityModel.append({"text": qsTr("跟随默认城市"), "value": ""})
        let cities = []
        if (backend && backend.cityOptions) {
            cities = backend.cityOptions()
        } else if (typeof Configs !== "undefined" && Configs.data) {
            const rootConfig = Configs.data
            const pluginConfigs = rootConfig.plugins && rootConfig.plugins.configs
            const pluginConfig = pluginConfigs && pluginConfigs["com.helloswx.weather"]
            cities = (pluginConfig && pluginConfig.cities) || []
        }
        for (let i = 0; i < (cities || []).length; i++) {
            const city = cities[i]
            cityModel.append({"text": city.name || qsTr("未命名城市"), "value": String(city.id)})
        }
        const wanted = settings && settings.city_id ? settings.city_id : ""
        for (let i = 0; i < cityModel.count; i++) {
            if (cityModel.get(i).value === String(wanted)) {
                citySelector.currentIndex = i
                return
            }
        }
        citySelector.currentIndex = 0
    }

    Connections {
        target: typeof Configs !== "undefined" ? Configs : null
        function onConfigChanged() { layout.reloadCities() }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_location_20_regular"
        title: qsTr("显示城市")
        description: qsTr("选择该小组件显示的城市，或跟随默认城市")

        ComboBox {
            id: citySelector
            Layout.preferredWidth: 220
            model: cityModel
            textRole: "text"
            onActivated: settings.city_id = cityModel.get(currentIndex).value
        }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_temperature_20_regular"
        title: qsTr("主体显示")
        description: qsTr("主体屏幕展示实时温度，或当日最高 / 最低温")

        ComboBox {
            id: contentModeBox
            Layout.preferredWidth: 180
            property var values: ["current", "high_low"]
            model: ListModel {
                ListElement { text: qsTr("实时温度") }
                ListElement { text: qsTr("最高 / 最低温") }
            }
            onActivated: settings.content_mode = values[currentIndex]
            Component.onCompleted: currentIndex = Math.max(0, values.indexOf(settings.content_mode || "current"))
        }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_clock_20_regular"
        title: qsTr("加入「未来 3 小时」屏幕")
        description: qsTr("部分数据源没有逐小时预报，此时该屏幕不会出现")

        Switch {
            id: hourlySwitch
            onToggled: settings.show_hourly = checked
            Component.onCompleted: checked = settings.show_hourly === true
        }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_slide_multiple_20_regular"
        title: qsTr("轮播降水预报与天气预警")
        description: qsTr("关闭后只保留主体屏幕，降水与预警仅显示为右上角摘要")

        Switch {
            id: carouselSwitch
            onToggled: settings.carousel = checked
            Component.onCompleted: checked = settings.carousel !== false
        }
    }

    SettingCard {
        Layout.fillWidth: true
        visible: carouselSwitch.checked
        icon.name: "ic_fluent_timer_20_regular"
        title: qsTr("主体屏幕停留时长")
        description: qsTr("优先级最高，停留时间应长于次级屏幕（秒）")

        SpinBox {
            Layout.preferredWidth: 150
            from: 4
            to: 120
            stepSize: 1
            onValueModified: settings.main_seconds = value
            Component.onCompleted: value = settings.main_seconds || 12
        }
    }

    SettingCard {
        Layout.fillWidth: true
        visible: carouselSwitch.checked
        icon.name: "ic_fluent_warning_20_regular"
        title: qsTr("降水 / 预警屏幕停留时长")
        description: qsTr("次级屏幕的停留时间（秒）")

        SpinBox {
            Layout.preferredWidth: 150
            from: 2
            to: 60
            stepSize: 1
            onValueModified: settings.detail_seconds = value
            Component.onCompleted: value = settings.detail_seconds || 6
        }
    }
}
